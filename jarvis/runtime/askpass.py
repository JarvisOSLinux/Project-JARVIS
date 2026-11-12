"""The sudo password, asked in jarvis-app and on phones (#310).

With ``SUDO_PASSWORD_POPUP`` on, a root call elevates through ``sudo -A``
(dmcp's choice, made by asking `askpass_helper` ``--check`` before each
elevation) instead of pkexec, whose dialog only shows on the host's desktop.
sudo runs the helper; the helper asks this relay over ``askpass.sock``; the
relay sends ``password_request`` to the GUI clients that subscribed to it
(jarvis-app, and through `jarvis web` the owner's and trusted phones) and
hands the first answer to that helper alone, which gives it to sudo. The
password is not logged, stored, put in the session, or sent anywhere else.

Who may ask: anything running as this user can connect to the socket, an MCP
server JARVIS started included, so a request is shown only when it comes from
the helper this daemon wrote, run by a real sudo (effective uid 0) whose
parent is the dmcp binary JARVIS uses, below this daemon in the process tree.
The popup shows sudo's own command line, read from ``/proc``, not anything
the asker says. What it cannot stop is code that already runs as this user
and reads a process's memory; that is the residual risk #310 named for any
password typed on a phone, and polkit's own desktop dialog has it too.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shlex
import shutil
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import Config
from ..core.logger import get_logger
from . import askpass_helper

logger = get_logger(__name__)

HELPER_NAME = "askpass-helper"
SOCKET_NAME = "askpass.sock"
# Environment the daemon hands to dmcp (directly, and through dispatch).
ENV_HELPER = "DMCP_SUDO_ASKPASS"
ENV_SOCKET = "JARVIS_ASKPASS_SOCKET"
# sudo reads one line; anything longer than this is not a password.
MAX_PASSWORD = 4096
_MAX_DEPTH = 64


def helper_path() -> str:
    return os.path.join(Config.JARVIS_DATA_DIR, HELPER_NAME)


def socket_path() -> str:
    return os.path.join(Config.JARVIS_DATA_DIR, SOCKET_NAME)


def install_helper() -> str:
    """Write the program sudo runs (owner-only), and return its path.

    Rewritten at every start, so it always matches this JARVIS and the Python
    it runs on; ``-I`` keeps the user's environment from changing it.
    """
    path = helper_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = f"#!{sys.executable} -I\n" + Path(askpass_helper.__file__).read_text(
        encoding="utf-8"
    )
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o700)
    os.replace(tmp, path)
    return path


# -- who is asking --------------------------------------------------------


@dataclass
class ProcInfo:
    pid: int
    ppid: int
    comm: str
    euid: int
    cmdline: list[str]
    exe: Optional[str]


def read_proc(pid: int, root: str = "/proc") -> Optional[ProcInfo]:
    """What /proc says about `pid`, or None when it is gone or unreadable."""
    base = os.path.join(root, str(pid))
    try:
        with open(os.path.join(base, "stat"), encoding="utf-8", errors="replace") as f:
            stat = f.read()
        with open(os.path.join(base, "status"), encoding="utf-8") as f:
            status = f.read()
        with open(os.path.join(base, "cmdline"), "rb") as f:
            raw = f.read()
    except OSError:
        return None
    # comm sits in parentheses and may itself hold spaces or ")".
    head, _, rest = stat.rpartition(")")
    comm = head.partition("(")[2]
    fields = rest.split()
    euid = -1
    for line in status.splitlines():
        if line.startswith("Uid:"):
            parts = line.split()
            euid = int(parts[2]) if len(parts) > 2 else -1
    try:
        exe: Optional[str] = os.readlink(os.path.join(base, "exe"))
    except OSError:
        exe = None
    try:
        ppid = int(fields[1])
    except (IndexError, ValueError):
        return None
    args = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    return ProcInfo(pid, ppid, comm, euid, args, exe)


def sudo_command(sudo_args: list[str]) -> list[str]:
    """What a ``sudo -A -- <command…>`` will run, without dmcp's own flags."""
    args = sudo_args[1:]
    if "--" in args:
        args = args[args.index("--") + 1 :]
    else:
        while args and args[0].startswith("-"):
            args = args[1:]
    return [a for a in args if not a.startswith("--dmcp-internal-")]


def vet(
    pid: int,
    *,
    daemon_pid: int,
    helper: str,
    dmcp: Optional[str],
    proc: Callable[[int], Optional[ProcInfo]] = read_proc,
) -> tuple[Optional[str], int, str]:
    """(sudo's command, sudo's pid, "") for a request this relay may show,
    else (None, 0, why not)."""
    asker = proc(pid)
    if asker is None or helper not in asker.cmdline:
        return None, 0, "not JARVIS's askpass helper"
    sudo = proc(asker.ppid)
    if sudo is None or sudo.comm != "sudo" or sudo.euid != 0:
        return None, 0, "the helper's parent is not sudo"
    caller = proc(sudo.ppid)
    if dmcp is None or caller is None or caller.exe is None:
        return None, 0, "cannot tell who ran sudo"
    if os.path.realpath(caller.exe) != os.path.realpath(dmcp):
        return None, 0, f"sudo was run by {caller.exe}, not dmcp"
    step, depth = caller, 0
    while step is not None and step.pid != daemon_pid and depth < _MAX_DEPTH:
        if step.ppid <= 1:
            step = None
            break
        step, depth = proc(step.ppid), depth + 1
    if step is None or step.pid != daemon_pid:
        return None, 0, "dmcp was not started by this JARVIS"
    command = sudo_command(sudo.cmdline)
    if not command:
        return None, 0, "sudo names no command"
    return " ".join(shlex.quote(a) for a in command), sudo.pid, ""


