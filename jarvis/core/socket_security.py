"""Unix socket permission hardening.

JARVIS uses Unix domain sockets (``~/.jarvis/input.sock`` and
``~/.jarvis/output.sock``) instead of TCP ports.  This eliminates the
network-exposure attack surface that affected OpenClaw (CVE-2026-25253,
~40 000 instances reachable on Shodan with no authentication).

Unix sockets are still file-system objects: any OS process running as the
same UID can connect to them unless file permissions restrict access.  This
module applies those permissions at socket-creation time and verifies
ownership before re-using an existing socket file.

See docs/SECURITY-ARCHITECTURE.md for the full threat-model context.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .logger import get_logger

logger = get_logger(__name__)


def harden_socket_path(socket_path: str | Path) -> None:
    """Apply secure permissions to a Unix socket file and its parent directory.

    Sets the socket file to 0600 (owner read/write only).  If the parent
    directory is group- or world-accessible, locks it to 0700 as well.
    """
    p = Path(socket_path)
    if not p.exists():
        return

    try:
        p.chmod(0o600)
        logger.debug("Hardened socket permissions: %s → 0600", p)
    except OSError as exc:
        logger.warning("Could not set socket permissions on %s: %s", p, exc)

    parent = p.parent
    try:
        current_mode = stat.S_IMODE(parent.stat().st_mode)
        if current_mode & (stat.S_IRWXG | stat.S_IRWXO):
            parent.chmod(0o700)
            logger.debug("Hardened socket directory: %s → 0700", parent)
    except OSError as exc:
        logger.warning("Could not set directory permissions on %s: %s", parent, exc)


def verify_socket_ownership(socket_path: str | Path) -> bool:
    """Verify the socket file is owned by the current process user.

    Returns ``True`` if safe, ``False`` if ownership is unexpected.
    An unexpected owner could indicate a pre-created hijack socket (a
    different user created the file at our path before we could, hoping
    to intercept our connections).
    """
    p = Path(socket_path)
    if not p.exists():
        return True

    try:
        file_uid = p.stat().st_uid
        my_uid = os.getuid()
        if file_uid != my_uid:
            logger.error(
                "SECURITY: socket %s is owned by uid=%d, expected uid=%d. "
                "Possible socket hijack — refusing to use it.",
                p,
                file_uid,
                my_uid,
            )
            return False
        return True
    except OSError as exc:
        logger.warning("Could not stat socket %s: %s", p, exc)
        return False


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
