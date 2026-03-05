"""
LLM chat interface for JARVIS.

Supports mode switching (root / dispatch / contextor) with isolated
conversation histories per mode. Each mode has its own system prompt
and history — switching resets the target mode's history to its system
prompt so subsystems always start fresh.
"""

import json
from ..core.logger import get_logger
from .base import BaseLLMProvider

logger = get_logger(__name__)


class LLM:
    """Main LLM interface with hierarchical mode support."""

    MAX_JSON_RETRIES = 3

    def __init__(
        self,
        provider: BaseLLMProvider,
        prompts: dict[str, str],
        wrong_json_message: str = "",
    ):
        """
        Args:
            provider: A ready-to-use LLM provider instance.
            prompts: Mapping of mode name → formatted system prompt.
                     Must contain at least "root".
            wrong_json_message: Message sent to the LLM when it returns invalid JSON.
        """
        self.provider = provider
        self._wrong_json_message = wrong_json_message
        self._prompts = prompts
        self._mode = "root"

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
        Resets the target mode's history to its system prompt — subsystems
        always start with a clean slate.
        """
        if mode not in self._prompts:
            logger.warning(f"LLM: Unknown mode '{mode}', staying in '{self._mode}'")
            return

        logger.info(f"LLM: Switching mode {self._mode} → {mode}")

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
            return json.loads(response_text)
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
        """Reset current mode's history to its system prompt."""
        self._histories[self._mode] = [
            {"role": "system", "content": self._prompts[self._mode]},
        ]
        self.chat_history = self._histories[self._mode]
