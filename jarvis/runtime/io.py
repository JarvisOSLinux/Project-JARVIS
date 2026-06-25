"""Socket and output-broadcast runtime helpers."""

from __future__ import annotations

import asyncio
import json
import os
from logging import Logger
from typing import Any

from ..config import Config
from ..platform import current as platform


async def run_socket_listener(app: Any, logger: Logger) -> None:
    """Listen on IPC endpoint for external text/JSON input."""
    path = Config.JARVIS_INPUT_SOCKET
    if not path:
        return
    server = await platform.create_ipc_server(
        path, lambda r, w: handle_socket_connection(app, logger, r, w)
    )
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        server.close()
        await server.wait_closed()
        platform.ipc_cleanup(path)


async def handle_socket_connection(
    app: Any,
    logger: Logger,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle one input socket connection; route lines into the event loop."""
    try:
        while app._running:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            # Check for confirmation responses — inject into event loop.
            if text.startswith("{"):
                try:
                    msg = json.loads(text)
                    if msg.get("type") == "confirmation_response":
                        app.events.inject_confirmation_response(msg)
                        continue
                except json.JSONDecodeError:
                    pass

            app.events.inject_user_input(text)
            logger.info(f"JARVIS: Socket input: {text[:80]}...")
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def on_output_for_broadcast(app: Any, response: dict[str, Any]) -> None:
    """Schedule async broadcast of response to output subscribers."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_to_output_clients(app, response))
    except RuntimeError:
        pass


async def broadcast_to_output_clients(app: Any, response: dict[str, Any]) -> None:
    """Send response JSON line to all connected output clients."""
    line = json.dumps(response, ensure_ascii=False) + "\n"
    data = line.encode("utf-8")
    dead: list[asyncio.StreamWriter] = []
    for writer in app._output_clients:
        try:
            writer.write(data)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            dead.append(writer)
    for writer in dead:
        if writer in app._output_clients:
            app._output_clients.remove(writer)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def run_output_socket_listener(app: Any, logger: Logger) -> None:
    """Listen on IPC endpoint for output subscribers (apps/widgets)."""
    path = Config.JARVIS_OUTPUT_SOCKET
    if not path:
        return
    server = await platform.create_ipc_server(
        path, lambda r, w: handle_output_connection(app, logger, r, w)
    )
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        app._output_clients.clear()
        server.close()
        await server.wait_closed()
        platform.ipc_cleanup(path)


async def handle_output_connection(
    app: Any,
    logger: Logger,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle output subscriber lifecycle."""
    app._output_clients.append(writer)
    logger.info(
        f"JARVIS: Output subscriber connected ({len(app._output_clients)} total)"
    )
    try:
        await reader.read()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        if writer in app._output_clients:
            app._output_clients.remove(writer)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        logger.debug("JARVIS: Output subscriber disconnected")


# ---------------------------------------------------------------------------
# GUI socket — bidirectional structured JSON for desktop apps
# ---------------------------------------------------------------------------


async def run_gui_socket_listener(app: Any, logger: Logger) -> None:
    """Listen on IPC endpoint for GUI app connections (bidirectional)."""
    path = Config.JARVIS_GUI_SOCKET
    if not path:
        return
    server = await platform.create_ipc_server(
        path, lambda r, w: handle_gui_connection(app, logger, r, w)
    )
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        app._gui_clients.clear()
        server.close()
        await server.wait_closed()
        platform.ipc_cleanup(path)


async def handle_gui_connection(
    app: Any,
    logger: Logger,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle a bidirectional GUI client connection."""
    app._gui_clients.add(writer)
    logger.info(f"JARVIS: GUI client connected ({len(app._gui_clients)} total)")

    try:
        await _gui_write(writer, {"type": "state", "state": app._gui_state})
    except Exception:
        pass

    try:
        while app._running:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=60.0)
            except asyncio.TimeoutError:
                try:
                    await _gui_write(writer, {"type": "ping"})
                except Exception:
                    break
                continue

            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue

            await _process_gui_message(app, logger, msg, writer)

    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        app._gui_clients.discard(writer)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        logger.info("JARVIS: GUI client disconnected")


async def _process_gui_message(
    app: Any,
    logger: Logger,
    msg: dict,
    writer: asyncio.StreamWriter,
) -> None:
    """Route an incoming GUI protocol message."""
    msg_type = msg.get("type")

    if msg_type == "message":
        content = msg.get("content", "").strip()
        if content:
            await set_gui_state(app, "processing")
            app.events.inject_user_input(content)
            logger.info(f"JARVIS: GUI input: {content[:80]}...")

    elif msg_type == "confirmation_response":
        app.events.inject_confirmation_response(msg)

    elif msg_type == "start_listening":
        if app.voice_manager and hasattr(app.voice_manager, "activation"):
            app.voice_manager.activation.start_listening()
        await set_gui_state(app, "listening")

    elif msg_type == "stop_listening":
        if app.voice_manager and hasattr(app.voice_manager, "activation"):
            app.voice_manager.activation.stop_listening()
        await set_gui_state(app, "idle")

    elif msg_type == "stop_stream":
        await broadcast_to_gui_clients(
            app, {"type": "response", "content": "", "done": True}
        )
        await set_gui_state(app, "idle")

    elif msg_type == "ping":
        try:
            await _gui_write(writer, {"type": "pong"})
        except Exception:
            pass


async def set_gui_state(app: Any, state: str) -> None:
    """Update tracked GUI state and broadcast to all GUI clients."""
    app._gui_state = state
    await broadcast_to_gui_clients(app, {"type": "state", "state": state})


def on_gui_output(app: Any, response: dict[str, Any]) -> None:
    """Schedule async broadcast of response to GUI clients."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_gui_output_and_idle(app, response))
    except RuntimeError:
        pass


async def _gui_output_and_idle(app: Any, response: dict[str, Any]) -> None:
    """Translate daemon output into GUI protocol and reset state to idle."""
    text = response.get("output", "")
    if text:
        await broadcast_to_gui_clients(
            app, {"type": "response", "content": text, "done": True}
        )
    await set_gui_state(app, "idle")


async def broadcast_to_gui_clients(app: Any, message: dict) -> None:
    """Send a JSON message to all connected GUI clients."""
    if not app._gui_clients:
        return
    data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    dead: set[asyncio.StreamWriter] = set()
    for writer in list(app._gui_clients):
        try:
            writer.write(data)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            dead.add(writer)
    app._gui_clients -= dead


async def _gui_write(writer: asyncio.StreamWriter, message: dict) -> None:
    data = (json.dumps(message) + "\n").encode("utf-8")
    writer.write(data)
    await writer.drain()
