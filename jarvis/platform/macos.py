"""macOS platform backend — AF_UNIX, ~/Library paths, osascript, launchctl."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .base import BasePlatform


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
            f'display dialog "{body}" with title "{title}" '
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
