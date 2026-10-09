"""Daemon protocol messages -> TUI state and widgets (#236).

The TUI holds no engine: everything it shows arrives as a GUI-socket message
(see "GUI socket" in docs/architecture.md) or comes from ``offline`` while no
daemon is running.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from . import output as tui_output
from .confirm_modal import ConfirmModal
from .server_config_modal import ServerConfigModal

OFFLINE_BANNER = "daemon offline — /start to launch it"


async def request_snapshot(app: Any) -> None:
    """Ask the daemon for everything the screen shows.

    The transcript is requested only while the screen holds no conversation,
    so reattaching after a daemon restart does not repeat what is on screen.
    """
    requests = [{"type": "get_status"}, {"type": "list_sessions"}]
    if not app.has_conversation:
        requests.append({"type": "get_history"})
    requests.append({"type": "list_confirmations"})
    for msg in requests:
        await app.client.send(msg)


async def refresh_after_turn(app: Any) -> None:
    for msg in ({"type": "get_status"}, {"type": "list_sessions"}):
        await app.client.send(msg)


async def on_connection(app: Any, up: bool, reason: str) -> None:
    app.connected = up
    if up:
        app.offline_reason = ""
        app._append_log("[green]Connected to the JARVIS daemon.[/green]")
        await app.offline.close()
        await request_snapshot(app)
    else:
        app.offline_reason = OFFLINE_BANNER if "offline" in reason else reason
        app.daemon_state = "idle"
        app._append_log(f"[yellow]{tui_output.escape(app.offline_reason)}[/yellow]")
        app._update_status()
        await load_offline_view(app)
    app._update_status()


async def load_offline_view(app: Any) -> None:
    """Sessions and pending confirmations straight from disk."""
    from . import offline

    app.sessions = await app.offline.sessions()
    app.pending_confirmations = offline.pending_confirmations()
    await app._refresh_sidebar()
    queued = offline.queued_decisions()
    if app.pending_confirmations:
        app._append_log(
            f"[dim]{len(app.pending_confirmations)} confirmation(s) pending"
            f"{f', {len(queued)} decision(s) queued' if queued else ''}; "
            "they are resolved when the daemon starts.[/dim]"
        )


def render_history(
    app: Any, messages: List[Dict[str, Any]], *, replace: bool = True
) -> None:
    """Show a session's stored messages, replacing the transcript unless
    ``replace`` is false (the attach snapshot adds to what is on screen)."""
    if replace:
        app.clear_log()
    if not messages:
        if replace:
            app._append_log("[dim](no messages in this session yet)[/dim]")
        return
    app.has_conversation = True
    for message in messages:
        content = (message.get("content") or "").strip()
        if not content:
            continue
        escaped = tui_output.escape(content)
        if message.get("role") == "user":
            app._append_log(f"[bold cyan]you[/bold cyan] > {escaped}")
        else:
            app._append_log(f"[bold magenta]jarvis[/bold magenta] > {escaped}")


async def on_message(app: Any, msg: Dict[str, Any]) -> None:
    handler = _HANDLERS.get(msg.get("type", ""))
    if handler is not None:
        await handler(app, msg)


async def _response(app: Any, msg: Dict[str, Any]) -> None:
    text = msg.get("content", "")
    if not text:
        return
    app.has_conversation = True
    app._append_log(f"[bold magenta]jarvis[/bold magenta] > {tui_output.escape(text)}")
    await tui_output.maybe_autoname_session(app)
    await refresh_after_turn(app)


async def _state(app: Any, msg: Dict[str, Any]) -> None:
    app.daemon_state = str(msg.get("state", "idle"))
    app._update_status()


async def _status(app: Any, msg: Dict[str, Any]) -> None:
    app.daemon_status = msg
    session = msg.get("session") or {}
    app.current_session_id = session.get("id")
    app._update_status()
    await app._refresh_sidebar()


async def _session_list(app: Any, msg: Dict[str, Any]) -> None:
    app.last_session_error = None
    app.sessions = list(msg.get("sessions") or [])
    await app._refresh_sidebar()


async def _session_switched(app: Any, msg: Dict[str, Any]) -> None:
    session = msg.get("session") or {}
    app.current_session_id = session.get("id")
    render_history(app, msg.get("messages") or [])
    await refresh_after_turn(app)


async def _history(app: Any, msg: Dict[str, Any]) -> None:
    render_history(app, msg.get("messages") or [], replace=False)


async def _session_error(app: Any, msg: Dict[str, Any]) -> None:
    """Show a session error once: with memory disabled, every list/history
    request fails the same way and would otherwise repeat on each turn."""
    text = str(msg.get("message", ""))
    if text == app.last_session_error:
        return
    app.last_session_error = text
    app._append_log(f"[red]{tui_output.escape(text)}[/red]")


async def _activity(app: Any, msg: Dict[str, Any]) -> None:
    text = str(msg.get("text", "")).strip()
    if text:
        app._append_log(f"[dim]... {tui_output.escape(text)}[/dim]")


async def _shutdown(app: Any, msg: Dict[str, Any]) -> None:
    app._append_log("[yellow]The daemon is shutting down.[/yellow]")


async def _confirmation_list(app: Any, msg: Dict[str, Any]) -> None:
    app.pending_confirmations = list(msg.get("confirmations") or [])
    live = {c.get("id") for c in app.pending_confirmations}
    for request_id in [r for r in app.confirm_screens if r not in live]:
        screen = app.confirm_screens.pop(request_id)
        screen.dismiss(None)
    app._update_status()


async def _confirmation_request(app: Any, msg: Dict[str, Any]) -> None:
    request_id = str(msg.get("id", ""))
    if not request_id or request_id in app.confirm_screens:
        return
    commands = [
        str(d.get("command") or d.get("tool", "")) for d in msg.get("details", [])
    ]
    screen = ConfirmModal(request_id, commands)
    app.confirm_screens[request_id] = screen

    def answered(approved: Any) -> None:
        app.confirm_screens.pop(request_id, None)
        if approved is None:
            return
        kind = "approve_confirmation" if approved else "deny_confirmation"
        asyncio.ensure_future(app.client.send({"type": kind, "id": request_id}))

    app.push_screen(screen, answered)


async def _config_request(app: Any, msg: Dict[str, Any]) -> None:
    request_id = msg.get("request_id")
    future: asyncio.Future = asyncio.get_running_loop().create_future()

    def answered(done: asyncio.Future) -> None:
        result = done.result()
        asyncio.ensure_future(
            app.client.send(
                {
                    "type": "config_response",
                    "request_id": request_id,
                    "confirmed": bool(result.confirmed),
                    "values": dict(result.values or {}),
                    "missing_required": list(result.missing_required or []),
                }
            )
        )

    future.add_done_callback(answered)
    await app.push_screen(
        ServerConfigModal(
            msg.get("server_id", ""),
            msg.get("name", ""),
            msg.get("description", ""),
            msg.get("props") or [],
            msg.get("saved") or {},
            future,
        )
    )


_HANDLERS = {
    "response": _response,
    "state": _state,
    "status": _status,
    "session_list": _session_list,
    "session_switched": _session_switched,
    "session_error": _session_error,
    "history": _history,
    "activity": _activity,
    "DAEMON_SHUTDOWN": _shutdown,
    "confirmation_list": _confirmation_list,
    "confirmation_request": _confirmation_request,
    "config_request": _config_request,
}
