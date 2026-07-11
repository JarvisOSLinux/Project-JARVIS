"""System-side sudo toggle for the JARVIS user (Project-JARVIS #158).

``enable_sudo`` / ``disable_sudo`` install or remove a sudoers drop-in that
grants the invoking user sudo; ``is_sudo_enabled`` reports whether that drop-in
is present so ``jarvis sudo`` can reconcile the real system state against the
``JARVIS_SUDO_ENABLED`` config preference.

The drop-in is **password-required** on purpose. ``shellmcp`` escalates via
``sudo -A`` + ksshaskpass, and the GUI password prompt is the security boundary
the architecture depends on — a ``NOPASSWD`` rule would remove it. This toggle
makes the grant explicit and jarvis-managed; it never weakens the prompt.

Writing to ``/etc/sudoers.d`` is guarded: root is required, the candidate file
is validated with ``visudo -c`` before it is installed, and the swap is atomic
(temp file + ``os.replace``, mode ``0440``). A malformed sudoers file can lock a
user out of sudo entirely, so validation is not optional.
"""

import getpass
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

SUDOERS_DROPIN = Path("/etc/sudoers.d/jarvis")


def _target_user() -> str:
    # Under `sudo jarvis sudo enable` the process runs as root; act on the
    # original user, recorded by sudo in SUDO_USER, not on root.
    return os.environ.get("SUDO_USER") or getpass.getuser()


def _validate_sudoers(path: Path) -> bool:
    visudo = shutil.which("visudo") or "/usr/sbin/visudo"
    try:
        result = subprocess.run(
            [visudo, "-c", "-f", str(path)],
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _install_dropin(content: str) -> bool:
    directory = SUDOERS_DROPIN.parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # A dotted name so sudo's `#includedir` skips this file while it exists
        # (sudo ignores drop-ins containing a `.`), keeping the swap atomic.
        fd, tmp_name = tempfile.mkstemp(prefix=".jarvis.", dir=str(directory))
    except OSError:
        return False

    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o440)
        if not _validate_sudoers(tmp):
            return False
        os.replace(tmp, SUDOERS_DROPIN)
        return True
    except OSError:
        return False
    finally:
        # os.replace consumed tmp on success; clean up on every failure path.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def is_sudo_enabled() -> bool:
    """Return whether the jarvis-managed sudoers drop-in is installed.

    Read-only and root-free — reflects real system state so the CLI can flag
    when it diverges from the ``JARVIS_SUDO_ENABLED`` preference.
    """
    return SUDOERS_DROPIN.exists()


def enable_sudo() -> bool:
    """Install a password-required sudoers drop-in for the invoking user.

    Returns ``False`` without modifying anything when not run as root or when
    the candidate file fails ``visudo`` validation.
    """
    if os.geteuid() != 0:
        return False
    return _install_dropin(f"{_target_user()} ALL=(ALL) ALL\n")


def disable_sudo() -> bool:
    """Remove the jarvis-managed sudoers drop-in. Idempotent.

    Returns ``False`` when not run as root; ``True`` when the drop-in is absent
    or successfully removed.
    """
    if os.geteuid() != 0:
        return False
    try:
        SUDOERS_DROPIN.unlink(missing_ok=True)
        return True
    except OSError:
        return False
