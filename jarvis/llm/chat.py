"""
LLM chat interface for JARVIS.

Supports mode switching (root / dispatch / contextor) with isolated
conversation histories per mode. Each mode has its own system prompt
and history — switching resets the target mode's history to its system
prompt so subsystems always start fresh.

ROOT mode keeps a sliding window of recent exchanges for conversational
continuity. Subsystem modes (dispatch, contextor) always start clean.
"""

import json
import re
from ..core.logger import get_logger
from .base import BaseLLMProvider

logger = get_logger(__name__)

ROOT_HISTORY_WINDOW = 3


class LLM:
    """Main LLM interface with hierarchical mode support."""

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
            prompts: Mapping of mode name → formatted system prompt.
                     Must contain at least "root".
            wrong_json_message: Message sent to the LLM when it returns invalid JSON.
            root_window: Number of recent user/assistant exchange pairs to
                         keep in ROOT mode for conversational continuity.
        """
        self.provider = provider
        self._wrong_json_message = wrong_json_message
        self._prompts = prompts
        self._mode = "root"
        self._root_window = root_window

        logger.info(f"Using LLM provider: {self.provider.model}")

        self._histories: dict[str, list[dict]] = {}
        for mode, prompt in prompts.items():
            self._histories[mode] = [
                {"role": "system", "content": prompt},
            ]

        self.chat_history = list(self._histories["root"])

        logger.info("LLM: Initiating Preload...")
        try:
            self.provider.chat(self.chat_history)
            logger.info("LLM: Initiation Complete!")
        except Exception as e:
            logger.warning(f"LLM preload failed (this may be expected): {e}")
            logger.info("LLM: Continuing despite preload failure...")

    @property
    def mode(self) -> str:
        return self._mode

    def switch_mode(self, mode: str):
        """
        Switch to a different prompt mode (root, dispatch, contextor).

        For root: preserves a sliding window of recent exchanges so the
        LLM maintains short-term conversational continuity.

        For subsystems: always starts with a clean slate (system prompt only).
        """
        if mode not in self._prompts:
            logger.warning(f"LLM: Unknown mode '{mode}', staying in '{self._mode}'")
            return

        if mode == self._mode:
            return

        logger.info(f"LLM: Switching mode {self._mode} → {mode}")

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
            'Valid actions for your current mode: "respond", "dispatch", "contextor".\n'
            'Example: {"action": "respond", "output": "your message", "goal_updates": []}'
        ),
        "dispatch": (
            'Valid actions: "search", "list_tools", "install", "dispatch", '
            '"wait", "kill", "defer", "done".\n'
            'Example: {"action": "done", "summary": "result summary"}'
        ),
        "contextor": (
            'Valid actions: "store", "recall", "search_memory", "list_memory", "done".\n'
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

        try:
            response_text = self.provider.chat(self.chat_history)
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
        self._histories[self._mode] = [
            {"role": "system", "content": self._prompts[self._mode]},
        ]
        self.chat_history = self._histories[self._mode]

    def _trim_root_history(self):
        """
        Trim ROOT history to the system prompt + last N exchange pairs.
        An exchange pair is a (user, assistant) message pair.
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
        trimmed = [history[0]]
        for pair in keep:
            trimmed.extend(pair)

        if len(trimmed) < len(history):
            dropped = (len(history) - len(trimmed)) // 2
            logger.debug(f"LLM: Trimmed root history — kept {len(keep)} exchanges, dropped {dropped}")

        self._histories["root"] = trimmed
