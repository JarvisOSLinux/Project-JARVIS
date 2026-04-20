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

from typing import List, Optional

from ..config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)

DEFAULT_EMBED_MODEL = "nomic-embed-text"


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
        # Ollama doesn't natively support batch embeddings in all versions,
        # so we call sequentially. For typical JARVIS workloads (storing a
        # few memories per interaction) this is fine.
        results = []
        for text in texts:
            try:
                results.append(self.embed_single(text))
            except Exception as e:
                logger.warning(f"Embeddings: Failed to embed text: {e}")
                # Return zero vector as fallback so indexing stays consistent
                dim = self._dim or 768
                results.append([0.0] * dim)
        return results

    def ensure_model(self) -> bool:
        """Check if the embedding model is available, pull if configured."""
        try:
            # Quick probe — if this works, the model is available
            self.embed_single("test")
            return True
        except Exception as e:
            error_str = str(e).lower()
            if "not found" in error_str or "does not exist" in error_str:
                logger.warning(
                    f"Embedding model '{self.model}' not found. "
                    f"Pull it with: ollama pull {self.model}"
                )
                if getattr(Config, "LLM_AUTO_PULL", False):
                    return self._pull_model()
            else:
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
