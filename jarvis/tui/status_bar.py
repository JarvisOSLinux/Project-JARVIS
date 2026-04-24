"""Status bar helpers for the Textual TUI."""

from __future__ import annotations

from typing import Any

from textual.widgets import Static

from ..config import Config


def update_status(app: Any) -> None:
    parts = []
    if app.jarvis is not None and app.jarvis.sessions.current:
        parts.append(f"session: {app.jarvis.sessions.current.short_id()}")
    else:
        parts.append("session: (none)")

    model = getattr(Config, "LLM_MODEL", None) or "(unset)"
    provider = getattr(Config, "LLM_PROVIDER", "?")
    parts.append(f"model: {model}")
    parts.append(f"provider: {provider}")
    parts.append(
        "Ctrl+N new · Ctrl+D delete · Ctrl+Q quit · Ctrl+L log · Ctrl+I input · F1 help · "
        "Ctrl+Shift+C clear · Ctrl+Shift+E export"
    )

    app.status_text = "  |  ".join(parts)


def watch_status_text(app: Any, value: str) -> None:
    try:
        app.query_one("#status-bar", Static).update(value)
    except Exception:
        pass


def set_status(app: Any, text: str) -> None:
    app.status_text = text
