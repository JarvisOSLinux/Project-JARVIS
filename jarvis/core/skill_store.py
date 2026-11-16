"""Per-server skill files — LLM-authored how-to keyed by MCP server id (#202).

One server, one Markdown file under ``<JARVIS_DATA_DIR>/skills/``. Lookup is an
exact ``server_id`` match, so there is nothing to rank and contextor stays out
of this path entirely.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from ..config import Config
from ..platform.ownership import keep_owner, make_dirs
from .logger import get_logger

logger = get_logger(__name__)

# A server id becomes a filename, so it is validated as a name rather than
# rewritten into one: a sanitized id would silently address the wrong file.
_SERVER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class InvalidServerId(ValueError):
    """A server id that cannot be used as a skill filename."""


def is_valid_server_id(server_id: object) -> bool:
    """True for ids shaped like the reverse-domain ones dmcp issues."""
    if not isinstance(server_id, str) or ".." in server_id:
        return False
    return bool(_SERVER_ID_RE.match(server_id))


def skills_dir() -> Path:
    """Directory holding every skill file."""
    return Path(Config.JARVIS_DATA_DIR).expanduser() / "skills"


def skill_path(server_id: str) -> Path:
    """Path of one server's skill file, refusing ids that escape the directory."""
    if not is_valid_server_id(server_id):
        raise InvalidServerId(f"unusable server id for a skill file: {server_id!r}")
    return skills_dir() / f"{server_id}.md"


def load_skill(server_id: str) -> Optional[str]:
    """Return the server's skill text, or None when there is none to load.

    Never raises: this runs while ROOT context is being assembled, and a
    malformed id or unreadable file must cost the turn its skill, not the turn.
    An oversized skill is dropped whole rather than truncated — half a procedure
    reads as a complete one, and the cap exists to keep a runaway skill from
    crowding the window.
    """
    try:
        path = skill_path(server_id)
    except InvalidServerId as e:
        logger.warning(f"SkillStore: {e}")
        return None

    try:
        size = path.stat().st_size
    except OSError:
        return None

    cap = Config.SKILL_MAX_BYTES
    if size > cap:
        logger.warning(
            f"SkillStore: skipping skill for '{server_id}' — "
            f"{size} bytes exceeds the {cap}-byte cap"
        )
        return None

    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"SkillStore: cannot read skill for '{server_id}': {e}")
        return None


def save_skill(server_id: str, content: str) -> Path:
    """Replace the server's skill file wholesale and return its path."""
    path = skill_path(server_id)
    make_dirs(path.parent)

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".skill_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        keep_owner(tmp)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def delete_skill(server_id: str) -> bool:
    """Remove the server's skill file; False when there was nothing to remove."""
    path = skill_path(server_id)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