def peer_pid(writer: asyncio.StreamWriter) -> Optional[int]:
    """The connecting process, when it runs as this user."""
    sock = writer.get_extra_info("socket")
    if sock is None or not hasattr(socket, "SO_PEERCRED"):
        return None
    try:
        creds = sock.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
    except OSError:
        return None
    pid, uid, _ = struct.unpack("3i", creds)
    return pid if uid == os.getuid() else None


def password_error(password: Any) -> Optional[str]:
    if not isinstance(password, str) or not password:
        return "Type the password first."
    if len(password) > MAX_PASSWORD or any(c in password for c in "\n\r\0"):
        return "That is not something sudo can take as a password."
    return None


# -- the relay --------------------------------------------------------------


@dataclass
class _Pending:
    request: dict
    answer: asyncio.Future = field(repr=False)


class AskpassRelay:
    """Serve ``askpass.sock``; one request per helper connection."""

    def __init__(
        self,
        broadcast: Callable[[dict], None],
        dmcp: Optional[str] = None,
        daemon_pid: Optional[int] = None,
        proc: Callable[[int], Optional[ProcInfo]] = read_proc,
        peer: Callable[[asyncio.StreamWriter], Optional[int]] = peer_pid,
    ):
        self._broadcast = broadcast
        self._dmcp = dmcp
        self._daemon_pid = daemon_pid if daemon_pid is not None else os.getpid()
        self._proc = proc
        self._peer = peer
        self._pending: dict[str, _Pending] = {}
        # sudo pid -> (requests so far, when last); sudo asks again after a
        # wrong password, and the popup says which try it is.
        self._tries: dict[int, tuple[int, float]] = {}
        self._server: Optional[asyncio.AbstractServer] = None

    @staticmethod
    def enabled() -> bool:
        return bool(getattr(Config, "SUDO_PASSWORD_POPUP", False))

    async def start(self) -> None:
        from ..platform import current as platform

        path = socket_path()
        self._server = await platform.create_ipc_server(path, self.handle)
        if self._dmcp is None:
            self._dmcp = shutil.which(Config.DMCP_BINARY)
        os.environ[ENV_HELPER] = install_helper()
        os.environ[ENV_SOCKET] = path
        logger.info(f"JARVIS: sudo askpass relay at {path}")

    async def stop(self) -> None:
        for item in list(self._pending.values()):
            if not item.answer.done():
                item.answer.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            from ..platform import current as platform

            platform.ipc_cleanup(socket_path())
        os.environ.pop(ENV_HELPER, None)
        os.environ.pop(ENV_SOCKET, None)

    def pending(self) -> list[dict]:
        return [p.request for p in self._pending.values()]

    def answer(self, request_id: Any, password: Any) -> Optional[str]:
        """Hand `password` to the request; the reason when it cannot."""
        item = self._pending.get(str(request_id))
        if item is None or item.answer.done():
            return "That password request has already ended."
        why = password_error(password)
        if why:
            return why
        item.answer.set_result(password)
        return None

    def cancel(self, request_id: Any) -> Optional[str]:
        item = self._pending.get(str(request_id))
        if item is None or item.answer.done():
            return "That password request has already ended."
        item.answer.set_result(None)
        return None

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            msg = json.loads(line) if line else {}
            if not isinstance(msg, dict):
                return
            if msg.get("type") == "check":
                await self._write(writer, {"ready": self.enabled()})
            elif msg.get("type") == "ask":
                await self._ask(reader, writer)
        except (asyncio.TimeoutError, ValueError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _ask(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if not self.enabled():
            await self._write(writer, {"error": "the password popup is off"})
            return
        pid = self._peer(writer)
        command, sudo_pid, why = (
            vet(
                pid,
                daemon_pid=self._daemon_pid,
                helper=helper_path(),
                dmcp=self._dmcp,
                proc=self._proc,
            )
            if pid is not None
            else (None, 0, "unknown peer")
        )
        if command is None:
            logger.warning(f"JARVIS: refused a sudo password request: {why}")
            await self._write(writer, {"error": why})
            return

        now = time.time()
        self._tries = {p: t for p, t in self._tries.items() if now - t[1] < 600}
        tries = self._tries.get(sudo_pid, (0, now))[0] + 1
        self._tries[sudo_pid] = (tries, now)

        request = {
            "type": "password_request",
            "id": secrets.token_hex(8),
            "command": command,
            "attempt": tries,
            "at": now,
        }
        answer = asyncio.get_running_loop().create_future()
        self._pending[request["id"]] = _Pending(request, answer)
        gone = asyncio.ensure_future(reader.read())
        outcome = "gone"
        try:
            self._broadcast(request)
            await asyncio.wait({answer, gone}, return_when=asyncio.FIRST_COMPLETED)
            if answer.done() and not answer.cancelled():
                password = answer.result()
                if password is None:
                    outcome = "cancelled"
                    await self._write(writer, {"cancel": True})
                else:
                    outcome = "answered"
                    await self._write(writer, {"password": password})
        finally:
            gone.cancel()
            self._pending.pop(request["id"], None)
            self._broadcast(
                {
                    "type": "password_request_done",
                    "id": request["id"],
                    "outcome": outcome,
                }
            )
        logger.info(
            f"JARVIS: sudo password request {request['id']} {outcome} "
            f"(try {tries}): {command[:120]}"
        )

    @staticmethod
    async def _write(writer: asyncio.StreamWriter, message: dict) -> None:
        writer.write((json.dumps(message) + "\n").encode("utf-8"))
        await writer.drain()
