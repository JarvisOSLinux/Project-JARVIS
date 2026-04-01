"""
Vector store for JARVIS — ChromaDB-backed semantic memory index.

This wraps ChromaDB to provide:
- Automatic embedding on add/query (via OllamaEmbeddings)
- Metadata storage (theme, timestamp) for filtering
- Top-k semantic retrieval with optional theme filtering
- Reranking via relevance scoring

ChromaDB is chosen because:
- Embedded (no server process needed) — runs in-process
- Persistent storage to disk — survives restarts
- Lightweight — no heavy dependencies like FAISS GPU
- Built-in distance metrics and metadata filtering
"""

import os
import time
from typing import Any, Dict, List, Optional

from ..config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_CHROMA_DIR = os.path.join(Config.JARVIS_DATA_DIR, "chroma_db")


class VectorStore:
    """ChromaDB-backed vector store for semantic memory retrieval."""

    COLLECTION_NAME = "jarvis_memory"

    def __init__(
        self,
        embeddings: Any,  # OllamaEmbeddings instance
        persist_dir: str | None = None,
    ):
        self._embeddings = embeddings
        self._persist_dir = persist_dir or _DEFAULT_CHROMA_DIR

        try:
            import chromadb
        except ImportError:
            raise ImportError("chromadb required: pip install chromadb")

        os.makedirs(self._persist_dir, exist_ok=True)

        self._client = chromadb.PersistentClient(path=self._persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},  # cosine similarity
        )

        logger.info(
            f"VectorStore: Initialized at {self._persist_dir} "
            f"({self._collection.count()} entries)"
        )

    def add(
        self,
        content: str,
        theme: str,
        entry_id: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> str:
        """
        Add a memory entry to the vector store.

        Returns the document ID used for storage.
        """
        doc_id = entry_id or f"{theme}_{int(time.time() * 1000)}"

        doc_metadata = {
            "theme": theme,
            "stored_at": time.time(),
            "stored_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if metadata:
            doc_metadata.update(metadata)

        try:
            embedding = self._embeddings.embed_single(content)
            self._collection.add(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[content],
                metadatas=[doc_metadata],
            )
            logger.debug(f"VectorStore: Added entry '{doc_id}' under theme '{theme}'")
            return doc_id
        except Exception as e:
            logger.error(f"VectorStore: Failed to add entry: {e}")
            raise

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        theme: str | None = None,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search: find the top-k most relevant memories.

        Args:
            query_text: The text to search for semantically.
            top_k: Number of results to return.
            theme: Optional — restrict search to a specific theme.
            min_score: Minimum relevance score (0-1, cosine similarity).

        Returns:
            List of dicts with keys: content, theme, score, stored_at, id
        """
        if self._collection.count() == 0:
            return []

        try:
            query_embedding = self._embeddings.embed_single(query_text)

            where_filter = {"theme": theme} if theme else None

            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, self._collection.count()),
                where=where_filter,
            )

            entries = []
            if results and results["documents"] and results["documents"][0]:
                for i, doc in enumerate(results["documents"][0]):
                    # ChromaDB returns distances; for cosine, score = 1 - distance
                    distance = results["distances"][0][i] if results["distances"] else 0
                    score = 1.0 - distance

                    if score < min_score:
                        continue

                    meta = results["metadatas"][0][i] if results["metadatas"] else {}
                    entries.append({
                        "content": doc,
                        "theme": meta.get("theme", "unknown"),
                        "score": round(score, 4),
                        "stored_at": meta.get("stored_at", 0),
                        "id": results["ids"][0][i],
                    })

            logger.debug(
                f"VectorStore: Query '{query_text[:50]}...' returned "
                f"{len(entries)} results (top score: "
                f"{entries[0]['score'] if entries else 'N/A'})"
            )
            return entries

        except Exception as e:
            logger.error(f"VectorStore: Query failed: {e}")
            return []

    def delete_by_theme(self, theme: str) -> int:
        """Delete all entries for a given theme. Returns count deleted."""
        try:
            # Get all IDs for this theme
            results = self._collection.get(
                where={"theme": theme},
            )
            if results and results["ids"]:
                self._collection.delete(ids=results["ids"])
                count = len(results["ids"])
                logger.info(f"VectorStore: Deleted {count} entries for theme '{theme}'")
                return count
            return 0
        except Exception as e:
            logger.error(f"VectorStore: Delete failed for theme '{theme}': {e}")
            return 0

    def prune_old(self, max_age_days: int | None = None) -> int:
        """Remove entries older than max_age_days. Returns count removed."""
        max_age = max_age_days or getattr(Config, "MEMORY_RETENTION_DAYS", 90)
        cutoff = time.time() - (max_age * 86400)

        try:
            results = self._collection.get(
                where={"stored_at": {"$lt": cutoff}},
            )
            if results and results["ids"]:
                self._collection.delete(ids=results["ids"])
                count = len(results["ids"])
                logger.info(f"VectorStore: Pruned {count} entries older than {max_age} days")
                return count
            return 0
        except Exception as e:
            logger.error(f"VectorStore: Prune failed: {e}")
            return 0

    @property
    def count(self) -> int:
        """Total number of entries in the vector store."""
        return self._collection.count()
