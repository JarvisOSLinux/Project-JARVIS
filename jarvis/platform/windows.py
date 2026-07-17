"""Windows platform backend — TCP localhost IPC, %APPDATA% paths, toast notifications."""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from .base import BasePlatform

_PORT_FILE_SUFFIX = ".port"
_TOKEN_FILE_SUFFIX = ".token"
_TOKEN_NBYTES = 32


def _lock_down_to_current_user(path: str) -> None:
    """Best-effort ACL restriction to the current user via icacls.

    No pywin32 dependency: icacls ships on every supported Windows release.
    Best-effort because a failure here shouldn't crash startup — the token
    check in ipc_verify_peer is still the actual access-control boundary,
    this just narrows the blast radius of someone reading the token file.
    """
    user = os.environ.get("USERNAME")
    if not user:
        return
    try:
        subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


class WindowsPlatform(BasePlatform):

    def __init__(self) -> None:
        self._startup_token: Optional[str] = None

    # -- Paths ---------------------------------------------------------------

    def config_dir(self) -> Path:
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(base) / "jarvis"

    def data_dir(self) -> Path:
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "jarvis"

    # -- IPC -----------------------------------------------------------------
    # Windows lacks AF_UNIX. We use TCP on 127.0.0.1 with an ephemeral port
    # and write the port number to a lockfile next to where the socket path
    # would be, so callers can discover it.
    #
    # That loopback socket has no OS-level access control (any local process
    # of any user can connect — see Project-JARVIS #168), so a per-startup
    # random token is required as the connection's first line and checked
    # in ipc_verify_peer() before any confirmation/shutdown/input message is
    # honored. This is the documented interim mitigation, not the final fix:
    # the real fix is a named pipe with an explicit per-user SDDL DACL plus
    # GetNamedPipeClientProcessId as the SO_PEERCRED analogue, which needs a
    # real Windows box to verify (asyncio's ProactorEventLoop pipe-serving
    # internals are not something to rewrite blind on a Linux dev machine).

    async def create_ipc_server(
        self,
        path: str,
        client_handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any],
    ) -> asyncio.AbstractServer:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        server = await asyncio.start_server(client_handler, host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        port_file = path + _PORT_FILE_SUFFIX
        Path(port_file).write_text(str(port), encoding="utf-8")
        _lock_down_to_current_user(port_file)

        self._startup_token = secrets.token_hex(_TOKEN_NBYTES)
        token_file = path + _TOKEN_FILE_SUFFIX
        Path(token_file).write_text(self._startup_token, encoding="utf-8")
        _lock_down_to_current_user(token_file)

        return server

    def ipc_connect(self, path: str) -> socket.socket:
        port_file = path + _PORT_FILE_SUFFIX
        port = int(Path(port_file).read_text(encoding="utf-8").strip())
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(("127.0.0.1", port))
        token_file = path + _TOKEN_FILE_SUFFIX
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
            sock.sendall((token + "\n").encode("utf-8"))
        except OSError:
            # No token file readable — server will reject the connection
            # in ipc_verify_peer() if a token is required.
            pass
        return sock

    def ipc_cleanup(self, path: str) -> None:
        for f in (path, path + _PORT_FILE_SUFFIX, path + _TOKEN_FILE_SUFFIX):
            try:
                os.unlink(f)
            except OSError:
                pass
        self._startup_token = None

    def ipc_secure(self, path: str) -> None:
        pass

    def ipc_verify_owner(self, path: str) -> bool:
        return True

    async def ipc_verify_peer(self, reader: Any, writer: Any) -> bool:
        if self._startup_token is None:
            # No server-side token generated (e.g. create_ipc_server not
            # used, or called out of order) — fail closed.
            return False
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            return False
        received = line.decode("utf-8", errors="replace").strip()
        return secrets.compare_digest(received, self._startup_token)

    def system_ipc_candidates(self) -> list[str]:
        return []

    # -- Sidecar resolution ----------------------------------------------------

    def sidecar_search_dirs(self) -> list[Path]:
        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        return [
            Path(local) / "Programs" / "jarvis" / "bin",
            Path(program_files) / "jarvis" / "bin",
        ]

    # -- Privilege elevation -----------------------------------------------------

    def privileged_prefixes(self) -> tuple[str, ...]:
        return (
            "reg add",
            "reg delete",
            "net user",
            "net localgroup",
            "sc config",
            "sc create",
            "sc delete",
            "sc start",
            "sc stop",
            "netsh",
            "bcdedit",
            "diskpart",
        )

    def askpass_helpers(self) -> tuple[str, ...]:
        # Windows elevation is a UAC consent prompt, not a CLI askpass
        # helper — there is nothing to probe for here.
        return ()

    def elevate(self, command: str) -> str:
        raise RuntimeError(
            "Windows elevation is not implemented as a command-line wrapper. "
            "Each privileged action needs its own UAC consent prompt "
            "(e.g. via ShellExecuteEx with lpVerb='runas'), not a `sudo`-style "
            "prefix — see Project-JARVIS #173/#171 for the tracked follow-up."
        )

    def grant_privilege(self) -> bool:
        # No standing-grant concept on Windows: administrator rights come
        # from group membership + a fresh UAC prompt per elevated action.
        return False

    def revoke_privilege(self) -> bool:
        return False

    def is_privilege_granted(self) -> bool:
        return False

    def open_command(self, target: str) -> list[str]:
        return ["cmd", "/c", "start", "", target]

    # -- Notifications -------------------------------------------------------

    def has_desktop_notifications(self) -> bool:
        # Interim fix for #169: this used to return True whenever
        # ctypes.windll existed (i.e. always, on every Windows install),
        # even though the actual notification below has no Allow/Deny
        # buttons — it just sleeps and returns None, which
        # ConfirmationManager reads as an auto-deny, and worse, the
        # early-return in _send_notification() for an "available" desktop
        # channel meant that auto-deny shadowed the working socket/CLI
        # channels entirely. Only claim availability if a real actionable
        # toast backend is actually importable.
        try:
            import windows_toasts  # noqa: F401

            return True
        except ImportError:
            return False

    async def send_desktop_notification(
        self,
        title: str,
        body: str,
        timeout_ms: int,
    ) -> Optional[str]:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, _show_actionable_toast, title, body, timeout_ms
            )
        except Exception:
            return None

    # -- Service control -----------------------------------------------------

    def try_start_service(self, name: str, base_url: str) -> bool:
        return False

    # -- Signals -------------------------------------------------------------

    def install_signal_handlers(
        self,
        loop: asyncio.AbstractEventLoop,
        stop_callback: Callable[[], None],
    ) -> None:
        import signal

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, lambda s, f: stop_callback())
            except (ValueError, OSError):
                pass


def _show_actionable_toast(title: str, body: str, timeout_ms: int) -> Optional[str]:
    """Show a WinRT toast with real Allow/Deny buttons via `windows-toasts`.

    Runs in a worker thread (blocking, event-driven library) — only called
    when has_desktop_notifications() confirmed `windows_toasts` is
    importable. Returns "allow", "deny", or None (no response / dismissed
    / timed out).
    """
    import threading

    from windows_toasts import Toast, ToastButton, WindowsToaster

    result: dict[str, Optional[str]] = {"action": None}
    done = threading.Event()

    toast = Toast()
    # Text/body pass through the WinRT XML toast schema as data, not shell
    # or script text, so no quoting/escaping is needed here (unlike the
    # PowerShell balloon-tip this replaces, or macOS's osascript dialog).
    toast.text_fields = [title, body]
    toast.on_activated = lambda activated_event_args: (
        result.__setitem__("action", activated_event_args.arguments),
        done.set(),
    )
    toast.AddAction(ToastButton("Allow", "allow"))
    toast.AddAction(ToastButton("Deny", "deny"))

    toaster = WindowsToaster("JARVIS")
    toaster.show_toast(toast)

    done.wait(timeout=max(timeout_ms / 1000, 1))
    return result["action"]
