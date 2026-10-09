"""Transcript/output helpers for the Textual TUI."""

from __future__ import annotations

import re
from typing import Any

from rich.text import Text
from textual.widgets import RichLog

_DEFAULT_TITLE_RE = re.compile(r"^(New chat|Chat \d{4}-\d{2}-\d{2})", re.IGNORECASE)


def markup_to_plain(markup: str) -> str:
    """Strip Rich markup for export / plain transcript buffer."""
    try:
        return Text.from_markup(markup, emoji=False).plain
    except Exception:
        return markup


def escape(text: str) -> str:
    """Escape Rich markup meta-characters in user/LLM text."""
    return text.replace("[", r"\[")


async def maybe_autoname_session(app: Any) -> None:
    """Rename a timestamp-titled session after the first message's opening words."""
    pending = getattr(app, "_pending_autoname_text", None)
    if not pending or not app.connected:
        return
    session = (app.daemon_status or {}).get("session")
    if not session:
        return
    if not _DEFAULT_TITLE_RE.match(session.get("title") or ""):
        app._pending_autoname_text = None
        return
    words = pending.strip().split()
    new_title = " ".join(words[:6])
    if len(new_title) > 40:
        new_title = new_title[:37] + "…"
    if not new_title:
        return
    app._pending_autoname_text = None
    await app.client.send(
        {"type": "rename_session", "id": session["id"], "title": new_title}
    )


def append_log(app: Any, markup: str) -> None:
    try:
        chat_log = app.query_one("#chat-log", RichLog)
    except Exception:
        return
    chat_log.write(markup)
    app._export_lines.append(markup_to_plain(markup))
