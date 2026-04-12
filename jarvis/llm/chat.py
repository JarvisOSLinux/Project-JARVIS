"""
LLM chat interface for JARVIS.

Supports mode switching (root / dispatch) with isolated conversation
histories per mode. Each mode has its own system prompt and history —
switching resets the target mode's history to its system prompt so
subsystems always start fresh.

ROOT mode uses a two-tier context management system:
- Tier 1 (Hot): Sliding window of recent exchanges — full fidelity
- Tier 2 (Warm): Rolling summary of evicted exchanges — compressed

RAG retrieval (Tier 3 / Cold) is handled in main.py via
``_build_root_context()`` which injects relevant memories from the
contextor subsystem into the ROOT context string.
"""

import json
import re
from typing import Optional, Any
from ..core.logger import get_logger
from .base import BaseLLMProvider
from .context_manager import ContextManager

logger = get_logger(__name__)

ROOT_HISTORY_WINDOW = 3


class LLM:
    """Main LLM interface with hierarchical mode support and tiered context."""

    MAX_JSON_RETRIES = 3

    def __init__(
        self,
        provider: BaseLLMProvider,
        prompts: dict[str, str],
        wrong_json_message: str = "",
        root_window: int = ROOT_HISTORY_WINDOW,
    ):
        """
        Args:
            provider: A ready-to-use LLM provider instance.
            prompts: Mapping of mode name -> formatted system prompt.
                     Must contain at least "root".
            wrong_json_message: Message sent to the LLM when it returns invalid JSON.
            root_window: Number of recent user/assistant exchange pairs to
                         keep in Tier 1 (hot) for conversational continuity.
        """
        self.provider = provider
        self._wrong_json_message = wrong_json_message
        self._prompts = prompts
        self._mode = "root"
        self._root_window = root_window

        logger.info(f"Using LLM provider: {self.provider.model}")

        # --- Tier 1: Per-mode conversation histories ---
        self._histories: dict[str, list[dict]] = {}
        for mode, prompt in prompts.items():
            self._histories[mode] = [
                {"role": "system", "content": prompt},
            ]

        self.chat_history = self._histories["root"]

        # --- Tiered context manager (ROOT mode only) ---
        self._context_manager = ContextManager(
            provider=provider,
            system_prompt=prompts.get("root", ""),
            hot_window=root_window,
        )

        logger.info("LLM: Initiating Preload...")
        try:
            # Preload with a copy — warms Ollama's KV cache for the system
            # prompt without affecting the live chat_history reference.
            self.provider.chat(list(self.chat_history))
            logger.info("LLM: Initiation Complete!")
        except Exception as e:
            logger.warning(f"LLM preload failed (this may be expected): {e}")
            logger.info("LLM: Continuing despite preload failure...")

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def context_manager(self) -> ContextManager:
        """Access the tiered context manager (for external inspection/control)."""
        return self._context_manager

    def set_prompt(self, mode: str, prompt: str) -> bool:
        """
        Update the system prompt for a given mode.

        Returns True if the prompt actually changed. When the target mode
        is the current mode, the history is rebuilt to apply the new
        prompt on the next turn.
        """
        current = self._prompts.get(mode)
        if current == prompt:
            return False

        self._prompts[mode] = prompt
        # Rebuild history for non-root modes so the new prompt takes effect
        # immediately. Root has its own window/summary machinery; changing
        # its prompt mid-session isn't a supported operation here.
        if mode != "root":
            self._histories[mode] = [
                {"role": "system", "content": prompt},
            ]
            if self._mode == mode:
                self.chat_history = self._histories[mode]
        return True

    def switch_mode(self, mode: str):
        """
        Switch to a different prompt mode (root, dispatch).

        For root: preserves a sliding window of recent exchanges with
        compression of evicted messages into a rolling summary (Tier 2).

        For subsystems: always starts with a clean slate (system prompt only).
        """
        if mode not in self._prompts:
            logger.warning(f"LLM: Unknown mode '{mode}', staying in '{self._mode}'")
            return

        if mode == self._mode:
            return

        logger.info(f"LLM: Switching mode {self._mode} -> {mode}")

        if mode == "root":
            self._trim_root_history()
        else:
            self._histories[mode] = [
                {"role": "system", "content": self._prompts[mode]},
            ]

        self._mode = mode
        self.chat_history = self._histories[mode]

    # Mode-specific retry hints so the LLM knows what actions are valid
    _MODE_RETRY_HINTS: dict[str, str] = {
        "root": (
            'Valid actions: "respond", "dispatch", "store", "recall", '
            '"search_memory", "list_memory".\n'
            'Example: {"action": "respond", "output": "your message", "goal_updates": []}'
        ),
        "dispatch": (
            'Valid actions: "search", "list_tools", "install", "dispatch", '
            '"wait", "kill", "defer", "done".\n'
            'Example: {"action": "done", "summary": "result summary"}'
        ),
    }

    def ask(self, prompt: str, _retries_left: int | None = None) -> dict:
        """Ask the LLM a question in the current mode and return parsed JSON."""
        if _retries_left is None:
            _retries_left = self.MAX_JSON_RETRIES

        self.chat_history.append({
            "role": "user",
            "content": prompt,
        })

        # For ROOT mode, use tiered context (augmented system prompt with
        # rolling summary). For subsystem modes, use plain history.
        if self._mode == "root":
            hot_exchanges = self._get_hot_exchanges()
            messages = self._context_manager.build_messages(
                hot_exchanges=hot_exchanges,
                new_input=None,  # Already appended to chat_history above
            )
        else:
            messages = self.chat_history

        try:
            response_text = self.provider.chat(messages)
        except Exception as e:
            logger.error(f"LLM [{self._mode}] provider error: {e}")
            response_text = ""

        logger.debug(f"LLM [{self._mode}] Responded:\n{response_text}\n----------")

        if not response_text or not response_text.strip():
            logger.warning(f"LLM [{self._mode}] returned empty response")
            self.chat_history.pop()
            if _retries_left > 0:
                return self.ask(prompt, _retries_left - 1)
            return self._fallback_response()

        parsed = self._extract_json(response_text)
        if parsed is not None:
            clean = json.dumps(parsed)
            self.chat_history.append({
                "role": "assistant",
                "content": clean,
            })
            return parsed

        if _retries_left > 0:
            preview = response_text[:200].replace("\n", "\\n")
            logger.warning(
                f"LLM [{self._mode}] response was not valid JSON, retrying "
                f"({_retries_left} left). Raw: {preview}"
            )
            hint = self._MODE_RETRY_HINTS.get(self._mode, "")
            retry_msg = f"{self._wrong_json_message}\n{hint}"
            return self.ask(retry_msg, _retries_left - 1)

        logger.error("LLM failed to return valid JSON after all retries")
        return self._fallback_response()

    def _get_hot_exchanges(self) -> list[dict]:
        """Extract non-system messages from current root history (Tier 1)."""
        history = self._histories.get("root", [])
        return [msg for msg in history if msg["role"] != "system"]

    def _fallback_response(self) -> dict:
        """Return a safe fallback when all retries are exhausted."""
        msg = "I had trouble formatting my response. Could you try again?"
        if self._mode != "root":
            return {"action": "done", "summary": msg}
        return {"action": "respond", "output": msg}

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """
        Try to extract a JSON object from LLM output that may be wrapped
        in markdown fences, thinking tags, or preamble text.
        """
        # 1. Direct parse
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

        # 2. Strip <think>...</think> or <reasoning>...</reasoning> tags
        cleaned = re.sub(
            r"<(?:think|thinking|reasoning)>.*?</(?:think|thinking|reasoning)>",
            "", text, flags=re.DOTALL,
        ).strip()
        if cleaned != text:
            try:
                obj = json.loads(cleaned)
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                pass

        # 3. Strip markdown code fences
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if fence_match:
            try:
                obj = json.loads(fence_match.group(1).strip())
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                pass

        # 4. Find the first { ... } block (greedy from first { to last })
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            candidate = text[first_brace:last_brace + 1]
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    def reset_history(self):
        """Reset current mode's history to system prompt only."""
        if self._mode == "root":
            # Soft reset: clear hot window but keep the rolling summary
            self._context_manager.soft_reset()

        self._histories[self._mode] = [
            {"role": "system", "content": self._prompts[self._mode]},
        ]
        self.chat_history = self._histories[self._mode]

    def full_reset(self):
        """Hard reset: clear all tiers including rolling summary."""
        self._context_manager.reset()
        self._histories[self._mode] = [
            {"role": "system", "content": self._prompts[self._mode]},
        ]
        self.chat_history = self._histories[self._mode]

    def _trim_root_history(self):
        """
        Trim ROOT history to the system prompt + last N exchange pairs.
        An exchange pair is a (user, assistant) message pair.

        Evicted exchanges are compressed into a rolling summary (Tier 2)
        instead of being silently dropped.
        """
        history = self._histories["root"]
        if len(history) <= 1:
            return

        non_system = history[1:]

        pairs: list[list[dict]] = []
        current_pair: list[dict] = []
        for msg in non_system:
            current_pair.append(msg)
            if msg["role"] == "assistant":
                pairs.append(current_pair)
                current_pair = []
        if current_pair:
            pairs.append(current_pair)

        keep = pairs[-self._root_window:] if len(pairs) > self._root_window else pairs

        # Identify evicted pairs for compression
        evicted_pairs = pairs[:-self._root_window] if len(pairs) > self._root_window else []

        if evicted_pairs:
            # Flatten evicted pairs into a message list for summarization
            evicted_messages = []
            for pair in evicted_pairs:
                evicted_messages.extend(pair)

            # Compress evicted messages into rolling summary (Tier 2)
            self._context_manager.compress_evicted(evicted_messages)

            dropped = len(evicted_pairs)
            logger.debug(
                f"LLM: Trimmed root history — kept {len(keep)} exchanges, "
                f"compressed {dropped} into rolling summary"
            )

        trimmed = [history[0]]
        for pair in keep:
            trimmed.extend(pair)

        self._histories["root"] = trimmed
