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
server or a shell command JARVIS started included, and can run the real dmcp
for a command of its own choosing. So a request is shown only when it comes
from the helper this daemon wrote, run by a real sudo (effective uid 0) whose
parent is the dmcp binary JARVIS uses, below this daemon in the process tree,
and that dmcp is running something JARVIS itself started: a task it
dispatched (`expect_tasks`) or a dmcp command it ran (`expect_command`). The
popup shows sudo's own command line and prompt, read from ``/proc``, not
anything the asker says. What it cannot stop is code that already runs as
this user and reads a process's memory; that is the residual risk #310 named
for any password typed on a phone, and polkit's own desktop dialog has it too.

Cancelling, or the dmcp that ran sudo going away (a stopped task, a timeout),
kills that sudo instead of handing it an empty answer, which PAM would count
as a failed try (pam_faillock locks the account after a few).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shlex
import shutil
import signal
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

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
# How long an elevation JARVIS started stays expected. A popup is shown when
# sudo asks, right as the call starts; retries come within minutes.
EXPECT_TTL_S = 30 * 60
_MAX_EXPECTED = 256


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


# -- what JARVIS started ----------------------------------------------------

# key -> monotonic expiry. A key is ("call", server, tool, params as canonical
# JSON) for a dispatched task, or ("argv", *args) for a dmcp command.
_expected: dict[tuple, float] = {}


def _canonical(params: Any) -> str:
    return json.dumps(params or {}, sort_keys=True, separators=(",", ":"))


def _remember(key: tuple) -> tuple:
    now = time.monotonic()
    for k in [k for k, until in _expected.items() if until <= now]:
        del _expected[k]
    while len(_expected) >= _MAX_EXPECTED:
        del _expected[min(_expected, key=_expected.__getitem__)]
    _expected[key] = now + EXPECT_TTL_S
    return key


def expect_tasks(tasks: Iterable[Any]) -> None:
    """Tasks about to go to dispatch, each a `dmcp call` that may elevate."""
    for task in tasks:
        if isinstance(task, dict):
            _remember(
                (
                    "call",
                    str(task.get("server")),
                    str(task.get("tool")),
                    _canonical(task.get("params")),
                )
            )


def expect_command(args: Iterable[str]) -> tuple:
    """A dmcp command JARVIS is about to run; `forget` it once it ends."""
    return _remember(("argv", *[str(a) for a in args]))


def forget(key: tuple) -> None:
    _expected.pop(key, None)


def started_by_jarvis(cmdline: list[str]) -> bool:
    """Whether the dmcp running `cmdline` runs something JARVIS started."""
    args = [a for a in cmdline[1:] if not a.startswith("--dmcp-internal-")]
    now = time.monotonic()
    if args[:1] == ["call"] and len(args) >= 3:
        params: Any = {}
        if "--args" in args:
            at = args.index("--args")
            try:
                params = json.loads(args[at + 1])
            except (IndexError, ValueError):
                return False
        key: tuple = ("call", args[1], args[2], _canonical(params))
    else:
        key = ("argv", *args)
    return _expected.get(key, 0) > now


# -- who is asking --------------------------------------------------------


@dataclass
class ProcInfo:
    pid: int
    ppid: int
    comm: str
    euid: int
    cmdline: list[str]
    exe: Optional[str]
    start: int = 0  # clock ticks after boot: tells a reused pid apart


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
        start = int(fields[19])
    except (IndexError, ValueError):
        return None
    args = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    return ProcInfo(pid, ppid, comm, euid, args, exe, start)


def sudo_command(sudo_args: list[str]) -> list[str]:
    """What a ``sudo -A -- <command…>`` will run, without dmcp's own flags."""
    args = sudo_args[1:]
    if "--" in args:
        args = args[args.index("--") + 1 :]
    else:
        while args and args[0].startswith("-"):
            args = args[1:]
    return [a for a in args if not a.startswith("--dmcp-internal-")]


@dataclass
class Vetted:
    command: str  # what sudo will run, as a shell would quote it
    prompt: str  # what sudo asks, the helper's argument
    sudo: ProcInfo
    caller: ProcInfo  # the dmcp that ran sudo


def vet(
    pid: int,
    *,
    daemon_pid: int,
    helper: str,
    dmcp: Optional[str],
    proc: Callable[[int], Optional[ProcInfo]] = read_proc,
) -> tuple[Optional[Vetted], str]:
    """The request `pid` makes, when this relay may show it; else (None, why)."""
    asker = proc(pid)
    if asker is None or helper not in asker.cmdline:
        return None, "not JARVIS's askpass helper"
    sudo = proc(asker.ppid)
    if sudo is None or sudo.comm != "sudo" or sudo.euid != 0:
        return None, "the helper's parent is not sudo"
    caller = proc(sudo.ppid)
    if dmcp is None or caller is None or caller.exe is None:
        return None, "cannot tell who ran sudo"
    if os.path.realpath(caller.exe) != os.path.realpath(dmcp):
        return None, f"sudo was run by {caller.exe}, not dmcp"
    step, depth = caller, 0
    while step is not None and step.pid != daemon_pid and depth < _MAX_DEPTH:
        if step.ppid <= 1:
            step = None
            break
        step, depth = proc(step.ppid), depth + 1
    if step is None or step.pid != daemon_pid:
        return None, "dmcp was not started by this JARVIS"
    command = sudo_command(sudo.cmdline)
    if not command:
        return None, "sudo names no command"
    at = asker.cmdline.index(helper)
    prompt = " ".join(asker.cmdline[at + 1 :]).strip()
    return (
        Vetted(" ".join(shlex.quote(a) for a in command), prompt, sudo, caller),
        "",
    )


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


