"""macOS platform backend — AF_UNIX, ~/Library paths, osascript, launchctl."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import stat
import struct
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .base import BasePlatform

# `sys/un.h` (BSD/Darwin): SOL_LOCAL = 0, LOCAL_PEERCRED = 0x001. Not
# exposed as named constants by the socket module on Darwin, but
# socket.getsockopt() passes numeric level/optname straight through to the
# underlying C getsockopt(2), so no ctypes needed.
_SOL_LOCAL = 0
_LOCAL_PEERCRED = 0x001
# struct xucred { u_int cr_version; uid_t cr_uid; short cr_ngroups; gid_t cr_groups[16]; }
_XUCRED_FMT = "IIh16i"
_XUCRED_SIZE = struct.calcsize(_XUCRED_FMT)


class MacOSPlatform(BasePlatform):

    # -- Paths ---------------------------------------------------------------

    def config_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "jarvis"

    def data_dir(self) -> Path:
        return Path.home() / "Library" / "Application Support" / "jarvis"

    # -- IPC -----------------------------------------------------------------

    async def create_ipc_server(
        self,
        path: str,
        client_handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any],
    ) -> asyncio.AbstractServer:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        server = await asyncio.start_unix_server(client_handler, path=path)
        self.ipc_secure(path)
        return server

    def ipc_connect(self, path: str) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(path)
        return sock

    def ipc_cleanup(self, path: str) -> None:
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass

    def ipc_secure(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            return
        try:
            p.chmod(0o600)
        except OSError:
            pass
        parent = p.parent
        try:
            current_mode = stat.S_IMODE(parent.stat().st_mode)
            if current_mode & (stat.S_IRWXG | stat.S_IRWXO):
                parent.chmod(0o700)
        except OSError:
            pass

    def ipc_verify_owner(self, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            return True
        try:
            return p.stat().st_uid == os.getuid()
        except OSError:
            return False

    async def ipc_verify_peer(self, reader: Any, writer: Any) -> bool:
        sock = writer.get_extra_info("socket")
        if sock is None:
            return True
        try:
            creds = sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, _XUCRED_SIZE)
            fields = struct.unpack(_XUCRED_FMT, creds)
            cr_uid = fields[1]
            return cr_uid == os.getuid()
        except OSError:
            # LOCAL_PEERCRED unavailable — fall back to the 0600 file
            # permission (ipc_secure/ipc_verify_owner) as the boundary.
            return True

    # -- Sidecar resolution ----------------------------------------------------

    def sidecar_search_dirs(self) -> list[Path]:
        home = Path.home()
        return [
            home / ".local" / "bin",
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
        ]

    # -- Privilege elevation -----------------------------------------------------

    def privileged_prefixes(self) -> tuple[str, ...]:
        return (
            "launchctl",
            "sysadminctl",
            "dscl",
            "networksetup",
            "installer",
            "systemsetup",
            "pfctl",
            "sysctl -w",
            "tee /etc",
            "tee /usr",
            "tee /Library",
        )

    def askpass_helpers(self) -> tuple[str, ...]:
        # macOS ships no CLI askpass helper; the shim script below wraps
        # osascript's GUI password dialog to present the same interface.
        return ("/usr/local/libexec/jarvis-osascript-askpass",)

    def find_askpass(self) -> Optional[str]:
        shim = _ensure_osascript_askpass_shim()
        return shim if shim else None

    def elevate(self, command: str) -> str:
        if not self.find_askpass():
            raise RuntimeError("Could not install the osascript askpass shim.")
        return f"sudo -A {command}"

    def grant_privilege(self) -> bool:
        # No jarvis-managed sudoers.d toggle on macOS yet; `sudo -A` already
        # prompts via the osascript askpass shim on every privileged call,
        # so there is nothing additional to persistently grant.
        return False

    def revoke_privilege(self) -> bool:
        return False

    def is_privilege_granted(self) -> bool:
        return False

    def open_command(self, target: str) -> list[str]:
        return ["open", target]

    # -- Notifications -------------------------------------------------------

    def has_desktop_notifications(self) -> bool:
        return shutil.which("osascript") is not None

    async def send_desktop_notification(
        self,
        title: str,
        body: str,
        timeout_ms: int,
    ) -> Optional[str]:
        script = (
            f'display dialog "{_applescript_quote(body)}" with title "{_applescript_quote(title)}" '
            f'buttons {{"Deny", "Allow"}} default button "Deny" '
            f"giving up after {max(timeout_ms // 1000, 5)}"
        )
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        result = stdout.decode().strip().lower()
        if "allow" in result:
            return "allow"
        return "deny" if proc.returncode == 0 else None

    # -- Service control -----------------------------------------------------

    def try_start_service(self, name: str, base_url: str) -> bool:
        # Try launchctl (Homebrew services register as user agents)
        for cmd in (
            ["launchctl", "start", f"com.{name}.{name}"],
            ["launchctl", "start", name],
        ):
            try:
                subprocess.run(cmd, timeout=5, capture_output=True)
            except Exception:
                continue
            for _ in range(3):
                time.sleep(1)
                if _is_service_up(base_url):
                    return True
        return False


def _is_service_up(base_url: str) -> bool:
    import urllib.request

    try:
        urllib.request.urlopen(f"{base_url}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def _applescript_quote(text: str) -> str:
    """Escape a string for embedding in a double-quoted AppleScript literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


_ASKPASS_SHIM_PATH = Path("/usr/local/libexec/jarvis-osascript-askpass")

_ASKPASS_SHIM_SCRIPT = """#!/bin/sh
# Installed by JARVIS (jarvis/platform/macos.py). Bridges SUDO_ASKPASS to a
# GUI credential prompt via osascript, mirroring ksshaskpass on Linux — the
# GUI dialog is the elevation security boundary, never a silent NOPASSWD.
exec osascript -e 'Tell application "System Events" to display dialog \\
  "sudo needs your password:" default answer "" with hidden answer \\
  buttons {"Cancel", "OK"} default button "OK"' \\
  -e 'text returned of result'
"""


def _ensure_osascript_askpass_shim() -> Optional[str]:
    """Install the askpass shim script if missing/stale, return its path.

    Best-effort: requires write access to /usr/local/libexec (root, or a
    directory the current user owns). Returns None if it can't be written —
    callers fall back to failing the elevation attempt loudly rather than
    silently running unprivileged.
    """
    try:
        if (
            _ASKPASS_SHIM_PATH.is_file()
            and _ASKPASS_SHIM_PATH.read_text() == _ASKPASS_SHIM_SCRIPT
            and os.access(_ASKPASS_SHIM_PATH, os.X_OK)
        ):
            return str(_ASKPASS_SHIM_PATH)
        _ASKPASS_SHIM_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ASKPASS_SHIM_PATH.write_text(_ASKPASS_SHIM_SCRIPT)
        _ASKPASS_SHIM_PATH.chmod(0o755)
        return str(_ASKPASS_SHIM_PATH)
    except OSError:
        return None
