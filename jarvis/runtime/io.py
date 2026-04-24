"""Socket and output-broadcast runtime helpers."""

from __future__ import annotations

import asyncio
import json
import os
from logging import Logger
from typing import Any

from ..config import Config


async def run_socket_listener(app: Any, logger: Logger) -> None:
    """Listen on Unix socket for external text/JSON input."""
    path = Config.JARVIS_INPUT_SOCKET
    if not path:
        return
    sock_dir = os.path.dirname(path)
    os.makedirs(sock_dir, exist_ok=True)
    if os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            pass
    server = await asyncio.start_unix_server(
        lambda r, w: handle_socket_connection(app, logger, r, w),
        path=path,
    )
    try:
        if os.path.exists(path):
            try:
                os.chmod(path, 0o660)
            except OSError:
                pass
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        server.close()
        await server.wait_closed()
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


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
    """Listen on Unix socket for output subscribers (apps/widgets)."""
    path = Config.JARVIS_OUTPUT_SOCKET
    if not path:
        return
    sock_dir = os.path.dirname(path)
    os.makedirs(sock_dir, exist_ok=True)
    if os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            pass
    server = await asyncio.start_unix_server(
        lambda r, w: handle_output_connection(app, logger, r, w),
        path=path,
    )
    try:
        if os.path.exists(path):
            try:
                os.chmod(path, 0o660)
            except OSError:
                pass
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        app._output_clients.clear()
        server.close()
        await server.wait_closed()
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


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
