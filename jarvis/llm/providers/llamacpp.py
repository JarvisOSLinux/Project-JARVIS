"""Local llama.cpp provider: JARVIS runs the model server itself."""

from typing import Dict, Iterator, List, Optional

from ...config import Config
from ...core.logger import get_logger
from .. import runner
from .api import APIProvider

logger = get_logger(__name__)


class LlamaCppProvider(APIProvider):
    """Chat/embeddings against a ``llama-server`` that JARVIS supervises.

    With no ``base_url`` the server is started on demand from ``model`` (a
    GGUF path or a Hugging Face ``user/repo[:quant]``) on ``LLM_PORT``. With a
    ``base_url`` it talks to an already-running llama-server and manages
    nothing.
    """

    def __init__(
        self,
        model: str,
        base_url: Optional[str] = None,
        temperature: Optional[float] = None,
    ):
        self._server = None if base_url else runner.get_server(model, Config.LLM_PORT)
        super().__init__(
            model=model,
            api_url=base_url or self._server.url,
            temperature=temperature,
        )

    def _ensure_server(self) -> None:
        if self._server is not None and Config.LLM_AUTO_START:
            self._server.ensure_running()

    def chat(self, messages: List[Dict[str, str]]) -> str:
        self._ensure_server()
        return super().chat(messages)

    def stream_chat(self, messages: List[Dict[str, str]]) -> Iterator[str]:
        self._ensure_server()
        yield from super().stream_chat(messages)

    def embed(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError(
            "chat models are not started with --embedding; use LlamaEmbeddings"
        )

    def is_available(self) -> bool:
        if self._server is None:
            return super().is_available()
        return self._server.is_up() or runner.find_binary() is not None
