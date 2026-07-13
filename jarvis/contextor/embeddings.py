"""
Embedding provider for JARVIS — generates vector embeddings via Ollama.

Uses Ollama's /api/embeddings endpoint with a lightweight embedding model
(default: nomic-embed-text). These embeddings power semantic search in the
vector store, enabling RAG-style context retrieval.

Why Ollama embeddings?
- Runs locally, no API keys needed — matches JARVIS's local-first design
- Same infrastructure as the chat model (Ollama server)
- nomic-embed-text is small (~270MB) and produces 768-dim vectors
"""

from typing import List

from ..config import Config
from ..core.logger import get_logger
from ..llm.ollama_utils import try_start_ollama as _try_start_ollama_base

logger = get_logger(__name__)

DEFAULT_EMBED_MODEL = "nomic-embed-text"

_CONNECT_KEYWORDS = ("connect", "connection", "refused", "unreachable", "timeout")


def _try_start_ollama(base_url: str) -> bool:
    return _try_start_ollama_base(base_url, log_prefix="Embeddings")


class OllamaEmbeddings:
    """Generate embeddings using a local Ollama instance."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self.model = model or getattr(Config, "EMBED_MODEL", DEFAULT_EMBED_MODEL)
        self.base_url = base_url or getattr(
            Config, "EMBED_URL", "http://localhost:11434"
        )

        try:
            from ollama import Client

            self._client = Client(host=self.base_url, timeout=30)
        except ImportError:
            raise ImportError("ollama package required: pip install ollama")

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

    def embed_single(self, text: str) -> List[float]:
        """Embed a single text string, returning a float vector."""
        response = self._client.embeddings(
            model=self.model,
            prompt=text,
        )
        return response["embedding"]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts. Falls back to sequential calls."""
        results = []
        for text in texts:
            try:
                results.append(self.embed_single(text))
            except Exception as e:
                logger.warning(f"Embeddings: Failed to embed text: {e}")
                dim = self._dim or 768
                results.append([0.0] * dim)
        return results

    def ensure_model(self) -> bool:
        """Check if the embedding model is available; auto-start Ollama and pull if needed."""
        try:
            self.embed_single("test")
            return True
        except Exception as e:
            error_str = str(e).lower()

            if any(kw in error_str for kw in _CONNECT_KEYWORDS):
                logger.info("Embeddings: Ollama not reachable — attempting auto-start")
                if not _try_start_ollama(self.base_url):
                    return False
                # Ollama is now up — re-check; may still need to pull the model.
                return self.ensure_model()

            if "not found" in error_str or "does not exist" in error_str:
                # nomic-embed-text is infrastructure, not a user LLM choice —
                # always pull it automatically when Ollama is reachable.
                logger.info(
                    f"Embeddings: Model '{self.model}' not pulled yet — pulling now"
                )
                return self._pull_model()

            logger.error(f"Embeddings: Availability check failed: {e}")
            return False

    def _pull_model(self) -> bool:
        """Pull the embedding model from Ollama."""
        try:
            import ollama as _ollama

            logger.info(f"Embeddings: Pulling model '{self.model}'...")
            _ollama.pull(self.model)
            logger.info(f"Embeddings: Successfully pulled '{self.model}'")
            return True
        except Exception as e:
            logger.error(f"Embeddings: Failed to pull '{self.model}': {e}")
            return False
