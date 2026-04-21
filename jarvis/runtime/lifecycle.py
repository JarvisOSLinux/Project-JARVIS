"""Lifecycle helpers for startup, runtime service wiring, and shutdown."""

from __future__ import annotations

import asyncio
import signal
import sys
import threading
from collections.abc import Callable
from logging import Logger
from typing import Any, Optional

from ..config import Config
from .voice_activation_thread import run_voice_activation


def install_signal_handlers(
    loop: asyncio.AbstractEventLoop, stop_callback: Callable[[], None]
) -> None:
    """Register graceful-stop signal handlers for SIGTERM/SIGINT."""
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_callback)
        except (ValueError, OSError):
            # Some environments (e.g. non-main thread/platform constraints)
            # do not allow adding custom signal handlers.
            pass


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
        if count.get("registry", 0) > 0:
            await dispatch.sync_index()
            logger.info("JARVIS: Tool vector index synced")
        else:
            logger.info(
                "JARVIS: Skipping tool vector sync (no registry servers visible)"
            )
    except Exception as e:
        logger.warning(f"JARVIS: Tool index sync failed (non-fatal): {e}")


def stdin_is_tty() -> bool:
    """True if stdin is a TTY (interactive chat mode)."""
    return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()


def resolve_user_source(app: Any) -> Optional[Callable[[], Any]]:
    """Resolve stdin user source unless TUI owns the terminal input."""
    if app.tui_mode:
        return None
    return app._await_user_input if stdin_is_tty() else None


async def start_runtime_services(
    app: Any, logger: Logger
) -> dict[str, Optional[asyncio.Task]]:
    """Start event sources and optional socket/voice runtime services."""
    user_source = resolve_user_source(app)
    app.events.start(
        signal_source=app._await_dispatch_signal,
        user_source=user_source,
    )

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
        input_socket_task = asyncio.create_task(app._run_socket_listener())
        logger.info(f"JARVIS: Socket listener at {Config.JARVIS_INPUT_SOCKET}")

    # Wire confirmation manager's event injector so responses flow
    # through the event loop instead of blocking.
    app.confirmation.set_event_injector(app.events.inject_confirmation_response)

    output_socket_task: Optional[asyncio.Task] = None
    if Config.JARVIS_OUTPUT_SOCKET:
        app.output_manager.add_output_callback(app._on_output_for_broadcast)
        app.confirmation.set_output_callback(
            app._on_output_for_broadcast,
            has_clients=lambda: len(app._output_clients) > 0,
        )
        output_socket_task = asyncio.create_task(app._run_output_socket_listener())
        logger.info(f"JARVIS: Output socket at {Config.JARVIS_OUTPUT_SOCKET}")

    return {
        "input_socket": input_socket_task,
        "output_socket": output_socket_task,
    }


def cancel_task_if_running(task: Optional[asyncio.Task]) -> None:
    """Cancel task if it exists and has not completed."""
    if task and not task.done():
        task.cancel()


def request_stop(app: Any) -> None:
    """Request graceful shutdown (e.g. from signal handler)."""
    app._running = False
    if app.voice_manager and hasattr(app.voice_manager, "activation"):
        try:
            app.voice_manager.activation.stop_listening()
        except Exception:
            pass
    app.events.request_shutdown()


async def shutdown(app: Any, logger: Logger) -> None:
    """Tear down event sources, sockets, dispatch, and contextor."""
    app._running = False
    app.output_manager.remove_output_callback(app._on_output_for_broadcast)
    await app.events.stop()
    await app.dispatch.disconnect()
    if app.contextor:
        app.contextor.disconnect()
    logger.info("JARVIS: Shutdown complete")
