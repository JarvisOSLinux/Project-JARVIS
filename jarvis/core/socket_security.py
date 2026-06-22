"""IPC endpoint permission hardening.

JARVIS uses IPC endpoints (Unix domain sockets on Linux/macOS, TCP
localhost on Windows) instead of network-visible TCP ports.  This
eliminates the network-exposure attack surface that affected OpenClaw
(CVE-2026-25253, ~40 000 instances reachable on Shodan).

On Unix systems, sockets are file-system objects: any OS process running
as the same UID can connect unless file permissions restrict access.
This module delegates permission enforcement and ownership verification
to the platform layer.

See docs/SECURITY-ARCHITECTURE.md for the full threat-model context.
"""

from __future__ import annotations

from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)


def harden_socket_path(socket_path: str | Path) -> None:
    """Apply secure permissions to the IPC endpoint via the platform layer."""
    from ..platform import current as platform

    path = str(socket_path)
    try:
        platform.ipc_secure(path)
        logger.debug("Hardened IPC endpoint: %s", path)
    except Exception as exc:
        logger.warning("Could not harden IPC endpoint %s: %s", path, exc)


def verify_socket_ownership(socket_path: str | Path) -> bool:
    """Verify the IPC endpoint is owned by the current user.

    Returns ``True`` if safe, ``False`` if ownership is unexpected.
    """
    from ..platform import current as platform

    path = str(socket_path)
    if not platform.ipc_verify_owner(path):
        logger.error(
            "SECURITY: IPC endpoint %s has unexpected ownership. "
            "Possible hijack — refusing to use it.",
            path,
        )
        return False
    return True


def warn_if_allow_all(confirmation_mode: str) -> None:
    """Log a prominent security warning when CONFIRMATION_MODE=allow_all.

    In allow_all mode JARVIS executes every tool call — including shell
    commands — without prompting the user.  This is intentionally unsafe
    and should only be used in fully isolated, trusted environments.
    """
    if confirmation_mode == "allow_all":
        logger.warning(
            "SECURITY WARNING: CONFIRMATION_MODE=allow_all — JARVIS will "
            "execute ALL tool calls (including shell commands) without "
            "asking for approval.  Only use this in isolated, trusted "
            "environments."
        )
