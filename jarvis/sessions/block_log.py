"""A session's action blocks, kept so a reopened chat shows them.

contextor keeps what was said. What JARVIS did (searches, installs, tool runs,
confirmations; runtime/action_events.py) is kept here: one JSON line per
finished block in ``$JARVIS_DATA_DIR/session_blocks/<session id>.jsonl``.

Not in contextor: its entries are searched on every later prompt and handed to
the model as memories, and a block holds raw tool output, which may reach the
model only through dispatch's provenance boundary (#165).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from ..core.logger import get_logger

logger = get_logger(__name__)

DIRNAME = "session_blocks"
_SESSION_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")


class BlockLog:
    """Finished blocks per session; memory-only when built without a directory."""

    def __init__(self, directory: Optional[str] = None):
        self.directory = directory
        self._memory: Dict[str, List[Dict[str, Any]]] = {}

    def _path(self, session_id: str) -> Optional[str]:
        # The id names a file: anything but a plain id could leave the directory.
        if self.directory is None or not _SESSION_ID.match(session_id or ""):
            return None
        return os.path.join(self.directory, f"{session_id}.jsonl")

    def append(self, session_id: str, block: Dict[str, Any]) -> None:
        if not session_id:
            return
        if self.directory is None:
            self._memory.setdefault(session_id, []).append(dict(block))
            return
        path = self._path(session_id)
        if path is None:
            return
        line = json.dumps(block, ensure_ascii=False) + "\n"
        try:
            os.makedirs(self.directory, mode=0o700, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            logger.warning(f"Sessions: could not keep a block for {session_id}: {e}")

    def read(self, session_id: str, limit: int) -> List[Dict[str, Any]]:
        """The session's newest `limit` blocks, oldest first."""
        if self.directory is None:
            return [dict(b) for b in self._memory.get(session_id, [])[-limit:]]
        path = self._path(session_id)
        if path is None:
            return []
        blocks = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        block = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(block, dict):
                        blocks.append(block)
        except FileNotFoundError:
            return []
        except OSError as e:
            logger.warning(f"Sessions: could not read blocks of {session_id}: {e}")
            return []
        return blocks[-limit:]

    def delete(self, session_id: str) -> None:
        self._memory.pop(session_id, None)
        path = self._path(session_id)
        if path is None:
            return
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Sessions: could not delete blocks of {session_id}: {e}")
