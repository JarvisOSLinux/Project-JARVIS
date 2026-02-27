"""
LLM chat interface for JARVIS.

Manages conversation history and JSON response parsing,
delegating actual inference to a :class:`BaseLLMProvider`.
"""

import json
from ..core.logger import get_logger
from .base import BaseLLMProvider

logger = get_logger(__name__)


class LLM:
    """Main LLM interface that works with any provider."""

    def __init__(
        self,
        provider: BaseLLMProvider,
        system_prompt: str,
        wrong_json_message: str = "",
    ):
        """
        Args:
            provider: A ready-to-use LLM provider instance.
            system_prompt: The system prompt (already formatted with OS info etc.).
            wrong_json_message: Message sent to the LLM when it returns invalid JSON.
        """
        self.provider = provider
        self._wrong_json_message = wrong_json_message

        logger.info(f"Using LLM provider: {self.provider.model}")

        self.default_chat = [
            {'role': 'system', 'content': system_prompt},
        ]
        self.chat_history = list.copy(self.default_chat)

        logger.info("LLM: Initiating Preload...")
        try:
            self.provider.chat(self.chat_history)
            logger.info("LLM: Initiation Complete!")
        except Exception as e:
            logger.warning(f"LLM preload failed (this may be expected): {e}")
            logger.info("LLM: Continuing despite preload failure...")

    def ask(self, prompt):
        """Ask the LLM a question and return the parsed JSON response."""
        self.chat_history.append({
            'role': 'user',
            'content': prompt,
        })

        response_text = self.provider.chat(self.chat_history)
        logger.debug(f"LLM Responded:'\n{response_text}\n----------")

        try:
            return json.loads(response_text)
        except json.decoder.JSONDecodeError:
            logger.warning("LLM response was not valid JSON, retrying with error message...")
            return self.ask(self._wrong_json_message)

    def reset_history(self):
        """Reset chat history to the default system prompt."""
        self.chat_history = list.copy(self.default_chat)
