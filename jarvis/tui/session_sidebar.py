"""Session sidebar refresh/selection helpers for the Textual TUI."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from textual.widgets import Label, ListItem, ListView


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


async def on_session_selected(app: Any, event: Any) -> None:
    """Handle user selection of a session in the sidebar list."""
    item = event.item
    if not hasattr(item, "session_id") or app.jarvis is None:
        return
    if item.session_id == app.jarvis.sessions.current_id:
        return
    app._pending_delete_session_id = None
    session = app.jarvis.sessions.switch(item.session_id)
    if session is None:
        app._append_log(f"[red]Could not switch to {item.session_id[:8]}[/red]")
        return
    app._append_log(
        f"[dim]— switched to {session.short_id()} "
        f"('{session.title or 'untitled'}') —[/dim]"
    )
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
