"""Transcript/output helpers for the Textual TUI."""

from __future__ import annotations

import re
from typing import Any, Optional

from rich.text import Text

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


def _chat(app: Any) -> Any:
    from .chat_view import ChatView

    try:
        return app.query_one("#chat-log", ChatView)
    except Exception:
        return None


def on_jarvis_output(app: Any, response: dict[str, Any]) -> None:
    """Output callback called from OutputManager on the main event loop.

    A reply to a slash command goes into that command's block; anything else
    JARVIS says closes the block and stands on its own.
    """
    text = response.get("output", "")
    if not text:
        return
    chat = _chat(app)
    if response.get("command") and chat is not None and chat.answering:
        app._append_log(escape(text))
    else:
        if chat is not None:
            chat.end_command()
        app._append_log(f"[bold magenta]jarvis[/bold magenta] > {escape(text)}")
        if not response.get("comment"):
            show_activity(app, None)
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
    """What JARVIS is doing now, on the line above the input, not in the chat.

    The chat keeps the record as action blocks; the activity line only says
    what is happening at this moment (#267).
    """
    text = str(event.get("text", "")).strip()
    if text:
        show_activity(app, text)


def show_activity(app: Any, text: Optional[str]) -> None:
    """Set the activity line; None says what is still running, if anything."""
    from textual.widgets import Static

    if text is None:
        running = [
            a for a in getattr(app, "_running_actions", {}).values() if a == "tool"
        ]
        text = (
            f"{len(running)} task{'s' if len(running) != 1 else ''} still running "
            "(F4 shows them)"
            if running
            else ""
        )
    try:
        line = app.query_one("#activity", Static)
    except Exception:
        return
    line.update(Text(f"⋯ {text}" if text else "", style="dim italic"))
    line.set_class(not text, "-idle")


def on_jarvis_action(app: Any, event: dict[str, Any]) -> None:
    """An action JARVIS took, drawn as a block (runtime/action_events.py)."""
    from .chat_view import export_line

    chat = _chat(app)
    if chat is None:
        return
    running = app._running_actions
    action_id = str(event.get("id") or "")
    if event.get("phase") == "start":
        running[action_id] = event.get("kind")
    elif event.get("phase") == "end":
        running.pop(action_id, None)
    if chat.on_action(event) is not None and event.get("phase") != "update":
        app._export_lines.append(export_line(event))


def append_log(app: Any, markup: str) -> None:
    chat = _chat(app)
    if chat is None:
        return
    chat.write(markup)
    app._export_lines.append(markup_to_plain(markup))
