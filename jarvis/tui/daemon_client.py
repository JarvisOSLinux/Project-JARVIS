"""Async client for the daemon's GUI socket, and the helper that starts the daemon.

The TUI is a client of the daemon (#236): it talks the same JSON-lines protocol
as the desktop app, treats a missing daemon as a state rather than an error,
and keeps retrying so a daemon started elsewhere is picked up.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Tuple, Union

from ..config import Config
from ..core.logger import get_logger
from ..platform import current as platform

logger = get_logger(__name__)

CLIENT_LABEL = "jarvis-tui"
RETRY_SECONDS = 2.0
START_TIMEOUT_SECONDS = 20.0
_LOG_TAIL_BYTES = 2000

Callback = Callable[..., Union[None, Awaitable[None]]]


def find_gui_endpoint() -> Optional[str]:
    """The GUI socket path if a daemon has left one, else None."""
    path = Config.JARVIS_GUI_SOCKET
    if path and (os.path.exists(path) or os.path.exists(path + ".port")):
        return path
    return None


async def _call(callback: Callback, *args: Any) -> None:
    result = callback(*args)
    if inspect.isawaitable(result):
        await result


class DaemonClient:
    """Keeps one connection to the daemon, reconnecting until stopped.

    ``on_message(msg)`` gets every protocol message. ``on_connection(up, why)``
    fires on each transition, with a human-readable reason for the TUI banner.
    """

    def __init__(
        self,
        on_message: Callback,
        on_connection: Callback,
        retry_seconds: float = RETRY_SECONDS,
    ) -> None:
        self._on_message = on_message
        self._on_connection = on_connection
        self._retry = retry_seconds
        self._writer: Optional[asyncio.StreamWriter] = None
        self._stopping = False
        self._up: Optional[bool] = None

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def send(self, msg: dict) -> bool:
        writer = self._writer
        if writer is None:
            return False
        try:
            writer.write((json.dumps(msg) + "\n").encode("utf-8"))
            await writer.drain()
        except (ConnectionError, OSError):
            return False
        return True

    def stop(self) -> None:
        self._stopping = True
        writer = self._writer
        if writer is not None:
            writer.close()

    async def run(self) -> None:
        while not self._stopping:
            reason = await self._session()
            await self._transition(False, reason)
            if not self._stopping:
                await asyncio.sleep(self._retry)

    async def _transition(self, up: bool, reason: str) -> None:
        if self._up is up:
            return
        self._up = up
        await _call(self._on_connection, up, reason)

    async def _session(self) -> str:
        path = find_gui_endpoint()
        if path is None:
            return "daemon offline"
        if not platform.ipc_verify_owner(path):
            return "IPC endpoint is not owned by this user; refusing to connect"
        try:
            sock = await asyncio.to_thread(platform.ipc_connect, path)
            reader, writer = await asyncio.open_connection(sock=sock)
        except (OSError, ValueError):
            return "daemon offline"

        self._writer = writer
        try:
            await self.send({"type": "hello", "label": CLIENT_LABEL})
            await self._transition(True, "connected")
            await self._read(reader)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            self._writer = None
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        return "daemon disconnected"

    async def _read(self, reader: asyncio.StreamReader) -> None:
        while not self._stopping:
            line = await reader.readline()
            if not line:
                return
            try:
                msg = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "ping":
                await self.send({"type": "pong"})
                continue
            await _call(self._on_message, msg)


def daemon_log_path() -> Path:
    return Path(Config.JARVIS_DATA_DIR) / "daemon.log"


def _spawn_detached(log_file: Any) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "jarvis.main", "run"]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": log_file,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def _log_tail(path: Path, start: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read()[-_LOG_TAIL_BYTES:]
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace").strip()


async def start_daemon(
    timeout: float = START_TIMEOUT_SECONDS,
    spawn: Callable[[Any], subprocess.Popen] = _spawn_detached,
) -> Tuple[bool, str]:
    """Launch the daemon detached and wait for its GUI endpoint.

    Returns ``(True, "")`` once the endpoint exists. On failure the second
    element is the daemon's own output, not a generic message.
    """
    if find_gui_endpoint() is not None:
        return True, ""

    log = daemon_log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    start = log.stat().st_size if log.exists() else 0
    with open(log, "ab") as log_file:
        proc = spawn(log_file)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if find_gui_endpoint() is not None:
            return True, ""
        if proc.poll() is not None:
            tail = _log_tail(log, start)
            return False, tail or f"daemon exited with status {proc.returncode}"
        await asyncio.sleep(0.2)
    return False, (
        _log_tail(log, start) or f"daemon did not open its socket within {timeout:.0f}s"
    )
