"""What the TUI can show with no daemon running (#236).

Sessions and their history live on disk in contextor's store, and pending
confirmations live in ``confirmations.json``. Both are readable without the
daemon, and a decision taken here goes to the offline queue the daemon replays
at its next start, through the same functions ``jarvis confirm`` uses.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from ..core.confirmation_manager import (
    decision_queue_path,
    load_decision_queue,
    load_pending_summaries,
    queue_decision,
    store_path,
)
from ..core.logger import get_logger
from ..runtime.io import SESSION_HISTORY_LIMIT, entries_to_messages

logger = get_logger(__name__)


class OfflineStore:
    """Read-only view of persisted state. The contextor binary is started on
    first use and stopped by ``close()``, which the TUI calls the moment the
    daemon attaches so two processes never share the store for long."""

    def __init__(self) -> None:
        self._contextor: Optional[Any] = None

    def _open(self) -> Optional[Any]:
        if self._contextor is not None:
            return self._contextor
        try:
            from ..contextor.adapter import ContextorAdapter

            adapter = ContextorAdapter(embeddings=None)
            if adapter.connect():
                self._contextor = adapter
        except Exception as e:
            logger.debug(f"TUI: offline contextor unavailable: {e}")
        return self._contextor

    def _sessions_sync(self, limit: int) -> List[Dict[str, Any]]:
        contextor = self._open()
        if contextor is None or not contextor.supports_sessions:
            return []
        from ..sessions.manager import SessionManager

        return [s.to_dict() for s in SessionManager(contextor).list(limit=limit)]

    def _history_sync(self, session_id: str) -> List[Dict[str, Any]]:
        contextor = self._open()
        if contextor is None:
            return []
        result = contextor.recall(
            "conversation_log", limit=SESSION_HISTORY_LIMIT, session_id=session_id
        )
        return entries_to_messages(result.get("entries", []))

    async def sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._sessions_sync, limit)

    async def history(self, session_id: str) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._history_sync, session_id)

    async def close(self) -> None:
        contextor, self._contextor = self._contextor, None
        if contextor is not None:
            await asyncio.to_thread(contextor.disconnect)


def pending_confirmations() -> List[Dict[str, Any]]:
    """Pending confirmations as the daemon last persisted them."""
    return load_pending_summaries(store_path())


def queued_decisions() -> List[Dict[str, Any]]:
    return load_decision_queue(decision_queue_path())


def queue_offline_decision(decision: Dict[str, Any]) -> bool:
    """Queue an approve/deny message for the daemon's next start. Returns
    True if it replaced an earlier decision for the same id."""
    return queue_decision(decision, decision_queue_path())
