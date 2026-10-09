"""Status bar helpers for the Textual TUI."""

from __future__ import annotations

from typing import Any

from textual.widgets import Static


def update_status(app: Any) -> None:
    if not app.connected:
        app.status_text = "  |  ".join(
            [
                f"● {app.offline_reason or 'connecting…'}",
                "Ctrl+Q quit · F1 help",
            ]
        )
        return

    status = app.daemon_status or {}
    session = status.get("session")
    parts = [f"session: {session['short_id']}" if session else "session: (none)"]
    parts.append(f"model: {status.get('model') or '(unset)'}")
    parts.append(f"provider: {status.get('provider') or '(none)'}")

    tokens = status.get("prompt_tokens") or 0
    if tokens > 0:
        window = status.get("context_window") or 0
        if window > 0:
            parts.append(f"ctx: {tokens}/{window} ({int(tokens / window * 100)}%)")
        else:
            parts.append(f"ctx: {tokens}tk")

    if app.daemon_state not in ("idle", "listening"):
        parts.append(app.daemon_state)
    if app.pending_confirmations:
        parts.append(f"{len(app.pending_confirmations)} awaiting approval")

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
