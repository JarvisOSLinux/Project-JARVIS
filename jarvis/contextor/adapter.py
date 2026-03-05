"""
ContextorAdapter — manages long-term memory for JARVIS.

Provides a local file-based memory backend that works out of the box.
Memory is organized by theme — each theme gets its own JSON file under
``~/.jarvis/memory/``. Themes are created on demand.

When the Rust contextor binary is ready, this adapter can be extended
to delegate to it via stdio (same pattern as DispatchAdapter).
"""

import json
import os
import time
from typing import Dict, Any, List, Optional
from ..core.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_MEMORY_DIR = os.path.join(os.path.expanduser("~"), ".jarvis", "memory")


class ContextorAdapter:
    """File-based long-term memory for JARVIS."""

    def __init__(self, memory_dir: str | None = None):
        self._memory_dir = memory_dir or _DEFAULT_MEMORY_DIR
        os.makedirs(self._memory_dir, exist_ok=True)
        logger.info(f"Contextor: Memory directory: {self._memory_dir}")

    def _theme_path(self, theme: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in theme.lower())
        return os.path.join(self._memory_dir, f"{safe}.jsonl")

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def store(self, theme: str, content: str) -> Dict[str, Any]:
        """
        Store a piece of information under a theme.

        Each entry is timestamped. Themes are created on demand.
        """
        entry = {
            "content": content,
            "stored_at": time.time(),
            "stored_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        path = self._theme_path(theme)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            logger.info(f"Contextor: Stored under theme '{theme}' ({len(content)} chars)")
            return {"stored": True, "theme": theme}
        except OSError as e:
            logger.error(f"Contextor: Failed to store under '{theme}': {e}")
            return {"error": f"Failed to store: {e}"}

    def recall(self, theme: str, limit: int = 20) -> Dict[str, Any]:
        """
        Recall entries stored under a theme.
        Returns the most recent ``limit`` entries (newest last).
        """
        path = self._theme_path(theme)
        if not os.path.exists(path):
            logger.info(f"Contextor: No memory found for theme '{theme}'")
            return {"theme": theme, "entries": [], "found": False}

        entries: list[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError as e:
            logger.error(f"Contextor: Failed to read theme '{theme}': {e}")
            return {"error": f"Failed to recall: {e}"}

        recent = entries[-limit:]
        logger.info(f"Contextor: Recalled {len(recent)} entries for theme '{theme}'")
        return {"theme": theme, "entries": recent, "found": True}

    def search(self, keywords: List[str], limit: int = 20) -> Dict[str, Any]:
        """
        Search across all themes for entries matching any of the keywords.
        """
        keywords_lower = [k.lower() for k in keywords]
        matches: list[dict] = []

        try:
            for filename in os.listdir(self._memory_dir):
                if not filename.endswith(".jsonl"):
                    continue

                theme = filename[:-6]  # strip .jsonl
                path = os.path.join(self._memory_dir, filename)

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                entry = json.loads(line)
                            except json.JSONDecodeError:
                                continue

                            text = entry.get("content", "").lower()
                            if any(kw in text for kw in keywords_lower):
                                matches.append({**entry, "theme": theme})
                                if len(matches) >= limit:
                                    break
                except OSError:
                    continue

                if len(matches) >= limit:
                    break
        except OSError as e:
            logger.error(f"Contextor: Failed to search memory: {e}")
            return {"error": f"Search failed: {e}", "results": []}

        logger.info(f"Contextor: Search found {len(matches)} match(es) for keywords {keywords}")
        return {"results": matches}

    def list_themes(self) -> Dict[str, Any]:
        """
        List all stored themes with entry counts.
        """
        themes: list[dict] = []

        try:
            for filename in sorted(os.listdir(self._memory_dir)):
                if not filename.endswith(".jsonl"):
                    continue

                theme = filename[:-6]
                path = os.path.join(self._memory_dir, filename)

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        count = sum(1 for line in f if line.strip())
                    stat = os.stat(path)
                    themes.append({
                        "theme": theme,
                        "entries": count,
                        "last_modified": time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime),
                        ),
                    })
                except OSError:
                    continue
        except OSError as e:
            logger.error(f"Contextor: Failed to list themes: {e}")
            return {"error": f"List failed: {e}", "themes": []}

        logger.info(f"Contextor: {len(themes)} theme(s) in memory")
        return {"themes": themes}

    def delete_theme(self, theme: str) -> Dict[str, Any]:
        """Delete all entries for a theme."""
        path = self._theme_path(theme)
        if not os.path.exists(path):
            return {"deleted": False, "reason": "Theme not found"}

        try:
            os.remove(path)
            logger.info(f"Contextor: Deleted theme '{theme}'")
            return {"deleted": True, "theme": theme}
        except OSError as e:
            logger.error(f"Contextor: Failed to delete theme '{theme}': {e}")
            return {"error": f"Failed to delete: {e}"}