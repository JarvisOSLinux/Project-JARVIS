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

Sessions (chat conversations):
  Every memory entry has an optional ``session_id``.  Entries without
  one are *global* and visible to every session (facts the user wants
  to carry across conversations).  Entries with a ``session_id`` are
  scoped to that conversation.
"""

import json
import subprocess
import threading
from typing import Any, Dict, Optional

from ..config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)


class ContextorAdapter:
    """Stdio client for the Rust contextor binary."""

    def __init__(self, embeddings: Optional[Any] = None):
        self._binary = Config.CONTEXTOR_BINARY
        self._embeddings = embeddings
        self._process: Optional[subprocess.Popen] = None
        self._connected = False
        self._lock = threading.Lock()
        self._supports_sessions: Optional[bool] = None
        self._supports_delete_scope: Optional[bool] = None

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

        if self._connected:
            self._send(
                {
                    "cmd": "prune",
                    "retention_days": Config.MEMORY_RETENTION_DAYS,
                    "max_per_theme": Config.MAX_ENTRIES_PER_THEME,
                }
            )

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

    @property
    def supports_sessions(self) -> bool:
        """Whether the connected binary supports session CRUD commands."""
        # Unknown defaults to True so we only disable after a proven mismatch.
        return self._supports_sessions is not False

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
                response = json.loads(response_line.decode("utf-8"))
                self._update_capabilities_from_error(cmd, response)
                return response
            except (BrokenPipeError, OSError) as e:
                self._connected = False
                return {"ok": False, "error": f"Pipe error: {e}"}
            except json.JSONDecodeError as e:
                return {"ok": False, "error": f"Invalid JSON from contextor: {e}"}

    def _update_capabilities_from_error(
        self,
        cmd: Dict[str, Any],
        response: Dict[str, Any],
    ) -> None:
        """Infer binary capability flags from parse/validation errors."""
        if response.get("ok", False):
            return
        error = str(response.get("error", "")).lower()
        name = str(cmd.get("cmd", "")).lower()
        if not error or not name:
            return

        if (
            "unknown variant" in error
            and name
            in {
                "create_session",
                "list_sessions",
                "get_session",
                "update_session",
                "delete_session",
            }
            and self._supports_sessions is not False
        ):
            self._supports_sessions = False
            logger.warning(
                "Contextor: Session commands unsupported by the connected "
                "binary; running with single-stream memory scope."
            )

        if (
            "unknown field" in error
            and "session_id" in error
            and name == "delete"
            and self._supports_delete_scope is not False
        ):
            self._supports_delete_scope = False
            logger.warning(
                "Contextor: Scoped delete unsupported by the connected binary; "
                "falling back to theme-wide delete."
            )

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

    def store(
        self,
        theme: str,
        content: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Store a fact under a theme.

        Args:
            theme: Topic name the entry is filed under.
            content: The fact/content to remember.
            session_id: If given, scope to that chat session.  None = global
                (visible to every session).
        """
        if not self._connected:
            return {"error": "Contextor not connected"}

        vector = self._embed(content) or []
        result = self._send(
            {
                "cmd": "store",
                "theme": theme,
                "content": content,
                "vector": vector,
                "metadata": {},
                "session_id": session_id,
            }
        )

        if result.get("ok"):
            scope = f"session={session_id[:8]}" if session_id else "global"
            logger.info(
                f"Contextor: Stored under '{theme}' ({len(content)} chars, {scope})"
            )
            return {"stored": True, "theme": theme, "session_id": session_id}

        logger.warning(f"Contextor: Store failed: {result.get('error')}")
        return {"error": result.get("error", "Store failed")}

    def _auto_store_message(
        self,
        text: str,
        role: str,
        session_id: Optional[str] = None,
    ) -> None:
        """Store a conversation turn under ``conversation_log``.

        Args:
            text: The utterance to store.
            role: ``"user_prompt"`` or ``"assistant_reply"``.
            session_id: Chat session scope.  None = global.
        """
        if not self._connected or not (text or "").strip():
            return

        # Cap huge assistant dumps to avoid pathological DB / embedding size.
        max_chars = 100_000
        if len(text) > max_chars:
            text = text[:max_chars] + "\n… [truncated]"

        vector = self._embed(text) or []
        self._send(
            {
                "cmd": "store",
                "theme": "conversation_log",
                "content": text,
                "vector": vector,
                "metadata": {"type": role},
                "session_id": session_id,
            }
        )
        scope = f"session={session_id[:8]}" if session_id else "global"
        logger.debug(f"Contextor: Auto-stored {role} ({len(text)} chars, {scope})")

    def auto_store_prompt(
        self,
        text: str,
        session_id: Optional[str] = None,
    ) -> None:
        """Store every user prompt for long-term recall.

        No LLM decision — fires on every input.  Creates a searchable
        history under the ``conversation_log`` theme.
        """
        self._auto_store_message(text, "user_prompt", session_id)

    def auto_store_assistant_reply(
        self,
        text: str,
        session_id: Optional[str] = None,
    ) -> None:
        """Store assistant replies under ``conversation_log``.

        Produces a searchable per-session transcript (user + assistant turns).
        Skips empty replies and replies without a session scope.
        """
        if not session_id:
            return
        self._auto_store_message(text, "assistant_reply", session_id)

    def recall(
        self,
        theme: str,
        limit: int = 20,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Recall entries by exact theme (no embeddings needed).

        Args:
            theme: Topic name.
            limit: Max entries to return.
            session_id: If given, only return entries belonging to this
                session plus any global entries.  None = all entries
                regardless of session.
        """
        if not self._connected:
            return {"theme": theme, "entries": [], "found": False}

        result = self._send(
            {
                "cmd": "recall",
                "theme": theme,
                "limit": limit,
                "session_id": session_id,
            }
        )
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
        theme: Optional[str] = None,
        min_score: float = 0.3,
        session_id: Optional[str] = None,
        include_global: bool = True,
    ) -> Dict[str, Any]:
        """
        Semantic search across memories using vector similarity.

        Returns ``{"available": false}`` when the binary or embeddings
        are unavailable — no silent keyword fallback.

        Args:
            query: Natural language search query.
            top_k: Max results.
            offset: Skip this many top results (for pagination).
            theme: Restrict to one theme.  None = all themes.
            min_score: Cosine similarity floor (0.0-1.0).
            session_id: If given, restrict to entries in this session.
            include_global: When ``session_id`` is set, also match global
                entries.  Ignored if ``session_id`` is None.
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

        result = self._send(
            {
                "cmd": "search",
                "vector": vector,
                "top_k": top_k,
                "offset": offset,
                "min_score": min_score,
                "theme": theme,
                "session_id": session_id,
                "include_global": include_global,
            }
        )

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
        session_id: Optional[str] = None,
        include_global: bool = True,
    ) -> str:
        """
        RAG entry point — retrieve relevant memories formatted for LLM injection.

        Returns a formatted string, or empty string if nothing relevant.
        """
        result = self.semantic_search(
            query,
            top_k=top_k,
            offset=offset,
            min_score=min_score,
            session_id=session_id,
            include_global=include_global,
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

    def list_themes(
        self,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List all stored themes with entry counts.

        Args:
            session_id: If given, restrict counts to this session (plus
                global entries).  None = count across all sessions.
        """
        if not self._connected:
            return {"themes": []}

        result = self._send({"cmd": "list", "session_id": session_id})
        if result.get("ok"):
            themes = result.get("themes", [])
            logger.info(f"Contextor: {len(themes)} theme(s) in memory")
            return {"themes": themes}

        return {"themes": [], "error": result.get("error")}

    def delete_theme(
        self,
        theme: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete all entries for a theme.

        Args:
            theme: Theme name.
            session_id: If given, only delete entries in this session.
                None = delete across all sessions (destructive).
        """
        if not self._connected:
            return {"deleted": False, "reason": "Not connected"}

        cmd: Dict[str, Any] = {"cmd": "delete", "theme": theme}
        if session_id and self._supports_delete_scope is not False:
            cmd["session_id"] = session_id
        result = self._send(cmd)

        # Compatibility fallback: older binaries reject `session_id` on delete.
        if (
            not result.get("ok")
            and session_id
            and "unknown field" in str(result.get("error", "")).lower()
            and "session_id" in str(result.get("error", "")).lower()
        ):
            self._supports_delete_scope = False
            result = self._send({"cmd": "delete", "theme": theme})

        if result.get("ok"):
            logger.info(f"Contextor: Deleted theme '{theme}'")
            return {"deleted": True, "theme": theme}

        return {"error": result.get("error", "Delete failed")}

    def update_memory(
        self,
        theme: str,
        content: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Replace the active memory for a theme, archiving the old entry as a memento.

        Empty content forgets the theme — no new active entry is created and
        search_memory will no longer return anything for this theme.
        """
        if not self._connected:
            return {"error": "Contextor not connected"}

        vector = self._embed(content) if content else []
        if content and vector is None:
            return {"error": "Embedding failed"}

        result = self._send(
            {
                "cmd": "replace_active",
                "theme": theme,
                "content": content,
                "vector": vector or [],
                "session_id": session_id,
            }
        )

        if result.get("ok"):
            if result.get("forgotten"):
                logger.info(f"Contextor: Forgot theme '{theme}'")
                return {"forgotten": True, "theme": theme}
            logger.info(
                f"Contextor: Updated active memory for '{theme}' "
                f"(archived={result.get('archived', False)})"
            )
            return {
                "updated": True,
                "theme": theme,
                "archived": result.get("archived", False),
            }

        logger.warning(f"Contextor: update_memory failed: {result.get('error')}")
        return {"error": result.get("error", "update_memory failed")}

    def peek_memento(
        self,
        theme: str,
        limit: int = 5,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return the last N archived entries (mementos) for a theme, newest first."""
        if not self._connected:
            return {"theme": theme, "mementos": []}

        result = self._send(
            {
                "cmd": "peek_memento",
                "theme": theme,
                "limit": limit,
                "session_id": session_id,
            }
        )

        if result.get("ok"):
            mementos = result.get("mementos", [])
            logger.info(f"Contextor: Peeked {len(mementos)} memento(s) for '{theme}'")
            return {"theme": theme, "mementos": mementos}

        return {"theme": theme, "mementos": [], "error": result.get("error")}

    def reindex(self) -> Dict[str, Any]:
        """Rebuild the vector index from stored entries."""
        if not self._connected:
            return {"error": "Not connected"}

        result = self._send({"cmd": "reindex"})
        if result.get("ok"):
            logger.info(f"Contextor: Reindexed {result.get('indexed', 0)} entries")
            return {"reindexed": result.get("indexed", 0)}

        return {"error": result.get("error", "Reindex failed")}

    # ------------------------------------------------------------------
    # Sessions (chat conversations)
    # ------------------------------------------------------------------

    def create_session(
        self,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new chat session.

        Args:
            title: Human-readable label.  If None the binary picks a default.
            metadata: Arbitrary JSON metadata stored with the session.

        Returns:
            ``{"session": {"id", "title", "created_at", ...}}`` on success.
            ``{"error": "..."}`` on failure.
        """
        if not self._connected:
            return {"error": "Contextor not connected"}
        if self._supports_sessions is False:
            return {"error": "Session commands unsupported by contextor binary"}

        cmd: Dict[str, Any] = {
            "cmd": "create_session",
            "title": title if title is not None else "",
        }
        if metadata:
            cmd["metadata"] = metadata

        result = self._send(cmd)
        if result.get("ok"):
            session = result.get("session") or {
                "id": result.get("session_id", ""),
                "title": title or "",
                "created_at": result.get("created_at", ""),
            }
            logger.info(
                f"Contextor: Created session id={session.get('id', '?')[:8]} "
                f"title='{session.get('title', '')}'"
            )
            return {"session": session}

        logger.warning(f"Contextor: create_session failed: {result.get('error')}")
        return {"error": result.get("error", "create_session failed")}

    def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List sessions, most-recently-updated first.

        Returns:
            ``{"sessions": [{"id", "title", "created_at", "updated_at",
            "summary", "entry_count"}, ...]}``
        """
        if not self._connected:
            return {"sessions": []}
        if self._supports_sessions is False:
            return {
                "sessions": [],
                "error": "Session commands unsupported by contextor binary",
            }

        result = self._send(
            {
                "cmd": "list_sessions",
                "limit": limit,
                "offset": offset,
            }
        )
        if result.get("ok"):
            sessions = result.get("sessions", [])
            logger.info(f"Contextor: Listed {len(sessions)} session(s)")
            return {"sessions": sessions}

        return {"sessions": [], "error": result.get("error")}

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Fetch a single session's full record."""
        if not self._connected:
            return {"error": "Contextor not connected"}
        if self._supports_sessions is False:
            return {"error": "Session commands unsupported by contextor binary"}

        result = self._send({"cmd": "get_session", "session_id": session_id})
        if result.get("ok"):
            return {"session": result.get("session", {})}

        return {"error": result.get("error", "get_session failed")}

    def update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update a session's title or rolling summary.

        Only provided fields are updated; omitted fields are left alone.
        """
        if not self._connected:
            return {"error": "Contextor not connected"}
        if self._supports_sessions is False:
            return {"error": "Session commands unsupported by contextor binary"}

        cmd: Dict[str, Any] = {"cmd": "update_session", "session_id": session_id}
        if title is not None:
            cmd["title"] = title
        if summary is not None:
            cmd["rolling_summary"] = summary

        result = self._send(cmd)
        if result.get("ok"):
            logger.info(f"Contextor: Updated session {session_id[:8]}")
            return {"session": result.get("session", {})}

        return {"error": result.get("error", "update_session failed")}

    def delete_session(
        self,
        session_id: str,
        delete_entries: bool = True,
    ) -> Dict[str, Any]:
        """Delete a session.

        Args:
            session_id: Which session to delete.
            delete_entries: If True, also delete every memory entry
                belonging to this session.  If False, entries are
                orphaned (session_id set to null, becoming global).
        """
        if not self._connected:
            return {"error": "Contextor not connected"}
        if self._supports_sessions is False:
            return {"error": "Session commands unsupported by contextor binary"}

        result = self._send(
            {
                "cmd": "delete_session",
                "session_id": session_id,
                "delete_entries": delete_entries,
            }
        )
        if result.get("ok"):
            logger.info(f"Contextor: Deleted session {session_id[:8]}")
            return {"deleted": True, "session_id": session_id}

        return {"error": result.get("error", "delete_session failed")}
