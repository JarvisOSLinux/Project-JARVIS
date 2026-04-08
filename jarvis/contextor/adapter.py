"""
ContextorAdapter — manages long-term memory for JARVIS.

Embeddings-only memory retrieval:
- Every user prompt is automatically stored with its embedding vector
- Retrieval is purely semantic (cosine similarity via vector store)
- No keyword substring search — if embeddings are unavailable, search
  returns empty results with a clear "unavailable" flag
- JSONL files remain the persistence source of truth
- Vector store (ChromaDB) is the search index, kept in sync on store()

Storage location follows XDG:
  ~/.local/share/jarvis/memory/       (JSONL themes)
  ~/.local/share/jarvis/chroma_db/    (vector index)

When the Rust contextor binary is ready, this adapter becomes a thin
MCP client over stdio — same pattern as DispatchAdapter.
"""

import json
import os
import time
from typing import Dict, Any, List, Optional
from ..core.logger import get_logger
from ..config import Config

logger = get_logger(__name__)

_DEFAULT_MEMORY_DIR = os.path.join(Config.JARVIS_DATA_DIR, "memory")


class ContextorAdapter:
    """Embeddings-only long-term memory for JARVIS."""

    def __init__(
        self,
        memory_dir: str | None = None,
        vector_store: Optional[Any] = None,
    ):
        self._memory_dir = memory_dir or _DEFAULT_MEMORY_DIR
        os.makedirs(self._memory_dir, exist_ok=True)

        self._vector_store = vector_store
        self._available = vector_store is not None

        if self._available:
            logger.info(
                f"Contextor: Memory directory: {self._memory_dir}, "
                f"vector store: enabled ({vector_store.count} entries)"
            )
        else:
            logger.warning(
                "Contextor: Vector store NOT available — memory search is disabled. "
                "Store and recall by theme still work. "
                "Ensure EMBED_MODEL is pulled and chromadb is installed."
            )

    @property
    def search_available(self) -> bool:
        """Whether semantic search is operational."""
        return self._available

    def _theme_path(self, theme: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in theme.lower())
        return os.path.join(self._memory_dir, f"{safe}.jsonl")

    def _prune_theme(self, path: str) -> None:
        """
        Prune theme file: remove entries older than retention days, then cap by
        max entries per theme (FIFO — oldest dropped first).
        """
        if not os.path.exists(path):
            return
        retention_days = getattr(Config, "MEMORY_RETENTION_DAYS", 90)
        max_entries = getattr(Config, "MAX_ENTRIES_PER_THEME", 500)
        cutoff = time.time() - (retention_days * 86400)
        entries: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        stored_at = entry.get("stored_at", 0)
                        if stored_at >= cutoff:
                            entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            logger.warning(f"Contextor: Failed to read for prune: {e}")
            return
        if len(entries) > max_entries:
            entries = entries[-max_entries:]
        try:
            if not entries:
                os.remove(path)
                logger.debug(f"Contextor: Removed empty theme file {path}")
            else:
                with open(path, "w", encoding="utf-8") as f:
                    for entry in entries:
                        f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.warning(f"Contextor: Failed to rewrite after prune: {e}")

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def store(self, theme: str, content: str) -> Dict[str, Any]:
        """
        Store a piece of information under a theme.

        Each entry is timestamped, written to JSONL (source of truth),
        and indexed in the vector store for semantic retrieval.
        """
        entry = {
            "content": content,
            "stored_at": time.time(),
            "stored_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        path = self._theme_path(theme)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            self._prune_theme(path)

            # Index in vector store for semantic search
            if self._vector_store:
                try:
                    self._vector_store.add(content=content, theme=theme)
                except Exception as e:
                    # Non-fatal — JSONL is the source of truth
                    logger.warning(f"Contextor: Vector indexing failed (non-fatal): {e}")

            logger.info(f"Contextor: Stored under theme '{theme}' ({len(content)} chars)")
            return {"stored": True, "theme": theme}
        except OSError as e:
            logger.error(f"Contextor: Failed to store under '{theme}': {e}")
            return {"error": f"Failed to store: {e}"}

    def auto_store_prompt(self, text: str) -> None:
        """
        Automatically store a user prompt for long-term recall.

        Called on every user input — no LLM decision needed. This creates
        a complete searchable history of everything the user has ever said.
        Stored under the "conversation_log" theme.
        """
        entry = {
            "content": text,
            "stored_at": time.time(),
            "stored_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
            "type": "user_prompt",
        }

        path = self._theme_path("conversation_log")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

            # Index in vector store — this is what makes old prompts searchable
            if self._vector_store:
                try:
                    self._vector_store.add(
                        content=text,
                        theme="conversation_log",
                        metadata={"type": "user_prompt"},
                    )
                except Exception as e:
                    logger.debug(f"Contextor: Auto-store vector indexing failed: {e}")

            logger.debug(f"Contextor: Auto-stored user prompt ({len(text)} chars)")
        except OSError as e:
            logger.debug(f"Contextor: Auto-store failed (non-fatal): {e}")

    def recall(self, theme: str, limit: int = 20) -> Dict[str, Any]:
        """
        Recall entries stored under a theme.
        Returns the most recent ``limit`` entries (newest last).
        This is a direct theme lookup — no embeddings needed.
        """
        path = self._theme_path(theme)
        if not os.path.exists(path):
            logger.info(f"Contextor: No memory found for theme '{theme}'")
            return {"theme": theme, "entries": [], "found": False}

        entries: list[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError as e:
            logger.error(f"Contextor: Failed to read theme '{theme}': {e}")
            return {"error": f"Failed to recall: {e}"}

        recent = entries[-limit:]
        logger.info(f"Contextor: Recalled {len(recent)} entries for theme '{theme}'")
        return {"theme": theme, "entries": recent, "found": True}

    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        offset: int = 0,
        theme: str | None = None,
        min_score: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Semantic search — find memories by meaning using embeddings.

        Embeddings-only: returns empty results when vector store is
        unavailable. No keyword fallback.

        Args:
            query: Natural language query.
            top_k: Number of results to return.
            offset: Skip the top N closest results (for pagination/digging deeper).
            theme: Optional theme filter.
            min_score: Minimum cosine similarity (0-1).
        """
        if not self._vector_store:
            logger.warning("Contextor: Semantic search unavailable (no vector store)")
            return {
                "results": [],
                "available": False,
                "reason": "Vector store not initialized — ensure embedding model is pulled and chromadb is installed",
            }

        # Fetch top_k + offset results, then slice off the offset
        fetch_count = top_k + offset

        results = self._vector_store.query(
            query_text=query,
            top_k=fetch_count,
            theme=theme,
            min_score=min_score,
        )

        # Apply offset — skip the N most similar results
        if offset > 0 and results:
            results = results[offset:]

        logger.info(
            f"Contextor: Semantic search for '{query[:50]}' "
            f"returned {len(results)} results (offset={offset}, top_k={top_k})"
        )
        return {"results": results, "available": True}

    def retrieve_context(
        self,
        query: str,
        top_k: int = 5,
        offset: int = 0,
        min_score: float = 0.3,
    ) -> str:
        """
        RAG entry point — retrieve relevant memories and format them
        for injection into the LLM context.

        Returns a formatted string ready for prompt inclusion, or empty
        string if nothing relevant or search is unavailable.
        """
        result = self.semantic_search(
            query, top_k=top_k, offset=offset, min_score=min_score,
        )

        if not result.get("available", False):
            return ""

        entries = result.get("results", [])
        if not entries:
            return ""

        # Format retrieved memories for the LLM
        parts = []
        for entry in entries:
            theme = entry.get("theme", "unknown")
            content = entry.get("content", "")
            score = entry.get("score", 0)
            parts.append(f"  [{theme}] (relevance: {score:.0%}) {content}")

        formatted = "RELEVANT MEMORIES:\n" + "\n".join(parts)
        logger.debug(f"Contextor: RAG context ({len(entries)} entries, {len(formatted)} chars)")
        return formatted

    def list_themes(self) -> Dict[str, Any]:
        """List all stored themes with entry counts."""
        themes: list[dict] = []

        try:
            for filename in sorted(os.listdir(self._memory_dir)):
                if not filename.endswith(".jsonl"):
                    continue

                theme = filename[:-6]
                path = os.path.join(self._memory_dir, filename)

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        count = sum(1 for line in f if line.strip())
                    stat = os.stat(path)
                    themes.append({
                        "theme": theme,
                        "entries": count,
                        "last_modified": time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime),
                        ),
                    })
                except OSError:
                    continue
        except OSError as e:
            logger.error(f"Contextor: Failed to list themes: {e}")
            return {"error": f"List failed: {e}", "themes": []}

        logger.info(f"Contextor: {len(themes)} theme(s) in memory")
        return {"themes": themes}

    def delete_theme(self, theme: str) -> Dict[str, Any]:
        """Delete all entries for a theme (both JSONL and vector store)."""
        path = self._theme_path(theme)
        if not os.path.exists(path):
            return {"deleted": False, "reason": "Theme not found"}

        try:
            os.remove(path)

            if self._vector_store:
                try:
                    self._vector_store.delete_by_theme(theme)
                except Exception as e:
                    logger.warning(f"Contextor: Vector store cleanup failed: {e}")

            logger.info(f"Contextor: Deleted theme '{theme}'")
            return {"deleted": True, "theme": theme}
        except OSError as e:
            logger.error(f"Contextor: Failed to delete theme '{theme}': {e}")
            return {"error": f"Failed to delete: {e}"}

    def reindex_vector_store(self) -> Dict[str, Any]:
        """
        Rebuild the vector store index from JSONL source files.
        Useful after migration or if the vector store gets corrupted.
        """
        if not self._vector_store:
            return {"error": "No vector store configured"}

        indexed = 0
        errors = 0

        try:
            for filename in os.listdir(self._memory_dir):
                if not filename.endswith(".jsonl"):
                    continue

                theme = filename[:-6]
                path = os.path.join(self._memory_dir, filename)

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                entry = json.loads(line)
                                content = entry.get("content", "")
                                if content:
                                    self._vector_store.add(
                                        content=content,
                                        theme=theme,
                                        metadata={
                                            "stored_at": entry.get("stored_at", 0),
                                            "stored_iso": entry.get("stored_iso", ""),
                                            "type": entry.get("type", ""),
                                        },
                                    )
                                    indexed += 1
                            except Exception:
                                errors += 1
                                continue
                except OSError:
                    continue
        except OSError as e:
            return {"error": f"Reindex failed: {e}"}

        logger.info(f"Contextor: Reindexed {indexed} entries ({errors} errors)")
        return {"reindexed": indexed, "errors": errors}
