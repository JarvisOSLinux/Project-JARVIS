"""Pending server setups: what an installed server still needs before it runs (#254).

Installing a server whose manifest has a required setting nobody has filled
in used to hold the whole goal on a form until it closed. Now the install
records a *pending setup* here and moves on; the user fills it in whenever
and wherever suits them -- `jarvis setups fill` from any terminal, or the
TUI -- and filling it resumes the goal.

The model mirrors the confirmation queue (#224) on purpose:

* the daemon owns ``setups.json`` and is its only writer; it is restored at
  startup, so a pending setup outlives a restart;
* ``jarvis setups`` reads that file when the daemon is off, and parks what it
  did (completed / cancelled) in ``setup_completions.json``, which the daemon
  applies at its next start -- the same split as ``confirmation_decisions.json``.

What is stored is only what a server *needs*: each setting's key, label,
description, whether it is required or secret, and its default. Never a
value. Values go from the form straight to dmcp (`dmcp config set --stdin`),
so a secret never touches this file, the socket, a log or the chat (#242).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..config import Config
from .confirmation_manager import _atomic_write_json
from .logger import get_logger

logger = get_logger(__name__)

STORE_FILENAME = "setups.json"
COMPLETION_QUEUE_FILENAME = "setup_completions.json"

# The parts of a configurableProperties entry a form needs. Anything else in
# the manifest stays there.
_FIELD_KEYS = ("key", "label", "description", "required", "sensitive", "default")


def store_path() -> Path:
    """Where the daemon keeps its pending setups."""
    return Path(Config.JARVIS_DATA_DIR) / STORE_FILENAME


def completion_queue_path() -> Path:
    """Where `jarvis setups` parks what it did while the daemon was off.

    Trusted exactly as far as a message on the local input socket, and
    protected the same way: the same-user filesystem boundary under
    JARVIS_DATA_DIR (see confirmation_manager.decision_queue_path).
    """
    return Path(Config.JARVIS_DATA_DIR) / COMPLETION_QUEUE_FILENAME


def setting_fields(props: Iterable[Any]) -> List[Dict[str, Any]]:
    """The form-relevant part of each declared setting, in manifest order."""
    out = []
    for prop in props or []:
        if isinstance(prop, dict) and isinstance(prop.get("key"), str):
            out.append({k: prop[k] for k in _FIELD_KEYS if k in prop})
    return out


def missing_required(props: Iterable[Any], config: Dict[str, Any]) -> List[str]:
    """Required settings that have no value yet in the server's config."""
    config = config if isinstance(config, dict) else {}
    return [
        f["key"]
        for f in setting_fields(props)
        if f.get("required") and not str(config.get(f["key"]) or "").strip()
    ]


@dataclass
class PendingSetup:
    """One server waiting for its settings."""

    setup_id: str
    server_id: str
    server_name: str
    summary: str = ""
    fields: List[Dict[str, Any]] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    # The goal the install belonged to. Its description is kept as well: by the
    # time the setup is filled the model may have closed the goal, and the
    # resumed turn still needs to know what the user originally asked for.
    goal_id: Optional[str] = None
    goal_description: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def read_store(path: Path) -> List[PendingSetup]:
    """Parse a persisted list. Raises on an unreadable or corrupt file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("store is not a list of setup records")
    known = {f.name for f in fields(PendingSetup)}
    out = []
    for record in data:
        if (
            isinstance(record, dict)
            and record.get("setup_id")
            and record.get("server_id")
        ):
            out.append(PendingSetup(**{k: v for k, v in record.items() if k in known}))
    return out


def find(setups: Iterable[PendingSetup], ref: str) -> Optional[PendingSetup]:
    """A setup by its id or its server's id."""
    for setup in setups:
        if ref in (setup.setup_id, setup.server_id):
            return setup
    return None


class SetupQueue:
    """The daemon's pending setups, mirrored to ``store_path`` on every change.

    Without a store path it is memory-only and touches no file (tests).
    """

    def __init__(self, store_path: Optional[str] = None) -> None:
        self._store_path = Path(store_path).expanduser() if store_path else None
        self._pending: Dict[str, PendingSetup] = {}
        self._restore()

    def _restore(self) -> None:
        if self._store_path is None or not self._store_path.exists():
            return
        try:
            for setup in read_store(self._store_path):
                self._pending[setup.setup_id] = setup
        except Exception as e:
            logger.warning(
                f"Could not restore pending setups from {self._store_path}: {e}"
            )
            return
        if self._pending:
            logger.info(f"Restored {len(self._pending)} pending setup(s)")

    def _persist(self) -> None:
        if self._store_path is None:
            return
        try:
            _atomic_write_json(
                self._store_path, [s.to_dict() for s in self._pending.values()]
            )
        except Exception as e:
            logger.warning(f"Could not save pending setups to {self._store_path}: {e}")

    def add(
        self,
        server_id: str,
        manifest: Dict[str, Any],
        missing: List[str],
        goal: Any = None,
    ) -> PendingSetup:
        """Record that `server_id` needs `missing`; replaces an older entry for it."""
        for old in [s for s in self._pending.values() if s.server_id == server_id]:
            del self._pending[old.setup_id]
        setup = PendingSetup(
            setup_id=uuid.uuid4().hex[:8],
            server_id=server_id,
            server_name=str(manifest.get("name") or server_id),
            summary=str(manifest.get("summary") or manifest.get("description") or ""),
            fields=setting_fields(manifest.get("configurableProperties") or []),
            missing=list(missing),
            goal_id=getattr(goal, "id", None),
            goal_description=getattr(goal, "description", None),
        )
        self._pending[setup.setup_id] = setup
        self._persist()
        return setup

    def list(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._pending.values()]

    def find(self, ref: str) -> Optional[PendingSetup]:
        return find(self._pending.values(), ref)

    def pop(self, ref: str) -> Optional[PendingSetup]:
        setup = self.find(ref)
        if setup is not None:
            del self._pending[setup.setup_id]
            self._persist()
        return setup
