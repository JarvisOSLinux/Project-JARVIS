"""TUI-local input handlers (/help, /export) and transcript export helper."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import Config
from .help_screen import HelpScreen
from .slash_commands_doc import build_help_markdown


def handle_local_input(app: Any, text: str, bindings: Any) -> bool:
    """Handle TUI-only slash commands. Return True when handled."""
    low = text.lower()
    if low in ("/help", "/?"):
        app.push_screen(HelpScreen(build_help_markdown(bindings)))
        return True

    if low == "/export" or low.startswith("/export "):
        parts = text.split(maxsplit=1)
        name_arg = parts[1].strip() if len(parts) > 1 else None
        export_transcript_to_disk(app, name_arg)
        return True

    return False


def export_transcript_to_disk(app: Any, filename: Optional[str]) -> None:
    """Save plain transcript as Markdown under ``JARVIS_DATA_DIR/transcripts``."""
    root = Path(Config.JARVIS_DATA_DIR).expanduser().resolve()
    out_dir = root / "transcripts"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        app._append_log(f"[red]Export failed (mkdir): {e}[/red]")
        return

    if filename:
        base = os.path.basename(filename.strip())
        if not base or base in (".", ".."):
            app._append_log("[red]Export: invalid filename.[/red]")
            return
        if not base.lower().endswith(".md"):
            base = f"{base}.md"
    else:
        sid = "no-session"
        if app.jarvis is not None and app.jarvis.sessions.current:
            sid = app.jarvis.sessions.current.short_id()
        base = f"{sid}_{int(time.time())}.md"

    out_resolved = out_dir.resolve()
    path = (out_resolved / base).resolve()
    try:
        path.relative_to(out_resolved)
    except ValueError:
        app._append_log("[red]Export: path must stay under transcripts/.[/red]")
        return

    sid2 = "none"
    model = getattr(Config, "LLM_MODEL", None) or "(unset)"
    if app.jarvis is not None and app.jarvis.sessions.current:
        sid2 = app.jarvis.sessions.current.id

    header = "\n".join(
        [
            "# JARVIS transcript export",
            "",
            f"- Exported (UTC): {datetime.now(timezone.utc).isoformat()}",
            f"- Session id: `{sid2}`",
            f"- Model: `{model}`",
            "",
            "---",
            "",
        ]
    )
    body_lines = (
        app._export_lines if app._export_lines else ["_(no transcript lines yet)_"]
    )
    body = "\n".join(body_lines)
    text_out = header + body + "\n"

    try:
        path.write_text(text_out, encoding="utf-8")
    except OSError as e:
        app._append_log(f"[red]Export failed: {e}[/red]")
        return

    app._append_log(f"[green]Exported transcript to[/green] {path}")
