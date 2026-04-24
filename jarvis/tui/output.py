"""Transcript/output helpers for the Textual TUI."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import RichLog


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
    # Session might have been auto-created on first message; refresh.
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
