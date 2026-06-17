"""Ollama LLM provider."""

import sys
from typing import Dict, List, Optional

from ...core.logger import get_logger
from ..base import BaseLLMProvider

logger = get_logger(__name__)

LLM_TIMEOUT = 60  # seconds — prevents indefinite hangs on cloud API


class OllamaProvider(BaseLLMProvider):
    """Provider for local Ollama instances."""

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        auto_pull: bool = False,
        temperature: Optional[float] = None,
        strict_json: bool = False,
        think: Optional[bool] = None,
    ):
        super().__init__(model)
        self.base_url = base_url or "http://localhost:11434"
        self.auto_pull = auto_pull
        self.temperature = temperature
        self.strict_json = strict_json
        self.think = think
        self._api_key = api_key

        import os

        if base_url and base_url != "http://localhost:11434":
            os.environ["OLLAMA_HOST"] = base_url

        try:
            from ollama import Client

            client_kwargs = {"timeout": LLM_TIMEOUT}
            if api_key:
                client_kwargs["headers"] = {"Authorization": f"Bearer {api_key}"}
            self._client = Client(host=self.base_url, **client_kwargs)
            import ollama as _ollama

            self._ollama = _ollama
        except ImportError:
            raise ImportError(
                "ollama package not installed. Install with: pip install ollama"
            )

    # -- BaseLLMProvider interface -------------------------------------------

    def chat(self, messages: List[Dict[str, str]]) -> str:
        options = {}
        if self.temperature is not None:
            options["temperature"] = self.temperature

        kwargs: Dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "options": options or None,
        }
        if self.strict_json:
            # Grammar-constrained JSON. Conflicts with thinking-mode models —
            # leave off unless the user explicitly opts in.
            kwargs["format"] = "json"
        if self.think is not None:
            # think=True keeps the reasoning chain in the separate `thinking`
            # field rather than letting it leak into `content`. Silently ignored
            # by models that don't support the option.
            kwargs["think"] = self.think

        log_kwargs = {k: v for k, v in kwargs.items() if k != "messages"}
        log_kwargs["num_messages"] = len(kwargs.get("messages", []))
        logger.debug(f"Ollama chat request: {log_kwargs}")

        try:
            response = self._client.chat(**kwargs)
            text = self._extract_content(response)
            self.last_prompt_tokens = getattr(response, "prompt_eval_count", 0) or 0
            self.last_completion_tokens = getattr(response, "eval_count", 0) or 0
            return text
        except Exception as e:
            error_str = str(e).lower()
            if "model" in error_str and (
                "not found" in error_str or "does not exist" in error_str
            ):
                if self.auto_pull and not self._is_remote:
                    logger.warning("Model not found, auto-pulling...")
                    if self._pull_model():
                        try:
                            response = self._client.chat(**kwargs)
                            text = self._extract_content(response)
                            self.last_prompt_tokens = (
                                getattr(response, "prompt_eval_count", 0) or 0
                            )
                            self.last_completion_tokens = (
                                getattr(response, "eval_count", 0) or 0
                            )
                            return text
                        except Exception as retry_error:
                            logger.error(
                                f"Ollama chat error after model pull: {retry_error}"
                            )
                            raise
                raise RuntimeError(f"model '{self.model}' not found (status code: 404)")
            logger.error(f"Ollama chat error: {e}")
            raise

    @staticmethod
    def _extract_content(response) -> str:
        """Return the text content from an Ollama chat response.

        Thinking/reasoning models (e.g. qwq, deepseek-r1) place their
        reasoning in a separate ``thinking`` field and may leave ``content``
        empty for turns that require deep reasoning. Fall back to ``thinking``
        so those models still return parseable output.

        Some models leak their reasoning chain into ``content`` using special
        chat-template tokens (e.g. ``<|start|>assistant<|channel|>analysis``).
        Strip those tokens so downstream JSON parsers see clean output.
        """
        import re

        msg = response["message"]
        content = msg.get("content") or ""
        thinking = msg.get("thinking") or ""

        if not content and not thinking:
            eval_count = None
            if hasattr(response, "get"):
                eval_count = response.get("eval_count")
            if eval_count and eval_count > 0:
                logger.warning(
                    f"Ollama: model generated {eval_count} tokens but returned "
                    "empty content — possible cloud proxy issue"
                )

        if content:
            cleaned = re.sub(r"<\|[^|>]+\|>", "", content).strip()
            if cleaned != content:
                logger.debug(
                    f"Ollama: stripped special tokens from content "
                    f"({len(content)} → {len(cleaned)} chars)"
                )
            if cleaned:
                return cleaned
            # Stripping removed everything — fall through to thinking/raw
            logger.warning(
                "Ollama: content was non-empty but stripping removed all text, "
                "falling through to thinking field / raw content"
            )
        if thinking:
            logger.debug(
                "Ollama: content empty, falling back to thinking field "
                f"({len(thinking)} chars)"
            )
            return thinking
        if content:
            logger.warning(
                "Ollama: returning raw content (before stripping) as last resort "
                f"({len(content)} chars)"
            )
            return content
        return ""

    @property
    def _is_remote(self) -> bool:
        return self.base_url != "http://localhost:11434"

    def is_available(self) -> bool:
        try:
            self._client.list()
            return True
        except Exception as e:
            logger.debug(f"Ollama not available: {e}")
            return False

    # -- Ollama-specific -----------------------------------------------------

    def ensure_model(self) -> bool:
        """Ensure the model exists, pulling it if necessary."""
        if self._model_exists():
            logger.debug(f"Model '{self.model}' is already available")
            return True

        if self._is_remote:
            logger.info(
                f"Model '{self.model}' not in remote list at {self.base_url} "
                "— proceeding anyway (cloud models may not appear in list)"
            )
            return True

        logger.warning(f"Model '{self.model}' is not installed locally")

        if self.auto_pull:
            logger.info("Auto-pull enabled, pulling model automatically...")
            return self._pull_model()

        if sys.stdin.isatty():
            response = (
                input(
                    f"\nModel '{self.model}' is not installed. "
                    "Would you like to pull it now? (y/n): "
                )
                .strip()
                .lower()
            )
            if response in ("y", "yes"):
                return self._pull_model()
            logger.error("Model not available and user declined to pull it")
            return False

        logger.error(
            f"Model '{self.model}' is not installed and auto-pull is disabled. "
            "Set LLM_AUTO_PULL=true in your .env file"
        )
        return False

    def _model_exists(self) -> bool:
        try:
            models_response = self._client.list()
            if hasattr(models_response, "models"):
                models = models_response.models
                model_names = [m.model for m in models if hasattr(m, "model")]
            else:
                models = models_response.get("models", [])
                model_names = [m.get("name", "") for m in models if m.get("name")]

            logger.debug(
                f"Looking for model '{self.model}' in available models: {model_names}"
            )

            if self.model in model_names:
                return True

            model_base = self.model.split(":")[0]
            for name in model_names:
                if not name:
                    continue
                name_base = name.split(":")[0]
                if name_base == model_base:
                    return True
                if name.startswith(self.model):
                    return True
                if self.model.startswith(name_base):
                    return True

            return False
        except Exception as e:
            logger.debug(f"Error checking for model: {e}", exc_info=True)
            return False

    def _pull_model(self) -> bool:
        try:
            logger.info(f"Pulling model '{self.model}' from Ollama...")
            logger.info("This may take a while depending on model size...")

            for chunk in self._ollama.pull(self.model, stream=True):
                if "status" in chunk:
                    if (
                        "downloading" in chunk["status"].lower()
                        or "pulling" in chunk["status"].lower()
                    ):
                        completed = chunk.get("completed") or 0
                        total = chunk.get("total") or 0
                        progress = (
                            (completed / total * 100) if total and total > 0 else 0
                        )
                        print(
                            f"\rPulling {self.model}: {chunk['status']} ({progress:.1f}%)",
                            end="",
                            flush=True,
                        )

            print()
            logger.info(f"Successfully pulled model '{self.model}'")
            return True
        except Exception as e:
            logger.error(f"Failed to pull model '{self.model}': {e}")
            return False
