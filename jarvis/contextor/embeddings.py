"""
Embedding provider for JARVIS — vectors from a llama-server run with --embedding.

These embeddings power semantic search in the vector store (RAG context
retrieval and tool discovery). The default model, nomic-embed-text v1.5, is
small (~150MB at Q8_0) and produces 768-dim vectors. It runs in its own
llama-server on ``EMBED_PORT``, started on demand like the chat model.
"""

from typing import List

from ..config import Config
from ..core.logger import get_logger
from ..llm import runner

logger = get_logger(__name__)

EMBED_TIMEOUT = 30.0


class LlamaEmbeddings:
    """Generate embeddings using a llama-server that JARVIS supervises."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self.model = model or Config.EMBED_MODEL
        base_url = base_url or Config.EMBED_URL
        self._server = (
            None
            if base_url
            else runner.get_server(self.model, Config.EMBED_PORT, embedding=True)
        )
        self.base_url = (base_url or self._server.url).rstrip("/")

        import httpx

        self._httpx = httpx
        self._dim: int | None = None
        logger.info(f"Embeddings: Using model '{self.model}' at {self.base_url}")

    @property
    def dimension(self) -> int:
        """Return embedding dimension, probing the model on first call."""
        if self._dim is None:
            probe = self.embed_single("hello")
            self._dim = len(probe)
            logger.info(f"Embeddings: Detected dimension = {self._dim}")
        return self._dim

    def _request(self, texts: List[str]) -> List[List[float]]:
        response = self._httpx.post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model, "input": texts},
            timeout=EMBED_TIMEOUT,
        )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda d: d.get("index", 0))
        return [item["embedding"] for item in data]

    def embed_single(self, text: str) -> List[float]:
        """Embed a single text string, returning a float vector."""
        return self._request([text])[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts; a failed batch yields zero vectors."""
        try:
            return self._request(texts)
        except Exception as e:
            logger.warning(f"Embeddings: Failed to embed batch: {e}")
            dim = self._dim or 768
            return [[0.0] * dim for _ in texts]

    def ensure_model(self) -> bool:
        """Start the embedding server if needed and confirm it answers."""
        try:
            if self._server is not None:
                self._server.ensure_running()
            self.embed_single("test")
            return True
        except Exception as e:
            logger.error(f"Embeddings: Availability check failed: {e}")
            return False
