"""Status bar helpers for the Textual TUI."""

from __future__ import annotations

from typing import Any

from textual.widgets import Static

from ..config import Config


def update_status(app: Any) -> None:
    parts = []

    if getattr(app, "engine_error", None) is not None:
        # One unmistakable line rather than a row of "(none)" fields that read
        # like a slow startup. The panel still works, so say that too.
        pending, _ = app.read_confirmations()
        waiting = f"{len(pending)} pending" if pending else "none pending"
        app.status_text = (
            f"⚠ engine offline — confirmations reviewable ({waiting}), "
            "decisions queue  |  F3 confirmations · F1 help · Ctrl+Q quit"
        )
        return

    if app.jarvis is not None and app.jarvis.sessions.current:
        parts.append(f"session: {app.jarvis.sessions.current.short_id()}")
    else:
        parts.append("session: (none)")

    model = "(unset)"
    provider = "(none)"

    if app.jarvis is not None and hasattr(app.jarvis, "llm"):
        pool = getattr(app.jarvis.llm, "provider", None)
        if pool is not None and hasattr(pool, "active_provider_name"):
            provider = pool.active_provider_name or provider
            model = getattr(pool, "model", model)

    parts.append(f"model: {model}")
    parts.append(f"provider: {provider}")

    if app.jarvis is not None and hasattr(app.jarvis, "llm"):
        raw_provider = getattr(app.jarvis.llm, "provider", None)
        prompt_toks = getattr(raw_provider, "last_prompt_tokens", 0) or 0
        if prompt_toks > 0:
            window = getattr(Config, "LLM_CONTEXT_WINDOW", 0)
            if window > 0:
                pct = int(prompt_toks / window * 100)
                parts.append(f"ctx: {prompt_toks}/{window} ({pct}%)")
            else:
                parts.append(f"ctx: {prompt_toks}tk")

    try:
        pending, _ = app.read_confirmations()
    except Exception:
        pending = []
    if pending:
        parts.append(f"⚠ {len(pending)} pending (F3)")

    parts.append(
        "Ctrl+N new · Ctrl+D delete · Ctrl+Q quit · Ctrl+L log · Ctrl+I input · F1 help · "
        "F3 confirms · Ctrl+Shift+C clear · Ctrl+Shift+E export"
    )

    app.status_text = "  |  ".join(parts)


def watch_status_text(app: Any, value: str) -> None:
    try:
        app.query_one("#status-bar", Static).update(value)
    except Exception:
        pass


def set_status(app: Any, text: str) -> None:
    app.status_text = text
