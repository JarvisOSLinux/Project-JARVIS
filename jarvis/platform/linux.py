"""Linux platform backend — AF_UNIX, XDG paths, notify-send, systemctl."""

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


class LinuxPlatform(BasePlatform):

    # -- Paths ---------------------------------------------------------------

    def config_dir(self) -> Path:
        base = os.environ.get(
            "XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")
        )
        return Path(base) / "jarvis"

    def data_dir(self) -> Path:
        base = os.environ.get(
            "XDG_DATA_HOME",
            os.path.join(os.path.expanduser("~"), ".local", "share"),
        )
        return Path(base) / "jarvis"

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
            # Not a real transport (e.g. under test) — the 0600 file
            # permission from ipc_secure() is still the primary boundary.
            return True
        try:
            creds = sock.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            )
            pid, uid, gid = struct.unpack("3i", creds)
            return uid == os.getuid()
        except (OSError, AttributeError):
            return False

    def system_ipc_candidates(self) -> list[str]:
        return ["/run/jarvis/input.sock"]

    # -- Sidecar resolution ----------------------------------------------------

    def sidecar_search_dirs(self) -> list[Path]:
        home = Path.home()
        return [
            home / ".local" / "bin",
            Path("/usr/local/bin"),
            Path("/usr/bin"),
        ]

    # -- Privilege elevation -----------------------------------------------------

    def privileged_prefixes(self) -> tuple[str, ...]:
        return (
            "pacman",
            "apt",
            "apt-get",
            "dnf",
            "yum",
            "zypper",
            "systemctl enable",
            "systemctl disable",
            "systemctl start",
            "systemctl stop",
            "systemctl restart",
            "systemctl mask",
            "systemctl unmask",
            "systemctl daemon-reload",
            "modprobe",
            "rmmod",
            "insmod",
            "sysctl -w",
            "useradd",
            "userdel",
            "groupadd",
            "groupdel",
            "usermod",
            "timedatectl set",
            "localectl set",
            "hostnamectl set",
            "ip link set",
            "ip addr add",
            "tee /etc",
            "tee /usr",
            "tee /var",
        )

    def askpass_helpers(self) -> tuple[str, ...]:
        return (
            "/usr/bin/ksshaskpass",
            "/usr/lib/ssh/ksshaskpass",
            "ssh-askpass",
            "lxqt-openssh-askpass",
            "x11-ssh-askpass",
        )

    def elevate(self, command: str) -> str:
        if not self.find_askpass():
            raise RuntimeError(
                "No GUI askpass helper found (tried: "
                f"{', '.join(self.askpass_helpers())}). Install one of these "
                "to allow privileged commands to prompt via a GUI dialog."
            )
        return f"sudo -A {command}"

    def grant_privilege(self) -> bool:
        from ..core import sudo_manager

        return sudo_manager.enable_sudo()

    def revoke_privilege(self) -> bool:
        from ..core import sudo_manager

        return sudo_manager.disable_sudo()

    def is_privilege_granted(self) -> bool:
        from ..core import sudo_manager

        return sudo_manager.is_sudo_enabled()

    def open_command(self, target: str) -> list[str]:
        return ["xdg-open", target]

    # -- Notifications -------------------------------------------------------

    def has_desktop_notifications(self) -> bool:
        return shutil.which("notify-send") is not None

    async def send_desktop_notification(
        self,
        title: str,
        body: str,
        timeout_ms: int,
    ) -> Optional[str]:
        proc = await asyncio.create_subprocess_exec(
            "notify-send",
            "--app-name=JARVIS",
            f"--expire-time={timeout_ms}",
            "--action=allow=Allow",
            "--action=deny=Deny",
            title,
            body,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip() or None

    # -- Service control -----------------------------------------------------

    def try_start_service(self, name: str, base_url: str) -> bool:
        for cmd in (
            ["systemctl", "--user", "start", name],
            ["systemctl", "start", name],
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
