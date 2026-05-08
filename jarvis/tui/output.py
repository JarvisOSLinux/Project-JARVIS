"""Transcript/output helpers for the Textual TUI."""

from __future__ import annotations

import re
from typing import Any

from rich.text import Text
from textual.widgets import RichLog

_DEFAULT_TITLE_RE = re.compile(r"^Chat \d{4}-\d{2}-\d{2}")


def markup_to_plain(markup: str) -> str:
    """Strip Rich markup for export / plain transcript buffer."""
    try:
        return Text.from_markup(markup, emoji=False).plain
    except Exception:
        return markup


def escape(text: str) -> str:
    """Escape Rich markup meta-characters in user/LLM text."""
    return text.replace("[", r"\[")


def on_jarvis_output(app: Any, response: dict[str, Any]) -> None:
    """Output callback called from OutputManager on the main event loop."""
    text = response.get("output", "")
    if not text:
        return
    app._append_log(f"[bold magenta]jarvis[/bold magenta] > {escape(text)}")
    _maybe_autoname_session(app)
    # Session might have been auto-created on first message; refresh.
    app.schedule_sidebar_refresh()


def _maybe_autoname_session(app: Any) -> None:
    """Rename session from timestamp default to first-message summary."""
    pending = getattr(app, "_pending_autoname_text", None)
    if not pending:
        return
    if app.jarvis is None:
        return
    session = app.jarvis.sessions.current
    if session is None:
        return
    if not _DEFAULT_TITLE_RE.match(session.title or ""):
        app._pending_autoname_text = None
        return
    words = pending.strip().split()
    new_title = " ".join(words[:6])
    if len(new_title) > 40:
        new_title = new_title[:37] + "…"
    if not new_title:
        return
    app._pending_autoname_text = None
    app.jarvis.sessions.rename(new_title)
    app.schedule_sidebar_refresh()


def on_jarvis_activity(app: Any, event: dict[str, Any]) -> None:
    """Internal runtime narrative (LLM/dispatch status), not chat content."""
    text = str(event.get("text", "")).strip()
    if not text:
        return
    app._append_log(f"[dim]... {escape(text)}[/dim]")


def append_log(app: Any, markup: str) -> None:
    try:
        chat_log = app.query_one("#chat-log", RichLog)
    except Exception:
        return
    chat_log.write(markup)
    app._export_lines.append(markup_to_plain(markup))
