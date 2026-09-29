"""Persistent store for MCP server configurable parameters.

Values are saved to ~/.config/jarvis/jarvis_params.toml keyed by server ID.
They survive server uninstall/reinstall and are never passed to the LLM.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Dict

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


def _params_path() -> Path:
    from ..platform import current as platform

    path = platform.config_dir() / "jarvis_params.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load() -> Dict[str, Dict[str, str]]:
    path = _params_path()
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        # Only string values are ever written. Anything else came from the old
        # unquoted headers, which TOML read as nested tables, and stringifying
        # those dicts is what wrote `github = "{'missionsquad': ...}"` back out.
        loaded = {
            section: {k: v for k, v in values.items() if isinstance(v, str)}
            for section, values in data.items()
            if isinstance(values, dict)
        }
        return {section: values for section, values in loaded.items() if values}
    except Exception:
        return {}


def _toml_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _save(data: Dict[str, Dict[str, str]]) -> None:
    path = _params_path()
    lines: list[str] = []
    for section, values in data.items():
        # Quoted, so a reverse-DNS server id stays one table. Bare, TOML reads
        # [io.github.x.y] as four nested tables, and the store has never
        # round-tripped a single server as a result (#242).
        lines.append(f"[{_toml_string(section)}]")
        for key, value in values.items():
            lines.append(f"{_toml_string(key)} = {_toml_string(value)}")
        lines.append("")
    content = "\n".join(lines)
    # Atomic write: temp file → rename
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".jarvis_params_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ParamsStore:
    """Read/write jarvis_params.toml for a specific server."""

    def __init__(self, server_id: str) -> None:
        self._server_id = server_id

    def get(self) -> Dict[str, str]:
        """Return all saved key-value pairs for this server."""
        return _load().get(self._server_id, {})

    def set(self, key: str, value: str) -> None:
        data = _load()
        data.setdefault(self._server_id, {})[key] = value
        _save(data)

    def set_many(self, values: Dict[str, str]) -> None:
        data = _load()
        data.setdefault(self._server_id, {}).update(values)
        _save(data)
