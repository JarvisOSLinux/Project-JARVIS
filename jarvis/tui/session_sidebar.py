"""Session sidebar refresh/selection helpers for the Textual TUI."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from textual.widgets import Label, ListItem, ListView

from ..sessions.model import Session


def schedule_sidebar_refresh(app: Any) -> None:
    """Rebuild the session list on Textual's message pump."""

    def kick() -> None:
        asyncio.create_task(app._refresh_sidebar())

    try:
        app.call_next(kick)
    except Exception:
        asyncio.create_task(app._refresh_sidebar())


async def refresh_sidebar(app: Any, session_item_cls: Any) -> None:
    """Rebuild the session rows from ``app.sessions``, dropping duplicates."""
    async with app._sidebar_refresh_lock:
        seen_ids: set[str] = set()
        sessions = []
        for data in app.sessions:
            if data.get("id") in seen_ids:
                continue
            seen_ids.add(data.get("id"))
            sessions.append(Session.from_dict(data))

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
                item = session_item_cls(s, is_current=(s.id == app.current_session_id))
                if item.is_current:
                    item.add_class("-current")
                await list_view.append(item)


async def on_session_selected(app: Any, event: Any) -> None:
    """Handle user selection of a session in the sidebar list."""
    item = event.item
    if not hasattr(item, "session_id"):
        return
    if item.session_id == app.current_session_id:
        return
    app._pending_delete_session_id = None
    if app.connected:
        await app.client.send({"type": "switch_session", "id": item.session_id})
        return

    from .messages import render_history

    app.current_session_id = item.session_id
    render_history(app, await app.offline.history(item.session_id))
    await app._refresh_sidebar()


def get_delete_target_session(app: Any) -> Optional[Session]:
    """Prefer the highlighted sidebar session; fall back to the current one."""
    try:
        highlighted = app.query_one("#session-list", ListView).highlighted_child
    except Exception:
        highlighted = None

    target_id = (
        highlighted.session_id
        if hasattr(highlighted, "session_id")
        else app.current_session_id
    )
    for data in app.sessions:
        if data.get("id") == target_id:
            return Session.from_dict(data)
    return None
