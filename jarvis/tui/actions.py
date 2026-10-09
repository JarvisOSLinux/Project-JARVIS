"""Keybinding / menu actions for the Textual TUI (separate from App wiring)."""

from __future__ import annotations

from typing import Any

from textual.widgets import Input, RichLog

from .help_screen import HelpScreen
from .local_input import export_transcript_to_disk
from .session_sidebar import get_delete_target_session
from .slash_commands_doc import build_help_markdown


async def new_session(app: Any) -> None:
    if not app.connected:
        app._append_log("[yellow]daemon offline — /start to launch it[/yellow]")
        return
    app._pending_delete_session_id = None
    await app.client.send({"type": "create_session"})


async def delete_selected_session(app: Any) -> None:
    """Delete the highlighted (or current) session with two-step confirm."""
    if not app.connected:
        app._append_log("[yellow]daemon offline — /start to launch it[/yellow]")
        return
    target = get_delete_target_session(app)
    if target is None:
        app._append_log("[yellow]No session selected to delete.[/yellow]")
        return

    if app._pending_delete_session_id != target.id:
        app._pending_delete_session_id = target.id
        app._append_log(
            f"[yellow]Press Ctrl+D again to delete {target.short_id()} "
            f"('{target.title or 'untitled'}').[/yellow]"
        )
        return

    app._pending_delete_session_id = None
    await app.client.send({"type": "delete_session", "id": target.id})
    app._append_log(f"[dim]— deleting session {target.short_id()} —[/dim]")


def focus_chat(app: Any) -> None:
    """Move focus to the transcript for keyboard scrolling (arrows / PgUp)."""
    try:
        app.query_one("#chat-log", RichLog).focus()
    except Exception:
        pass


def focus_input(app: Any) -> None:
    """Move focus back to the message line."""
    try:
        app.query_one("#input", Input).focus()
    except Exception:
        pass


def open_help(app: Any, bindings: Any) -> None:
    """Open the help modal (Esc / F1 closes while help is focused)."""
    app.push_screen(HelpScreen(build_help_markdown(bindings)))


def clear_transcript(app: Any) -> None:
    """Clear the RichLog and export buffer only (does not touch contextor)."""
    try:
        log = app.query_one("#chat-log", RichLog)
        log.clear()
    except Exception:
        return
    app._export_lines.clear()
    app._append_log(
        "[dim]Transcript cleared (on-screen only; session memory unchanged.)[/dim]"
    )


def export_transcript(app: Any) -> None:
    """Write ``_export_lines`` to ``JARVIS_DATA_DIR/transcripts/``."""
    export_transcript_to_disk(app, None)
