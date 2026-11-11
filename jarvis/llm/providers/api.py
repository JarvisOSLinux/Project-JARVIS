"""OpenAI-compatible API LLM provider."""

import json
from typing import Any, Dict, Iterator, List, Optional

from ...core.logger import get_logger
from ..base import BaseLLMProvider
from ..images import detect_image_format, encode_image_base64

logger = get_logger(__name__)


def _convert_image_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rewrite messages carrying image paths into OpenAI content-parts.

    Images become ``image_url`` parts with base64 data URLs. Messages without
    images pass through untouched — including the list itself when nothing
    needs rewriting — so text-only payloads stay byte-identical.
    """
    if not any(m.get("images") for m in messages):
        return messages
    converted = []
    for m in messages:
        images = m.get("images")
        if not images:
            converted.append(m)
            continue
        parts: List[Dict[str, Any]] = [{"type": "text", "text": m.get("content", "")}]
        for path in images:
            fmt = detect_image_format(path)
            data = encode_image_base64(path)
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{fmt};base64,{data}"},
                }
            )
        rewritten = {k: v for k, v in m.items() if k != "images"}
        rewritten["content"] = parts
        converted.append(rewritten)
    return converted


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
        temperature: Optional[float] = None,
    ):
        if not api_url:
            raise ValueError("api_url is required for the API provider")

        super().__init__(model)
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature

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
            "messages": _convert_image_messages(messages),
            "stream": False,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature

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

    def stream_chat(self, messages: List[Dict[str, str]]) -> Iterator[str]:
        """Yield response text incrementally via SSE (`stream: true`)."""
        self._ensure_client()

        payload = {
            "model": self.model,
            "messages": _convert_image_messages(messages),
            "stream": True,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        try:
            with self._httpx.Client(timeout=60.0) as client:
                with client.stream(
                    "POST",
                    f"{self.api_url}/v1/chat/completions",
                    headers=self.headers,
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data = line[len("data: ") :].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        choices = chunk.get("choices") or []
                        if choices:
                            content = choices[0].get("delta", {}).get("content")
                            if content:
                                yield content
                        usage = chunk.get("usage")
                        if usage:
                            self.last_prompt_tokens = usage.get("prompt_tokens", 0) or 0
                            self.last_completion_tokens = (
                                usage.get("completion_tokens", 0) or 0
                            )

        except self._httpx.HTTPError as e:
            logger.error(f"API stream chat HTTP error: {e}")
            raise
        except Exception as e:
            logger.error(f"API stream chat error: {e}")
            raise

    def is_available(self) -> bool:
        # A missing httpx makes this provider unusable, full stop — report that
        # rather than raising out of a probe callers use to decide fallback.
        # Reachability errors below are deliberately optimistic instead (not
        # every OpenAI-compatible endpoint serves /v1/models), so the two cases
        # must not share an answer.
        try:
            self._ensure_client()
        except ImportError as e:
            logger.debug(f"API provider unusable: {e}")
            return False

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
