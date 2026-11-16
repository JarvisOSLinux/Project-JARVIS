"""What JARVIS writes as root in a user's directory stays that user's.

The jarvis CLI, run as root through a shell server (2026-11-15), rewrote
~/.config/jarvis/providers.json as root:root 0600, and the daemon, running as
the user, hit PermissionError on every provider read after it. Every file and
directory JARVIS writes under its config and data dirs goes through here. A
directory root owns (a system install's /etc/jarvis) keeps root's files.
"""

from __future__ import annotations

import logging
import os
import shlex
from typing import Iterable, List, Optional, Union

PathLike = Union[str, "os.PathLike[str]"]
# Files named in the start-up error; the rest are counted.
_SHOWN = 10


def _euid() -> Optional[int]:
    geteuid = getattr(os, "geteuid", None)
    return geteuid() if geteuid is not None else None


def keep_owner(path: PathLike) -> None:
    """When running as root, give ``path`` to the owner of its directory.

    Its mode is kept. A symlink is changed itself, never what it points to.
    """
    if _euid() != 0:
        return
    parent = os.stat(os.path.dirname(os.path.abspath(path)))
    if parent.st_uid != 0:
        os.chown(path, parent.st_uid, parent.st_gid, follow_symlinks=False)


def make_dirs(path: PathLike, mode: int = 0o777) -> None:
    """``os.makedirs(path, mode, exist_ok=True)``, each new directory kept by
    the owner of the one it is made in."""
    new = []
    folder = os.path.abspath(path)
    while not os.path.exists(folder):
        new.append(folder)
        folder = os.path.dirname(folder)
    os.makedirs(path, mode, exist_ok=True)
    for folder in reversed(new):
        keep_owner(folder)


def user_dir_under_root(paths: Iterable[PathLike]) -> Optional[str]:
    """Running as root, the first of ``paths`` a non-root user owns (or, when
    it does not exist yet, the dir it would be made in); else None."""
    if _euid() != 0:
        return None
    for path in paths:
        folder = os.path.abspath(path)
        while not os.path.exists(folder):
            folder = os.path.dirname(folder)
        if os.stat(folder).st_uid != 0:
            return folder
    return None


def foreign_files(top: PathLike, uid: int) -> List[str]:
    """``top`` and everything under it that ``uid`` does not own."""
    paths = [os.fspath(top)]
    for folder, dirs, files in os.walk(top):
        paths += [os.path.join(folder, name) for name in dirs + files]
    found = []
    for path in paths:
        try:
            if os.lstat(path).st_uid != uid:
                found.append(path)
        except OSError:
            continue
    return found


def report_foreign_files(logger: logging.Logger, dirs: Iterable[PathLike]) -> None:
    """Log an ERROR naming the files in the user's JARVIS dirs another user owns.

    The daemon cannot take them back itself, so the error gives the command
    that does. A dir in one the user does not own (/etc/jarvis) is skipped.
    """
    uid = _euid()
    if uid is None or uid == 0:
        return
    import grp
    import pwd

    for top in dict.fromkeys(os.path.abspath(d) for d in dirs):
        try:
            if os.stat(os.path.dirname(top)).st_uid != uid:
                continue
        except OSError:
            continue
        found = foreign_files(top, uid)
        if not found:
            continue
        try:
            user = pwd.getpwuid(uid).pw_name
        except KeyError:
            user = str(uid)
        try:
            group = grp.getgrgid(os.getegid()).gr_name
        except KeyError:
            group = str(os.getegid())
        shown = ", ".join(found[:_SHOWN])
        if len(found) > _SHOWN:
            shown += f" and {len(found) - _SHOWN} more"
        logger.error(
            f"JARVIS: {len(found)} file(s) in {top} belong to another user, so "
            f"JARVIS cannot read or change them: {shown}. A jarvis command run as "
            "root (through sudo or a root shell) leaves files like these. Fix: "
            f"sudo chown -R {user}:{group} {shlex.quote(top)}"
        )
