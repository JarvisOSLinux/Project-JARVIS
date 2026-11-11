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
        self._managed = not base_url
        super().__init__(
            model=model,
            api_url=base_url or f"http://{Config.LLM_HOST}:{Config.LLM_PORT}",
            temperature=temperature,
        )

    def _ensure_server(self) -> None:
        # The pool builds every provider at start, and LLM_PORT serves one
        # model: the provider being used claims it now, so failing over from
        # one local model to another swaps the server instead of talking to
        # the first model under the second's name.
        if self._managed and Config.LLM_AUTO_START:
            runner.get_server(self.model, Config.LLM_PORT).ensure_running()

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
        if not self._managed:
            return super().is_available()
        if runner.find_binary() is not None:
            return True
        server = runner.LlamaServer(self.model, Config.LLM_PORT)
        return server.is_up() and server.serves(self.model)
