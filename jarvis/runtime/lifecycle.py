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
from .io import (
    apply_confirmation_decision,
    broadcast_to_gui_clients,
    enrich_pending_with_goals,
    set_gui_state,
)
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


# Delay before the first registry sweep. Startup already spends its budget on
# connecting dispatch and syncing the tool index; drift is hours-old news by
# definition, so it waits until the daemon is answering the user.
UPDATE_SWEEP_FIRST_DELAY = 60.0


async def update_sweep_once(app: Any, logger: Logger, reported: set[str]) -> set[str]:
    """Refresh the adapter's drift/revocation cache once, returning what it named.

    ``reported`` is the previous pass's notable set: warnings fire on change, not
    on every sweep, so a revoked server that nobody has uninstalled yet does not
    turn the log into a wall of the same line every six hours.
    """
    try:
        rows = await app.dispatch.check_updates()
    except Exception as e:
        logger.debug(f"JARVIS: registry update sweep failed (non-fatal): {e}")
        return reported

    cache = {row["id"]: row for row in rows if isinstance(row, dict) and row.get("id")}
    app.dispatch.update_cache = cache

    notable = set()
    for sid, row in cache.items():
        if row.get("trust_status") == "removed":
            notable.add(f"{sid} REVOKED")
        elif row.get("update_available"):
            notable.add(f"{sid} update available")

    if notable and notable != reported:
        logger.warning("JARVIS: MCP registry sweep — " + ", ".join(sorted(notable)))
    return notable


async def run_update_sweep(app: Any, logger: Logger) -> None:
    """Poll dmcp for registry drift and revocation on a slow loop (#39).

    Without it a revoked server is only ever discovered by the call that fails
    on it — too late for dispatch to refuse the call in the first place.
    """
    interval = Config.UPDATE_CHECK_INTERVAL_MIN * 60
    if interval <= 0:
        return

    reported: set[str] = set()
    delay = UPDATE_SWEEP_FIRST_DELAY
    while True:
        await asyncio.sleep(delay)
        delay = interval
        reported = await update_sweep_once(app, logger, reported)


def _broadcast_confirmation_notice(app: Any, message: dict[str, Any]) -> None:
    """Forward a confirmation notification to whichever socket(s) have
    clients connected, plus a refreshed pending list for GUI clients so an
    already-open Permission Requests view updates the moment a new one
    arrives, not just when one gets resolved.

    The list goes through enrich_pending_with_goals so this emitter carries the
    same goal_description (#192) as the other two confirmation_list emitters
    (io.py's list_confirmations query, root_handlers' resolution broadcast).
    This is the create-time broadcast — the primary way an open view is
    populated — so without it the field would appear only after some unrelated
    re-query.
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
                    "confirmations": enrich_pending_with_goals(app),
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


def apply_queued_confirmation_decisions(app: Any, logger: Logger) -> int:
    """Replay decisions taken through `jarvis confirm` while the daemon was off.

    Each queued message goes through `apply_confirmation_decision`, the same
    translation a live socket client's message gets, so per-task indices,
    approve-all expansion, the GUI broadcast and the #146 goal-gone resume
    logging all behave identically. Ids that are unknown or already resolved
    fall out in `resolve()` as a warn-and-skip, which is the wanted outcome.

    Trust: the queue file is protected by the same same-user filesystem
    boundary the local socket model relies on, and is trusted exactly that far
    — no further. On Windows the file's inherited ACL is the analogue.

    The file is dropped only after every decision has been injected. Re-running
    a consumed decision is a harmless warn-and-skip, so a crash mid-apply costs
    at most a repeated no-op.
    """
    from ..core.confirmation_manager import (
        clear_decision_queue,
        decision_queue_path,
        load_decision_queue,
    )

    path = decision_queue_path()
    decisions = load_decision_queue(path, quarantine=True)
    if not decisions:
        clear_decision_queue(path)
        return 0

    applied = 0
    for decision in decisions:
        if apply_confirmation_decision(app, decision) is None:
            logger.warning(
                "JARVIS: Ignoring unrecognised queued decision %r", decision.get("type")
            )
            continue
        applied += 1

    clear_decision_queue(path)
    logger.info(
        "JARVIS: applied %d queued confirmation decision(s) from offline review",
        applied,
    )
    return applied


async def start_runtime_services(app: Any, logger: Logger) -> dict[str, Any]:
    """Start event sources and optional socket/voice runtime services."""
    user_source = resolve_user_source(app)
    app.events.start(
        signal_window_source=app.dispatch.get_signal_window,
        user_source=user_source,
    )
    # Route signals PUSHED by dispatch (notifications/message) into the same
    # merged event queue as polled signals, so ROOT is woken on task completion
    # without waiting for a poll (#26). A single signal becomes one turn; a
    # merged fire_wake=false batch becomes one turn for the group (#189).
    # Deduplicated against the poll fallback.
    app.dispatch.set_signal_sink(app.events.enqueue_pushed)

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

    # Offline review catches up here: the merged queue is running and the
    # injector is wired, so a decision queued with the daemon down resolves on
    # the first turns of this run — whether or not any socket is enabled.
    apply_queued_confirmation_decisions(app, logger)

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
        app.output_manager.add_activity_callback(app._on_gui_activity)
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
            has_interactive_clients=lambda: any(
                "confirmations" in caps and writer in app._gui_clients
                for writer, caps in app._gui_capabilities.items()
            ),
        )

    update_sweep_task: Optional[asyncio.Task] = None
    if Config.UPDATE_CHECK_INTERVAL_MIN > 0:
        update_sweep_task = asyncio.create_task(run_update_sweep(app, logger))
        logger.info(
            "JARVIS: Registry update sweep every "
            f"{Config.UPDATE_CHECK_INTERVAL_MIN}min"
        )

    # Opt-in, off by default -- see jarvis/server/openai_compat.py's
    # module docstring for why this is the one deliberate exception to
    # JARVIS having no TCP listener.
    openai_server = None
    if Config.OPENAI_SERVER_ENABLED:
        from ..server.openai_compat import OpenAICompatServer

        openai_server = OpenAICompatServer(app.llm.provider)
        openai_server.start()

    return {
        "input_socket": input_socket_task,
        "output_socket": output_socket_task,
        "gui_socket": gui_socket_task,
        "update_sweep": update_sweep_task,
        "voice_thread": voice_thread,
        "openai_server": openai_server,
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


def stop_openai_server_if_running(server: Optional[Any]) -> None:
    """Stop the opt-in OpenAI-compat server if it was started."""
    if server is not None and server.is_running:
        server.stop()


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
    app.output_manager.remove_activity_callback(app._on_gui_activity)
    await app.events.stop()
    await app.dispatch.disconnect()
    if app.contextor:
        app.contextor.disconnect()
    logger.info("JARVIS: Shutdown complete")
