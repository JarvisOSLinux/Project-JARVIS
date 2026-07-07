"""Lifecycle helpers for startup, runtime service wiring, and shutdown."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections.abc import Callable
from logging import Logger
from typing import Any, Dict, Optional

from ..config import Config
from ..core.voice_state import VoiceState
from ..platform import current as platform
from .io import broadcast_to_gui_clients, set_gui_state
from .voice_activation_thread import run_voice_activation


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop, stop_callback: Callable[[], None]
) -> None:
    """Register graceful-stop signal handlers for SIGTERM/SIGINT."""
    platform.install_signal_handlers(loop, stop_callback)


async def connect_dispatch_nonfatal(dispatch: Any, logger: Logger) -> None:
    """Connect to dispatch, but continue in conversation-only mode on failure."""
    try:
        await dispatch.connect()
    except Exception as e:
        logger.warning(f"JARVIS: Could not connect to dispatch: {e}")
        logger.info("JARVIS: Running in conversation-only mode")


async def bootstrap_tool_index_nonfatal(
    dispatch: Any, embeddings: Any, logger: Logger
) -> None:
    """Ensure embedding model and sync tool index when dispatch is available."""
    if not (dispatch.is_connected and embeddings):
        return

    try:
        await dispatch.ensure_embedding_model(embeddings)
        count = await dispatch.server_count()
        # registry count is always 0 through the MCP layer; use total instead.
        total = count.get("total", 0)
        if total > 0:
            await dispatch.sync_index()
            logger.info(f"JARVIS: Tool vector index synced ({total} servers)")
        else:
            logger.info("JARVIS: No servers to index")
    except Exception as e:
        logger.warning(f"JARVIS: Tool index sync failed (non-fatal): {e}")


def _broadcast_confirmation_notice(app: Any, message: dict[str, Any]) -> None:
    """Forward a confirmation notification to whichever socket(s) have
    clients connected, plus a refreshed pending list for GUI clients so an
    already-open Permission Requests view updates the moment a new one
    arrives, not just when one gets resolved.
    """
    if Config.JARVIS_OUTPUT_SOCKET and app._output_clients:
        app._on_output_for_broadcast(message)
    if app._gui_clients:
        asyncio.create_task(broadcast_to_gui_clients(app, message))
        asyncio.create_task(
            broadcast_to_gui_clients(
                app,
                {
                    "type": "confirmation_list",
                    "confirmations": app.confirmation.list_pending(),
                },
            )
        )


def _notify_voice_output_state(app: Any, state: VoiceState) -> None:
    """Bridge OutputManager's synchronous SPEAKING/IDLE callback onto the
    event loop. handle_response() already runs on the loop thread (called
    from async ROOT handlers), so this only needs to schedule a task -- no
    cross-thread handoff, unlike the voice thread's own state broadcasts."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(set_gui_state(app, state))
    except RuntimeError:
        pass


def stdin_is_tty() -> bool:
    """True if stdin is a TTY (interactive chat mode)."""
    return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()


def resolve_user_source(app: Any) -> Optional[Callable[[], Any]]:
    """Resolve stdin user source unless TUI owns the terminal input."""
    if app.tui_mode:
        return None
    return app._await_user_input if stdin_is_tty() else None


