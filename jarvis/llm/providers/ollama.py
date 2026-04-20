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
    ):
        super().__init__(model)
        self.base_url = base_url or "http://localhost:11434"
        self.auto_pull = auto_pull
        self.temperature = temperature
        self.strict_json = strict_json

        import os

        if base_url and base_url != "http://localhost:11434":
            os.environ["OLLAMA_HOST"] = base_url
        if api_key:
            os.environ["OLLAMA_API_KEY"] = api_key

        try:
            from ollama import Client

            self._client = Client(host=self.base_url, timeout=LLM_TIMEOUT)
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

        try:
            response = self._client.chat(**kwargs)
            return response["message"]["content"]
        except Exception as e:
            error_str = str(e).lower()
            if "model" in error_str and (
                "not found" in error_str or "does not exist" in error_str
            ):
                logger.warning(
                    "Model not found during chat, attempting to ensure model is available..."
                )
                if self.ensure_model():
                    try:
                        response = self._client.chat(**kwargs)
                        return response["message"]["content"]
                    except Exception as retry_error:
                        logger.error(
                            f"Ollama chat error after model pull: {retry_error}"
                        )
                        raise
                else:
                    raise RuntimeError(
                        f"Model '{self.model}' is not available and could not be pulled"
                    )
            logger.error(f"Ollama chat error: {e}")
            raise

    def is_available(self) -> bool:
        try:
            self._ollama.list()
            return True
        except Exception as e:
            logger.debug(f"Ollama not available: {e}")
            return False

    # -- Ollama-specific -----------------------------------------------------

    def ensure_model(self) -> bool:
        """Ensure the model exists locally, pulling it if necessary."""
        if self._model_exists():
            logger.debug(f"Model '{self.model}' is already available")
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
            models_response = self._ollama.list()
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
