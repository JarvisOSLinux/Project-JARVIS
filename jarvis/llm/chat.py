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

    def ask(self, prompt: str, _retries_left: int | None = None) -> dict:
        """Ask the LLM a question in the current mode and return parsed JSON."""
        if _retries_left is None:
            _retries_left = self.MAX_JSON_RETRIES

        self.chat_history.append({
            "role": "user",
            "content": prompt,
        })

        response_text = self.provider.chat(self.chat_history)
        logger.debug(f"LLM [{self._mode}] Responded:\n{response_text}\n----------")

        try:
            parsed = json.loads(response_text)
            self.chat_history.append({
                "role": "assistant",
                "content": response_text,
            })
            return parsed
        except json.JSONDecodeError:
            if _retries_left > 0:
                logger.warning(
                    f"LLM response was not valid JSON, retrying "
                    f"({_retries_left} attempts left)..."
                )
                return self.ask(self._wrong_json_message, _retries_left - 1)
            logger.error("LLM failed to return valid JSON after all retries")
            return {
                "action": "respond",
                "output": "I had trouble formatting my response. Could you try again?",
            }

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
