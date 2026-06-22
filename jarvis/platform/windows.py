"""Windows platform backend — TCP localhost IPC, %APPDATA% paths, toast notifications."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .base import BasePlatform

_PORT_FILE_SUFFIX = ".port"


class WindowsPlatform(BasePlatform):

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
        return server

    def ipc_connect(self, path: str) -> socket.socket:
        port_file = path + _PORT_FILE_SUFFIX
        port = int(Path(port_file).read_text(encoding="utf-8").strip())
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(("127.0.0.1", port))
        return sock

    def ipc_cleanup(self, path: str) -> None:
        port_file = path + _PORT_FILE_SUFFIX
        for f in (path, port_file):
            try:
                os.unlink(f)
            except OSError:
                pass

    def ipc_secure(self, path: str) -> None:
        pass

    def ipc_verify_owner(self, path: str) -> bool:
        return True

    # -- Notifications -------------------------------------------------------

    def has_desktop_notifications(self) -> bool:
        try:
            import ctypes

            ctypes.windll  # type: ignore[attr-defined]
            return True
        except (ImportError, AttributeError):
            return False

    async def send_desktop_notification(
        self,
        title: str,
        body: str,
        timeout_ms: int,
    ) -> Optional[str]:
        # PowerShell toast notification (works on Windows 10+)
        ps_script = (
            f"Add-Type -AssemblyName System.Windows.Forms; "
            f"$n = New-Object System.Windows.Forms.NotifyIcon; "
            f"$n.Icon = [System.Drawing.SystemIcons]::Information; "
            f"$n.Visible = $true; "
            f'$n.ShowBalloonTip({timeout_ms}, "{title}", "{body}", '
            f"[System.Windows.Forms.ToolTipIcon]::Info); "
            f"Start-Sleep -Seconds {max(timeout_ms // 1000, 5)}; "
            f"$n.Dispose()"
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell",
                "-NoProfile",
                "-Command",
                ps_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception:
            pass
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
