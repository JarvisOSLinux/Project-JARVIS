"""Small Jarvis-facing helpers: activity lines, transcript persistence, embeddings."""

from __future__ import annotations

from typing import Any


def get_embeddings(app: Any):
    """Return the embeddings instance, or None if unavailable."""
    return app._embeddings


def emit_activity(app: Any, text: str, kind: str = "activity") -> None:
    """Emit a concise, user-facing runtime status line."""
    app.output_manager.emit_activity(text=text, kind=kind)


def persist_assistant_turn(app: Any, text: str) -> None:
    """Append assistant-visible text to the session transcript in contextor."""
    if not app.contextor or not text or not str(text).strip():
        return
    sid = app.sessions.current_id
    if not sid:
        return
    app.contextor.auto_store_assistant_reply(
        str(text).strip(),
        session_id=sid,
    )
