"""
ContextorAdapter — thin stdio client to the Rust contextor binary.

The Rust binary handles all storage, vector indexing, and cosine
similarity search.  This adapter spawns it as a child process and
communicates over stdin/stdout with JSON lines.

JARVIS owns embedding computation (via OllamaEmbeddings).  The adapter
embeds text locally and passes pre-computed vectors to the binary.
The binary never calls Ollama or any ML runtime.

Protocol:
  JARVIS  -->  {"cmd": "store", ...}\n   -->  contextor (stdin)
  JARVIS  <--  {"ok": true, ...}\n       <--  contextor (stdout)
"""

import json
import subprocess
import threading
from typing import Dict, Any, Optional
from ..core.logger import get_logger
from ..config import Config

logger = get_logger(__name__)


class ContextorAdapter:
    """Stdio client for the Rust contextor binary."""

    def __init__(self, embeddings: Optional[Any] = None):
        self._binary = Config.CONTEXTOR_BINARY
        self._embeddings = embeddings
        self._process: Optional[subprocess.Popen] = None
        self._connected = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Spawn the contextor binary and verify connectivity."""
        try:
            self._process = subprocess.Popen(
                [self._binary],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            status = self._send({"cmd": "status"})
            self._connected = status.get("ok", False)
            if self._connected:
                logger.info(
                    f"Contextor: Connected "
                    f"(entries={status.get('total_entries', 0)}, "
                    f"themes={status.get('themes', 0)})"
                )
            else:
                logger.error(f"Contextor: Binary started but status failed: {status}")
        except FileNotFoundError:
            logger.warning(
                f"Contextor: Binary not found at '{self._binary}'. "
                f"Memory is disabled."
            )
            self._connected = False
        except Exception as e:
            logger.error(f"Contextor: Failed to start binary: {e}")
            self._connected = False

        # Run initial prune on startup
        if self._connected:
            self._send({
                "cmd": "prune",
                "retention_days": Config.MEMORY_RETENTION_DAYS,
                "max_per_theme": Config.MAX_ENTRIES_PER_THEME,
            })

        return self._connected

    def disconnect(self) -> None:
        """Terminate the contextor process."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            except Exception:
                pass
            self._process = None
        self._connected = False
        logger.info("Contextor: Disconnected")

    @property
    def search_available(self) -> bool:
        """Whether semantic search is operational (binary + embeddings)."""
        return self._connected and self._embeddings is not None

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Wire protocol
    # ------------------------------------------------------------------

    def _send(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Send a JSON command and read the JSON response."""
        if not self._process or self._process.poll() is not None:
            return {"ok": False, "error": "Contextor process not running"}

        with self._lock:
            try:
                line = json.dumps(cmd, ensure_ascii=False) + "\n"
                self._process.stdin.write(line.encode("utf-8"))
                self._process.stdin.flush()
                response_line = self._process.stdout.readline()
                if not response_line:
                    self._connected = False
                    return {"ok": False, "error": "No response from contextor"}
                return json.loads(response_line.decode("utf-8"))
            except (BrokenPipeError, OSError) as e:
                self._connected = False
                return {"ok": False, "error": f"Pipe error: {e}"}
            except json.JSONDecodeError as e:
                return {"ok": False, "error": f"Invalid JSON from contextor: {e}"}

    def _embed(self, text: str) -> Optional[list]:
        """Embed text via OllamaEmbeddings, or None if unavailable."""
        if not self._embeddings:
            return None
        try:
            return self._embeddings.embed_single(text)
        except Exception as e:
            logger.warning(f"Contextor: Embedding failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def store(self, theme: str, content: str) -> Dict[str, Any]:
        """Store a fact under a theme.  Embeds content and sends vector."""
        if not self._connected:
            return {"error": "Contextor not connected"}

        vector = self._embed(content) or []
        result = self._send({
            "cmd": "store",
            "theme": theme,
            "content": content,
            "vector": vector,
            "metadata": {},
        })

        if result.get("ok"):
            logger.info(f"Contextor: Stored under '{theme}' ({len(content)} chars)")
            return {"stored": True, "theme": theme}

        logger.warning(f"Contextor: Store failed: {result.get('error')}")
        return {"error": result.get("error", "Store failed")}

    def auto_store_prompt(self, text: str) -> None:
        """
        Automatically store every user prompt for long-term recall.

        No LLM decision — fires on every input.  Creates a complete
        searchable history under the ``conversation_log`` theme.
        """
        if not self._connected:
            return

        vector = self._embed(text) or []
        self._send({
            "cmd": "store",
            "theme": "conversation_log",
            "content": text,
            "vector": vector,
            "metadata": {"type": "user_prompt"},
        })
        logger.debug(f"Contextor: Auto-stored prompt ({len(text)} chars)")

    def recall(self, theme: str, limit: int = 20) -> Dict[str, Any]:
        """Recall entries by exact theme (no embeddings needed)."""
        if not self._connected:
            return {"theme": theme, "entries": [], "found": False}

        result = self._send({"cmd": "recall", "theme": theme, "limit": limit})
        if result.get("ok"):
            entries = result.get("entries", [])
            logger.info(f"Contextor: Recalled {len(entries)} entries for '{theme}'")
            return {"theme": theme, "entries": entries, "found": bool(entries)}

        return {"theme": theme, "entries": [], "found": False}

    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        offset: int = 0,
        theme: str | None = None,
        min_score: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Semantic search across all memories using vector similarity.

        Returns ``{"available": false}`` when the binary or embeddings
        are unavailable — no silent keyword fallback.
        """
        if not self._connected or not self._embeddings:
            return {
                "results": [],
                "available": False,
                "reason": (
                    "Contextor binary not running"
                    if not self._connected
                    else "Embedding model not available"
                ),
            }

        vector = self._embed(query)
        if vector is None:
            return {"results": [], "available": False, "reason": "Embedding failed"}

        result = self._send({
            "cmd": "search",
            "vector": vector,
            "top_k": top_k,
            "offset": offset,
            "min_score": min_score,
            "theme": theme,
        })

        if result.get("ok"):
            results = result.get("results", [])
            logger.info(
                f"Contextor: Search '{query[:50]}' "
                f"returned {len(results)} results (offset={offset})"
            )
            return {"results": results, "available": True}

        return {
            "results": [],
            "available": result.get("available", False),
            "reason": result.get("error", "Search failed"),
        }

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

        Returns a formatted string, or empty string if nothing relevant.
        """
        result = self.semantic_search(
            query, top_k=top_k, offset=offset, min_score=min_score,
        )

        if not result.get("available", False):
            return ""

        entries = result.get("results", [])
        if not entries:
            return ""

        parts = []
        for entry in entries:
            theme = entry.get("theme", "unknown")
            content = entry.get("content", "")
            score = entry.get("score", 0)
            parts.append(f"  [{theme}] (relevance: {score:.0%}) {content}")

        formatted = "RELEVANT MEMORIES:\n" + "\n".join(parts)
        logger.debug(
            f"Contextor: RAG context ({len(entries)} entries, {len(formatted)} chars)"
        )
        return formatted

    def list_themes(self) -> Dict[str, Any]:
        """List all stored themes with entry counts."""
        if not self._connected:
            return {"themes": []}

        result = self._send({"cmd": "list"})
        if result.get("ok"):
            themes = result.get("themes", [])
            logger.info(f"Contextor: {len(themes)} theme(s) in memory")
            return {"themes": themes}

        return {"themes": [], "error": result.get("error")}

    def delete_theme(self, theme: str) -> Dict[str, Any]:
        """Delete all entries for a theme."""
        if not self._connected:
            return {"deleted": False, "reason": "Not connected"}

        result = self._send({"cmd": "delete", "theme": theme})
        if result.get("ok"):
            logger.info(f"Contextor: Deleted theme '{theme}'")
            return {"deleted": True, "theme": theme}

        return {"error": result.get("error", "Delete failed")}

    def reindex(self) -> Dict[str, Any]:
        """Rebuild the vector index from stored entries."""
        if not self._connected:
            return {"error": "Not connected"}

        result = self._send({"cmd": "reindex"})
        if result.get("ok"):
            logger.info(f"Contextor: Reindexed {result.get('indexed', 0)} entries")
            return {"reindexed": result.get("indexed", 0)}

        return {"error": result.get("error", "Reindex failed")}
