"""Standing constraints — path-prefix deny rules enforced at the dispatch gate (#214).

Constraints are stored as JSON at $JARVIS_DATA_DIR/constraints.json and written
atomically. Enforcement is mechanical: checked before the confirmation gate, so
allow_all cannot bypass it. All public functions fail-safe — they catch every
exception and return an empty result rather than crashing the dispatch path.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from ..config import Config
from ..platform.ownership import keep_owner, make_dirs


def _store_path() -> Path:
    return Path(Config.JARVIS_DATA_DIR).expanduser() / "constraints.json"


def load_constraints() -> list[dict]:
    """Return all constraint records (active and inactive). Empty list on any error."""
    try:
        path = _store_path()
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [r for r in data if isinstance(r, dict)]
    except Exception:
        return []


def _save_constraints(records: list[dict]) -> None:
    path = _store_path()
    make_dirs(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".constraints_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
            f.write("\n")
        keep_owner(tmp)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_constraint(text: str, pattern: str, source: str = "cli") -> dict:
    """Add a path-prefix deny constraint. Returns the new record."""
    norm = os.path.abspath(os.path.expanduser(pattern.strip()))
    record: dict = {
        "id": uuid4().hex[:8],
        "text": text.strip(),
        "pattern": norm,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "source": source,
        "active": True,
    }
    records = load_constraints()
    records.append(record)
    _save_constraints(records)
    return record


def remove_constraint(constraint_id: str) -> bool:
    """Remove constraint by id. Returns True if found and removed."""
    try:
        records = load_constraints()
        new = [r for r in records if r.get("id") != constraint_id]
        if len(new) == len(records):
            return False
        _save_constraints(new)
        return True
    except Exception:
        return False


def active_constraints() -> list[dict]:
    """Return only active constraint records. Empty list on any error."""
    try:
        return [r for r in load_constraints() if r.get("active", True)]
    except Exception:
        return []
