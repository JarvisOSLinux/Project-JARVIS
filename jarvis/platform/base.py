"""Abstract base for platform-specific operations."""

from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional


class BasePlatform(ABC):
    """Interface that each OS backend implements."""

    # -- Paths ---------------------------------------------------------------

    @abstractmethod
    def config_dir(self) -> Path:
        """Return the user config directory (e.g. ~/.config/jarvis)."""

    @abstractmethod
    def data_dir(self) -> Path:
        """Return the user data directory (e.g. ~/.local/share/jarvis)."""

    # -- IPC -----------------------------------------------------------------

    @abstractmethod
    async def create_ipc_server(
        self,
        path: str,
        client_handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any],
    ) -> asyncio.AbstractServer:
        """Create an IPC server at *path* and return the ``asyncio.Server``."""

    @abstractmethod
    def ipc_connect(self, path: str) -> Any:
        """Return a connected socket to the IPC endpoint at *path*."""

    @abstractmethod
    def ipc_cleanup(self, path: str) -> None:
        """Remove the IPC endpoint file/resource after shutdown."""

    @abstractmethod
    def ipc_secure(self, path: str) -> None:
        """Apply restrictive permissions to the IPC endpoint."""

    @abstractmethod
    def ipc_verify_owner(self, path: str) -> bool:
        """Return True if the IPC endpoint is owned by the current user."""

    @abstractmethod
    async def ipc_verify_peer(self, reader: Any, writer: Any) -> bool:
        """Return True if the connecting peer is the current user.

        Accept-time check — call and check the result before any line from
        *reader* reaches ``inject_user_input`` or a confirmation/shutdown
        handler. Async because some backends (Windows) must read a
        credential line off the wire; others (Linux/macOS) check
        synchronously via the socket and ignore *reader* entirely.

        Linux: ``SO_PEERCRED``. macOS: ``LOCAL_PEERCRED``. Windows: a
        per-startup token sent as the connection's first line (interim —
        see windows.py for why this isn't yet a true peer-credential check).
        This is the real access-control boundary on platforms (Windows)
        where the IPC transport has no filesystem permissions to rely on —
        see Project-JARVIS #168.
        """

    def system_ipc_candidates(self) -> list[str]:
        """Well-known system-wide IPC endpoint paths to probe, if any.

        Only meaningful on platforms with a shared filesystem-namespace
        default location (Linux's ``/run/jarvis``). Empty on platforms
        where no such convention exists, so callers stop guessing a
        Linux-only path.
        """
        return []

    # -- Sidecar / privileged-helper resolution -------------------------------

    def sidecar_search_dirs(self) -> list[Path]:
        """Per-OS default install directories to search for sidecar binaries."""
        return []

    def resolve_sidecar(
        self, name: str, config_override: Optional[str] = None
    ) -> Optional[str]:
        """Resolve a sidecar binary: config override -> PATH -> per-OS defaults.

        Returns the absolute path if found, else ``None``. Centralizing this
        means "not found" errors can print the real per-OS search path
        instead of a bare binary name.
        """
        if config_override:
            override_path = Path(config_override)
            if override_path.is_file():
                return str(override_path)

        found = shutil.which(name)
        if found:
            return found

        for directory in self.sidecar_search_dirs():
            for candidate in (directory / name, directory / f"{name}.exe"):
                if candidate.is_file():
                    return str(candidate)
        return None

    # -- Privilege elevation ---------------------------------------------------

    @abstractmethod
    def privileged_prefixes(self) -> tuple[str, ...]:
        """Command prefixes that require elevation on this OS."""

    @abstractmethod
    def askpass_helpers(self) -> tuple[str, ...]:
        """Candidate GUI askpass helper binaries to probe, in priority order."""

    def find_askpass(self) -> Optional[str]:
        """Return the first available askpass helper, or None."""
        for helper in self.askpass_helpers():
            found = shutil.which(helper) or (helper if Path(helper).is_file() else None)
            if found:
                return found
        return None

    @abstractmethod
    def elevate(self, command: str) -> str:
        """Wrap *command* so it runs elevated via the GUI credential boundary.

        The GUI prompt (askpass dialog / osascript "with administrator
        privileges") is the security boundary on every OS — never silent,
        never NOPASSWD. Raises ``RuntimeError`` if no elevation mechanism
        is available on this platform (caller should surface that instead
        of running the command unprivileged).
        """

    @abstractmethod
    def grant_privilege(self) -> bool:
        """Persistently grant the current user elevation rights (e.g. sudo).

        Returns True on success. Platforms without a persistent-grant concept
        (Windows: elevation is per-action via UAC, not a standing grant)
        return False.
        """

    @abstractmethod
    def revoke_privilege(self) -> bool:
        """Revoke a grant made by ``grant_privilege``. Returns True on success."""

    @abstractmethod
    def is_privilege_granted(self) -> bool:
        """Return True if ``grant_privilege`` is currently in effect."""

    # -- App opening -----------------------------------------------------------

    @abstractmethod
    def open_command(self, target: str) -> list[str]:
        """Return the argv that opens *target* (file/URL/app) on this OS."""

    # -- Notifications -------------------------------------------------------

    @abstractmethod
    def has_desktop_notifications(self) -> bool:
        """Return True if desktop notifications are available."""

    @abstractmethod
    async def send_desktop_notification(
        self,
        title: str,
        body: str,
        timeout_ms: int,
    ) -> Optional[str]:
        """Show a desktop notification. Return the chosen action or None."""

    # -- Service control -----------------------------------------------------

    @abstractmethod
    def try_start_service(self, name: str, base_url: str) -> bool:
        """Attempt to start a system service by name. Return True if it came up."""

    # -- Signals -------------------------------------------------------------

    def install_signal_handlers(
        self,
        loop: asyncio.AbstractEventLoop,
        stop_callback: Callable[[], None],
    ) -> None:
        """Register graceful-stop handlers for SIGTERM/SIGINT."""
        import signal

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop_callback)
            except (ValueError, OSError, NotImplementedError):
                pass
