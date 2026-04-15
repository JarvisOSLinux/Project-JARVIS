"""
SessionManager — the Python-side controller for chat sessions.

Responsibilities:
  * Track the *current* session id (what JARVIS is actively talking in).
  * Proxy session CRUD to the contextor adapter.
  * Lazily create a default session on first use so users who never
    touch the /sessions machinery still get a coherent history.

All persistent state (session records, rolling summaries, entries)
lives in the Rust contextor binary.  This class is stateless aside
from the current-session pointer.
"""

from typing import List, Optional
from ..core.logger import get_logger
from .model import Session

logger = get_logger(__name__)


class SessionManager:
    """Manages the active chat session and delegates storage to contextor."""

    def __init__(self, contextor):
        """
        Args:
            contextor: A ContextorAdapter instance.  May be None when
                memory is disabled — the manager degrades gracefully
                and exposes ``current_id = None``.
        """
        self._contextor = contextor
        self._current: Optional[Session] = None

    # ------------------------------------------------------------------
    # Current-session pointer
    # ------------------------------------------------------------------

    @property
    def current(self) -> Optional[Session]:
        """The active session, or None if memory is disabled / none started."""
        return self._current

    @property
    def current_id(self) -> Optional[str]:
        """UUID of the active session, or None."""
        return self._current.id if self._current else None

    @property
    def available(self) -> bool:
        """True when contextor is connected (sessions require it)."""
        return (
            self._contextor is not None
            and getattr(self._contextor, "is_connected", False)
            and getattr(self._contextor, "supports_sessions", True)
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def ensure_session(self) -> Optional[Session]:
        """
        Ensure a current session exists — create one if needed.

        Called on first user input so we never write memory entries
        with a null session_id unless the user explicitly asked for
        global storage.

        Returns:
            The current session, or None if memory is disabled.
        """
        if not self.available:
            return None
        if self._current is not None:
            return self._current
        return self.new_session()

    def new_session(self, title: Optional[str] = None) -> Optional[Session]:
        """Create a new session and make it current."""
        if not self.available:
            logger.debug("Sessions: new_session called but contextor unavailable")
            return None

        result = self._contextor.create_session(title=title)
        if "error" in result:
            logger.warning(f"Sessions: create failed: {result['error']}")
            return None

        self._current = Session.from_dict(result["session"])
        logger.info(
            f"Sessions: Created new session {self._current.short_id()} "
            f"('{self._current.title}')"
        )
        return self._current

    def switch(self, session_id: str) -> Optional[Session]:
        """
        Switch to an existing session by id.

        Accepts either a full UUID or a unique prefix (first match wins).
        """
        if not self.available:
            return None

        # Exact fetch first — cheapest path.
        result = self._contextor.get_session(session_id)
        if "error" not in result and result.get("session"):
            self._current = Session.from_dict(result["session"])
            logger.info(f"Sessions: Switched to {self._current.short_id()}")
            return self._current

        # Try prefix match via list_sessions — useful for short ids typed
        # at the CLI.
        all_sessions = self.list(limit=500)
        matches = [s for s in all_sessions if s.id.startswith(session_id)]
        if len(matches) == 1:
            self._current = matches[0]
            logger.info(f"Sessions: Switched to {self._current.short_id()}")
            return self._current
        if len(matches) > 1:
            logger.warning(
                f"Sessions: id prefix '{session_id}' matched {len(matches)} sessions"
            )
        else:
            logger.warning(f"Sessions: No session matches '{session_id}'")
        return None

    def list(self, limit: int = 50, offset: int = 0) -> List[Session]:
        """Return sessions, most-recently-updated first."""
        if not self.available:
            return []
        result = self._contextor.list_sessions(limit=limit, offset=offset)
        return [Session.from_dict(s) for s in result.get("sessions", [])]

    def rename(self, title: str, session_id: Optional[str] = None) -> bool:
        """Rename a session.  Defaults to the current session."""
        if not self.available:
            return False
        sid = session_id or self.current_id
        if not sid:
            return False

        result = self._contextor.update_session(session_id=sid, title=title)
        if "error" in result:
            logger.warning(f"Sessions: rename failed: {result['error']}")
            return False

        # Refresh cached current session if that's what we renamed.
        if self._current and self._current.id == sid:
            self._current = Session.from_dict(result["session"])
        return True

    def delete(self, session_id: str, delete_entries: bool = True) -> bool:
        """Delete a session.  If it's the current one, clear the pointer."""
        if not self.available:
            return False

        result = self._contextor.delete_session(
            session_id=session_id, delete_entries=delete_entries,
        )
        if "error" in result:
            logger.warning(f"Sessions: delete failed: {result['error']}")
            return False

        if self._current and self._current.id == session_id:
            self._current = None
        return True

    # ------------------------------------------------------------------
    # Rolling summary (Tier 2 of tiered-context)
    # ------------------------------------------------------------------

    def save_summary(self, summary: str) -> bool:
        """Persist the rolling summary for the current session."""
        if not self.available or not self._current:
            return False
        result = self._contextor.update_session(
            session_id=self._current.id, summary=summary,
        )
        if "error" in result:
            logger.warning(f"Sessions: save_summary failed: {result['error']}")
            return False
        self._current.summary = summary
        return True

    def load_summary(self) -> str:
        """Return the rolling summary for the current session, or ''."""
        if self._current:
            return self._current.summary or ""
        return ""