def _kill(info: ProcInfo, proc: Callable[[int], Optional[ProcInfo]]) -> None:
    """SIGKILL `info`'s process if it is still that process (same start)."""
    now = proc(info.pid)
    if now is None or now.start != info.start:
        return
    try:
        os.kill(info.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


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
        started: Callable[[list[str]], bool] = started_by_jarvis,
        kill: Callable[[ProcInfo, Callable], None] = _kill,
        watch_s: float = 1.0,
    ):
        self._broadcast = broadcast
        self._dmcp = dmcp
        self._daemon_pid = daemon_pid if daemon_pid is not None else os.getpid()
        self._proc = proc
        self._peer = peer
        self._started = started
        self._kill = kill
        self._watch_s = watch_s
        self._pending: dict[str, _Pending] = {}
        # sudo pid -> (requests so far, its last prompt, when): sudo asks
        # again after a wrong password, and the popup says so.
        self._tries: dict[int, tuple[int, str, float]] = {}
        self._server: Optional[asyncio.AbstractServer] = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._closing = False

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
        """Close the socket; a request still waiting has its sudo killed."""
        self._closing = True
        for item in list(self._pending.values()):
            if not item.answer.done():
                item.answer.set_result(None)
        if self._server is not None:
            self._server.close()
            for writer in list(self._writers):
                writer.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=3)
            except asyncio.TimeoutError:
                logger.warning("JARVIS: the askpass relay did not close in time")
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
        self._writers.add(writer)
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            msg = json.loads(line) if line else {}
            if not isinstance(msg, dict):
                return
            if msg.get("type") == "check":
                await self._write(
                    writer, {"ready": self.enabled() and not self._closing}
                )
            elif msg.get("type") == "ask":
                await self._ask(reader, writer)
        except (asyncio.TimeoutError, ValueError, OSError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _ask(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._closing or not self.enabled():
            await self._write(writer, {"error": "the password popup is off"})
            return
        pid = self._peer(writer)
        vetted, why = (
            vet(
                pid,
                daemon_pid=self._daemon_pid,
                helper=helper_path(),
                dmcp=self._dmcp,
                proc=self._proc,
            )
            if pid is not None
            else (None, "unknown peer")
        )
        if vetted is not None and not self._started(vetted.caller.cmdline):
            why = f"JARVIS did not start {' '.join(vetted.caller.cmdline[1:])[:200]}"
            vetted = None
        if vetted is None:
            logger.warning(f"JARVIS: refused a sudo password request: {why}")
            await self._write(writer, {"error": why})
            return

        now = time.time()
        self._tries = {p: t for p, t in self._tries.items() if now - t[2] < 600}
        tries, last_prompt, _ = self._tries.get(vetted.sudo.pid, (0, "", now))
        self._tries[vetted.sudo.pid] = (tries + 1, vetted.prompt, now)

        request = {
            "type": "password_request",
            "id": secrets.token_hex(8),
            "command": vetted.command,
            "prompt": vetted.prompt,
            "attempt": tries + 1,
            # sudo asking the same again means the last answer was wrong; a
            # different prompt (a code, a new password) is a question of its own.
            "retry": tries > 0 and vetted.prompt == last_prompt,
            "at": now,
        }
        answer = asyncio.get_running_loop().create_future()
        self._pending[request["id"]] = _Pending(request, answer)
        gone = asyncio.ensure_future(reader.read())
        outcome = "gone"
        try:
            self._broadcast(request)
            while True:
                done, _ = await asyncio.wait(
                    {answer, gone},
                    timeout=self._watch_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done:
                    break
                caller = self._proc(vetted.caller.pid)
                if caller is None or caller.start != vetted.caller.start:
                    # The dmcp that asked is gone (a stopped task, a timeout):
                    # no answer may run its command now.
                    self._kill(vetted.sudo, self._proc)
                    break
            if answer.done() and not answer.cancelled():
                password = answer.result()
                if password is None:
                    outcome = "cancelled"
                    self._kill(vetted.sudo, self._proc)
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
            f"(try {tries + 1}): {vetted.command[:120]}"
        )

    @staticmethod
    async def _write(writer: asyncio.StreamWriter, message: dict) -> None:
        writer.write((json.dumps(message) + "\n").encode("utf-8"))
        await writer.drain()
