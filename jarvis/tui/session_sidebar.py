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


async def _render_placeholder(app: Any, markup: str) -> None:
    """Replace the session list with a single explanatory row."""
    try:
        list_view = app.query_one("#session-list", ListView)
    except Exception:
        return
    await list_view.clear()
    await list_view.append(ListItem(Label(markup)))


async def refresh_sidebar(app: Any, session_item_cls: Any, logger: Any) -> None:
    """Refresh sidebar session rows while handling races and duplicates."""
    async with app._sidebar_refresh_lock:
        manager = app.session_manager()
        if manager is None:
            # Engine-less with no contextor either: say so rather than showing
            # an empty list that looks like "you have no sessions".
            if app.engine_error is not None:
                await _render_placeholder(
                    app, "[dim](no engine — sessions hidden)[/dim]"
                )
            return
        try:
            sessions = manager.list(limit=50)
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

        current_id = app.current_session_id()
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


async def _load_session_history(app: Any, session_id: str) -> None:
    """Load stored conversation entries and render them into the RichLog."""
    ctx = (
        getattr(app.jarvis, "contextor", None)
        if app.jarvis is not None
        else getattr(app, "_offline_contextor", None)
    )
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
    manager = app.session_manager()
    if not hasattr(item, "session_id") or manager is None:
        return
    if item.session_id == app.current_session_id():
        return
    app._pending_delete_session_id = None
    session = manager.switch(item.session_id)
    if session is None:
        app._append_log(f"[red]Could not switch to {item.session_id[:8]}[/red]")
        return
    if app.jarvis is None:
        # No engine owns a "current session", so the panel's session scope
        # follows what the user is browsing instead.
        app._offline_session_id = session.id

    # Clear transcript and reload history for the selected session.
    try:
        from textual.widgets import RichLog

        chat_log = app.query_one("#chat-log", RichLog)
        chat_log.clear()
        app._export_lines.clear()
    except Exception:
        pass

    app._append_log(
        f"[dim]— {session.short_id()} " f"('{session.title or 'untitled'}') —[/dim]"
    )
    await _load_session_history(app, session.id)
    await app._refresh_sidebar()


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