async def start_runtime_services(app: Any, logger: Logger) -> dict[str, Any]:
    """Start event sources and optional socket/voice runtime services."""
    user_source = resolve_user_source(app)
    app.events.start(
        signal_window_source=app.dispatch.get_signal_window,
        user_source=user_source,
    )

    app.output_manager.set_state_callback(
        lambda state: _notify_voice_output_state(app, state)
    )

    voice_thread: Optional[threading.Thread] = None
    if app.voice_manager:
        voice_thread = threading.Thread(
            target=run_voice_activation,
            args=(app, logger),
            daemon=True,
            name="jarvis-voice",
        )
        voice_thread.start()
        logger.info("JARVIS: Voice activation started (dual input)")

    input_socket_task: Optional[asyncio.Task] = None
    if Config.JARVIS_INPUT_SOCKET:
        from ..core.socket_security import harden_socket_path, warn_if_allow_all

        harden_socket_path(Config.JARVIS_INPUT_SOCKET)
        warn_if_allow_all(Config.CONFIRMATION_MODE)
        input_socket_task = asyncio.create_task(app._run_socket_listener())
        logger.info(f"JARVIS: Socket listener at {Config.JARVIS_INPUT_SOCKET}")

    # Wire confirmation manager's event injector so responses flow
    # through the event loop instead of blocking.
    app.confirmation.set_event_injector(app.events.inject_confirmation_response)

    output_socket_task: Optional[asyncio.Task] = None
    if Config.JARVIS_OUTPUT_SOCKET:
        app.output_manager.add_output_callback(app._on_output_for_broadcast)
        output_socket_task = asyncio.create_task(app._run_output_socket_listener())
        logger.info(f"JARVIS: Output socket at {Config.JARVIS_OUTPUT_SOCKET}")

    gui_socket_task: Optional[asyncio.Task] = None
    if Config.JARVIS_GUI_SOCKET:
        from ..core.socket_security import harden_socket_path

        harden_socket_path(Config.JARVIS_GUI_SOCKET)
        app.output_manager.add_output_callback(app._on_gui_output)
        gui_socket_task = asyncio.create_task(app._run_gui_socket_listener())
        logger.info(f"JARVIS: GUI socket at {Config.JARVIS_GUI_SOCKET}")

    # Confirmation notifications need to reach whichever socket(s) are
    # actually enabled -- previously this only ever wired to the legacy
    # output socket, so a GUI-only setup (no JARVIS_OUTPUT_SOCKET) never
    # got a confirmation_request at all.
    if Config.JARVIS_OUTPUT_SOCKET or Config.JARVIS_GUI_SOCKET:
        app.confirmation.set_output_callback(
            lambda message: _broadcast_confirmation_notice(app, message),
            has_clients=lambda: bool(app._output_clients) or bool(app._gui_clients),
        )

    return {
        "input_socket": input_socket_task,
        "output_socket": output_socket_task,
        "gui_socket": gui_socket_task,
        "voice_thread": voice_thread,
    }


def cancel_task_if_running(task: Optional[asyncio.Task]) -> None:
    """Cancel task if it exists and has not completed."""
    if task and not task.done():
        task.cancel()


def join_voice_thread_if_running(
    thread: Optional[threading.Thread], timeout: float = 2.0
) -> None:
    """Best-effort join of the voice-activation thread on shutdown (#146).

    request_stop() already flips app._running, which run_voice_activation's
    loop notices within one poll interval and exits via -- this just waits
    for that to actually happen instead of trusting the daemon thread to be
    killed silently whenever the process happens to exit.
    """
    if thread and thread.is_alive():
        thread.join(timeout=timeout)


def request_stop(app: Any) -> None:
    """Request graceful shutdown (e.g. from signal handler or a GUI client's
    shutdown_request -- both funnel through here, so SIGTERM/SIGINT and the
    socket protocol trigger the exact same sequence, per #146)."""
    app._running = False
    if app.voice_manager and hasattr(app.voice_manager, "activation"):
        try:
            app.voice_manager.activation.stop_listening()
        except Exception:
            pass
    app.events.request_shutdown()


def build_shutdown_snapshot(app: Any) -> Dict[str, Any]:
    """Final-state snapshot broadcast on shutdown_request (#146): current GUI
    state, still-unresolved goals, the active session id, and a timestamp."""
    return {
        "state": app._gui_state,
        "goals": [g.to_context() for g in app.goals.get_active_goals()],
        "session_id": app.sessions.current_id if app.sessions else None,
        "timestamp": time.time(),
    }


async def broadcast_shutdown_notice(app: Any, logger: Logger) -> None:
    """Broadcast DAEMON_SHUTDOWN + the final-state snapshot to every
    connected GUI client. Must run before the GUI socket listener task is
    cancelled -- cancelling it clears app._gui_clients in its own cleanup,
    so broadcasting after that point would silently reach nobody.
    """
    if not app._gui_clients:
        return
    logger.info(
        f"JARVIS: Broadcasting shutdown notice to {len(app._gui_clients)} client(s)"
    )
    snapshot = build_shutdown_snapshot(app)
    await broadcast_to_gui_clients(app, {"type": "DAEMON_SHUTDOWN", **snapshot})


async def shutdown(app: Any, logger: Logger) -> None:
    """Tear down event sources, sockets, dispatch, and contextor.

    Goal state is archived unconditionally first -- dismiss_completed/
    dismiss_failed only ever ran for terminal goals, so without this any
    still-PENDING/ACTIVE/DEFERRED goal had zero on-disk trace once the
    process exited (#146).
    """
    app._running = False
    archived = app.goals.archive_all()
    if archived:
        logger.info(f"JARVIS: Archived {len(archived)} in-flight goal(s) on shutdown")
    app.output_manager.remove_output_callback(app._on_output_for_broadcast)
    app.output_manager.remove_output_callback(app._on_gui_output)
    await app.events.stop()
    await app.dispatch.disconnect()
    if app.contextor:
        app.contextor.disconnect()
    logger.info("JARVIS: Shutdown complete")
