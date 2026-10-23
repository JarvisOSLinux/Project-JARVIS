"""Session sidebar refresh/selection helpers for the Textual TUI."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from textual.widgets import Label, ListItem, ListView

from . import output as tui_output


def schedule_sidebar_refresh(app: Any) -> None:
    """Rebuild the session list on Textual's message pump."""

    def kick() -> None:
        asyncio.create_task(app._refresh_sidebar())

    try:
        app.call_next(kick)
    except Exception:
        asyncio.create_task(app._refresh_sidebar())


async def refresh_sidebar(app: Any, session_item_cls: Any, logger: Any) -> None:
    """Refresh sidebar session rows while handling races and duplicates."""
    async with app._sidebar_refresh_lock:
        if app.jarvis is None:
            return
        try:
            sessions = app.jarvis.sessions.list(limit=50)
        except Exception as e:
            logger.debug(f"TUI: session list failed: {e}")
            sessions = []

        seen_ids: set[str] = set()
        unique_sessions = []
        for s in sessions:
            if s.id in seen_ids:
                continue
            seen_ids.add(s.id)
            unique_sessions.append(s)
        sessions = unique_sessions

        current_id = app.jarvis.sessions.current_id
        if (
            app._pending_delete_session_id is not None
            and app._pending_delete_session_id not in seen_ids
        ):
            app._pending_delete_session_id = None
        try:
            list_view = app.query_one("#session-list", ListView)
        except Exception:
            return

        await list_view.clear()
        if not sessions:
            await list_view.append(ListItem(Label("[dim](no sessions yet)[/dim]")))
        else:
            for s in sessions:
                item = session_item_cls(s, is_current=(s.id == current_id))
                if item.is_current:
                    item.add_class("-current")
                await list_view.append(item)
        app._update_status()


def show_session(app: Any, session: Any) -> None:
    """Show *session* in the chat: its header and stored messages, or no session.

    The one place the chat changes to another session. `SessionManager`
    announces every switch (`/switch`, `/new`, the sidebar, deleting the
    session you are in, a socket client), so the chat can never show one
    session while JARVIS is in another.
    """
    try:
        from .chat_view import ChatView

        app.query_one("#chat-log", ChatView).clear()
        app._export_lines.clear()
    except Exception:
        pass
    app._pending_delete_session_id = None
    if session is None:
        app._append_log("[dim]— no session; your next message starts a new one —[/dim]")
    else:
        app._append_log(
            f"[dim]— {session.short_id()} "
            f"('{tui_output.escape(session.title or 'untitled')}') —[/dim]"
        )
        render_session_history(app, session.id)
    app.schedule_sidebar_refresh()


def render_session_history(app: Any, session_id: str) -> None:
    """Write a session's stored conversation into the chat, oldest first."""
    if app.jarvis is None:
        return
    ctx = getattr(app.jarvis, "contextor", None)
    if ctx is None or not getattr(ctx, "is_connected", False):
        return
    try:
        result = ctx.recall("conversation_log", limit=100, session_id=session_id)
        entries = result.get("entries", [])
    except Exception:
        return
    if not entries:
        app._append_log("[dim](no messages in this session yet)[/dim]")
        return
    # Sort chronologically — contextor returns most-recent-first by default.
    try:
        entries.sort(key=lambda e: e.get("stored_at", ""))
    except Exception:
        pass
    for entry in entries:
        content = (entry.get("content") or "").strip()
        if not content:
            continue
        meta = entry.get("metadata") or {}
        entry_type = meta.get("type", "")
        escaped = tui_output.escape(content)
        if entry_type == "user_prompt":
            app._append_log(f"[bold cyan]you[/bold cyan] > {escaped}")
        elif entry_type == "assistant_reply":
            app._append_log(f"[bold magenta]jarvis[/bold magenta] > {escaped}")
        else:
            app._append_log(escaped)


async def on_session_selected(app: Any, event: Any) -> None:
    """Handle user selection of a session in the sidebar list."""
    item = event.item
    if not hasattr(item, "session_id") or app.jarvis is None:
        return
    if item.session_id == app.jarvis.sessions.current_id:
        return
    if app.jarvis.sessions.switch(item.session_id) is None:
        app._append_log(f"[red]Could not switch to {item.session_id[:8]}[/red]")


def get_delete_target_session(app: Any) -> Optional[Any]:
    """Prefer highlighted sidebar session; fall back to current session."""
    try:
        list_view = app.query_one("#session-list", ListView)
        highlighted = list_view.highlighted_child
    except Exception:
        highlighted = None

    if hasattr(highlighted, "session_id") and app.jarvis is not None:
        sessions = app.jarvis.sessions.list(limit=500)
        for s in sessions:
            if s.id == highlighted.session_id:
                return s
        return None
    if app.jarvis is not None:
        return app.jarvis.sessions.current
    return None
