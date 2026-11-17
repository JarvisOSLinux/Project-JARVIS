"""
System information gathering for JARVIS AI Assistant

This module handles gathering system information needed for LLM initialization
and other system-dependent operations.
"""

import getpass
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List


class SystemInfo:
    """Handles system information gathering and shell detection"""

    @staticmethod
    def get_system_info() -> Dict[str, str]:
        """
        Get comprehensive system information for LLM initialization

        Returns:
            Dictionary containing system information
        """
        system = platform.system().lower()

        return {
            "system": system,
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "shell": SystemInfo._get_shell_command(system),
        }

    @staticmethod
    def get_users() -> Dict[str, Any]:
        """
        Human users logged in right now, and the account JARVIS runs as

        A packaged JARVIS runs as the system user `jarvis`, so its own
        account says nothing about who is talking to it; logind does.

        Returns:
            {"active": (name, home) or None, "others": [(name, home)],
             "runs_as": name}
        """
        me = getpass.getuser()
        own = (me, str(Path.home()))
        try:
            if platform.system() != "Linux" or not shutil.which("loginctl"):
                raise OSError("loginctl unavailable")
            import pwd  # POSIX-only; imported here so Windows still loads this module

            def loginctl(*args: str) -> subprocess.CompletedProcess:
                return subprocess.run(
                    ["loginctl", *args], capture_output=True, text=True, timeout=2
                )

            listed = loginctl("list-sessions", "--json=short")
            if listed.returncode != 0:
                raise OSError(listed.stderr.strip())
            # No seat0 on a headless machine: nobody is at the screen.
            seat = loginctl("show-seat", "seat0", "-P", "ActiveSession")
            seat_session = seat.stdout.strip() if seat.returncode == 0 else ""
            users: Dict[str, str] = {}
            active = None
            for s in json.loads(listed.stdout or "[]"):
                if s["class"] != "user" or not SystemInfo._is_human(s["uid"]):
                    continue
                users.setdefault(s["user"], pwd.getpwnam(s["user"]).pw_dir)
                if s["session"] == seat_session:
                    active = s["user"]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            return {"active": own, "others": [], "runs_as": me}

        if active is None and SystemInfo._is_human(os.geteuid()):
            active, users[me] = me, own[1]
        elif active is None and len(users) == 1:
            active = next(iter(users))
        return {
            "active": (active, users[active]) if active else None,
            "others": [(n, h) for n, h in users.items() if n != active],
            "runs_as": me,
        }

    @staticmethod
    def _is_human(uid: int) -> bool:
        # shortcut: assumes UID_MIN 1000, read /etc/login.defs if a distro differs
        return uid >= 1000 and uid != 65534

    @staticmethod
    def _get_shell_command(system: str) -> List[str]:
        """
        Get the appropriate shell command for the current system

        Args:
            system: Platform system name (lowercase)

        Returns:
            List of shell command arguments
        """
        if system == "windows":
            if shutil.which("pwsh"):
                return [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                ]
            elif shutil.which("powershell"):
                return [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                ]
            else:
                return ["cmd.exe", "/d", "/s", "/c"]
        else:
            if shutil.which("bash"):
                return ["bash", "-lc"]
            else:
                return ["sh", "-lc"]

    @staticmethod
    def get_platform_summary() -> str:
        """
        Get a human-readable platform summary

        Returns:
            String describing the current platform
        """
        info = SystemInfo.get_system_info()
        return f"{info['system'].title()} {info['release']} ({info['machine']})"
