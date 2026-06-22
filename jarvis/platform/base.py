"""Abstract base for platform-specific operations."""

from __future__ import annotations

import asyncio
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
