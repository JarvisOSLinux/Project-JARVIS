"""Keybinding / menu actions for the Textual TUI (separate from App wiring)."""

from __future__ import annotations

from typing import Any

from textual.widgets import Input, RichLog

from .help_screen import HelpScreen
from .local_input import export_transcript_to_disk
from .session_sidebar import get_delete_target_session
from .slash_commands_doc import build_help_markdown


async def new_session(app: Any) -> None:
    if app.jarvis is None:
        return
    if not app.jarvis.sessions.available:
        app._append_log("[yellow]Memory is disabled — sessions unavailable.[/yellow]")
        return
    session = app.jarvis.sessions.new_session()
    if session:
        app._pending_delete_session_id = None
        app._append_log(f"[dim]— started new session {session.short_id()} —[/dim]")
        await app._refresh_sidebar()
    else:
        app._append_log("[red]Could not create a new session.[/red]")


async def delete_selected_session(app: Any) -> None:
    """Delete the highlighted (or current) session with two-step confirm."""
    if app.jarvis is None:
        return
    if not app.jarvis.sessions.available:
        app._append_log("[yellow]Memory is disabled — sessions unavailable.[/yellow]")
        return
    target = get_delete_target_session(app)
    if target is None:
        app._append_log("[yellow]No session selected to delete.[/yellow]")
        return

    sid = target.id
    if app._pending_delete_session_id != sid:
        app._pending_delete_session_id = sid
        app._append_log(
            f"[yellow]Press Ctrl+D again to delete {target.short_id()} "
            f"('{target.title or 'untitled'}').[/yellow]"
        )
        return

    app._pending_delete_session_id = None
    if app.jarvis.sessions.delete(sid):
        app._append_log(f"[dim]— deleted session {target.short_id()} —[/dim]")
        await app._refresh_sidebar()
    else:
        app._append_log(f"[red]Delete failed for {target.short_id()}.[/red]")


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
