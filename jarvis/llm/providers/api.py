"""OpenAI-compatible API LLM provider."""

from typing import Any, Dict, List, Optional

from ...core.logger import get_logger
from ..base import BaseLLMProvider

logger = get_logger(__name__)


class APIProvider(BaseLLMProvider):
    """Provider for OpenAI-compatible API endpoints.

    Works with OpenAI, Claude (via compatibility layer), OpenRouter,
    and any server that exposes ``/v1/chat/completions`` — including
    key-less local servers such as LM Studio, where ``api_key`` may be
    omitted entirely.
    """

    def __init__(
        self,
        model: str,
        api_url: str = "",
        api_key: str = "",
        headers: Optional[Dict[str, str]] = None,
    ):
        if not api_url:
            raise ValueError("api_url is required for the API provider")

        super().__init__(model)
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        if headers:
            self.headers.update(headers)

        self._httpx: Any = None

    def _ensure_client(self) -> None:
        """Import httpx on first use."""
        if self._httpx is not None:
            return
        try:
            import httpx as _httpx

            self._httpx = _httpx
            logger.debug(f"httpx initialized for {self.api_url}")
        except ImportError:
            raise ImportError(
                "httpx package not installed. Install with: pip install httpx"
            )

    # -- BaseLLMProvider interface -------------------------------------------

    def chat(self, messages: List[Dict[str, str]]) -> str:
        self._ensure_client()

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }

        try:
            with self._httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{self.api_url}/v1/chat/completions",
                    headers=self.headers,
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()

                if "choices" in result and len(result["choices"]) > 0:
                    usage = result.get("usage", {})
                    self.last_prompt_tokens = usage.get("prompt_tokens", 0) or 0
                    self.last_completion_tokens = usage.get("completion_tokens", 0) or 0
                    return result["choices"][0]["message"]["content"]
                raise ValueError(f"Unexpected API response format: {result}")

        except self._httpx.HTTPError as e:
            logger.error(f"API HTTP error: {e}")
            raise
        except Exception as e:
            logger.error(f"API chat error: {e}")
            raise

    def is_available(self) -> bool:
        self._ensure_client()
        try:
            with self._httpx.Client(timeout=5.0) as client:
                try:
                    response = client.get(
                        f"{self.api_url}/v1/models",
                        headers=self.headers,
                    )
                    if response.status_code == 200:
                        return True
                except self._httpx.HTTPError:
                    pass
                return True
        except Exception as e:
            logger.debug(f"API availability check failed: {e}")
            return True

    def embed(self, texts: List[str]) -> List[List[float]]:
        self._ensure_client()

        payload = {"model": self.model, "input": texts}

        try:
            with self._httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{self.api_url}/v1/embeddings",
                    headers=self.headers,
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()

                data = result.get("data")
                if not data:
                    raise ValueError(f"Unexpected embeddings response format: {result}")
                return [
                    item["embedding"]
                    for item in sorted(data, key=lambda d: d.get("index", 0))
                ]

        except self._httpx.HTTPError as e:
            logger.error(f"API embeddings HTTP error: {e}")
            raise
        except Exception as e:
            logger.error(f"API embeddings error: {e}")
            raise
