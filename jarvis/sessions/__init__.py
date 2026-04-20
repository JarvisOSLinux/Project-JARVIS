"""
Chat sessions — long-running conversations with their own memory scope.

A *session* is analogous to a Claude Desktop chat: it has a title,
a rolling summary, a creation timestamp, and a stream of messages
that all share the same ``session_id`` when stored in the contextor.

Entries without a ``session_id`` (global) transcend every session —
these are facts the user wants to carry across conversations.

Session metadata lives in the Rust contextor binary (SQLite).  JARVIS
just keeps a pointer to the *current* session in ``SessionManager``.
"""

from .manager import SessionManager
from .model import Session

__all__ = ["Session", "SessionManager"]
