"""TUI-local input handlers and transcript export helper."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import Config
from ..core.providers import (
    add_provider,
    edit_provider,
    list_providers,
    move_provider,
    parse_flags,
    remove_provider,
)
from .help_screen import HelpScreen
from .slash_commands_doc import build_help_markdown


def handle_local_input(app: Any, text: str, bindings: Any) -> bool:
    """Handle TUI-only slash commands. Return True when handled."""
    low = text.lower().strip()
    if low in ("/help", "/?"):
        app.push_screen(HelpScreen(build_help_markdown(bindings)))
        return True

    if low == "/export" or low.startswith("/export "):
        parts = text.split(maxsplit=1)
        name_arg = parts[1].strip() if len(parts) > 1 else None
        export_transcript_to_disk(app, name_arg)
        return True

    if low == "/clear":
        from .actions import clear_transcript

        clear_transcript(app)
        return True

    if low in ("/quit", "/exit"):
        app.exit()
        return True

    if low == "/status":
        _show_status(app)
        return True

    if low == "/providers" or low.startswith("/providers "):
        _handle_providers(app, text)
        return True

    if low == "/model" or low.startswith("/model "):
        _handle_model(app, text)
        return True

    return False


def _show_status(app: Any) -> None:
    """Display current provider, model, and session info."""
    model = getattr(Config, "LLM_MODEL", None) or "(unset)"
    provider_name = getattr(Config, "LLM_PROVIDER", "?")

    pool = None
    if app.jarvis is not None and hasattr(app.jarvis, "llm"):
        pool = getattr(app.jarvis.llm, "provider", None)

    if pool is not None and hasattr(pool, "active_provider_name"):
        provider_name = pool.active_provider_name or provider_name
        model = getattr(pool, "model", model)

    session_id = "(none)"
    if app.jarvis is not None and app.jarvis.sessions.current:
        session_id = app.jarvis.sessions.current.short_id()

    lines = [
        "[bold cyan]Status[/bold cyan]",
        f"  provider: {provider_name}",
        f"  model: {model}",
        f"  session: {session_id}",
    ]
    app._append_log("\n".join(lines))


def _show_providers(app: Any) -> None:
    """Display configured providers with live pool status if available."""
    pool_status = {}
    if app.jarvis is not None and hasattr(app.jarvis, "llm"):
        pool = getattr(app.jarvis.llm, "provider", None)
        if pool is not None and hasattr(pool, "get_status"):
            for info in pool.get_status():
                pool_status[info["name"]] = info

    providers = list_providers()
    if not providers:
        app._append_log(
            "[dim]No providers configured. "
            f"Using legacy: {Config.LLM_PROVIDER} / {Config.LLM_MODEL}[/dim]"
        )
        return

    lines = [f"[bold cyan]Provider pool[/bold cyan] ({len(providers)} providers)"]
    for i, p in enumerate(providers):
        name = p.get("name", f"provider-{i}")
        ptype = p.get("type", "?")
        model = p.get("model", "?")

        status_str = ""
        live = pool_status.get(name)
        if live:
            s = live["status"]
            if s == "active":
                status_str = " [green]active[/green]"
            elif s in ("cooldown", "exhausted"):
                remaining = live.get("cooldown_remaining", "?")
                status_str = f" [yellow]{s} ({remaining}s)[/yellow]"
            elif s == "error":
                status_str = " [red]error[/red]"

        lines.append(f"  [{i + 1}] {name} — {ptype}/{model}{status_str}")

    app._append_log("\n".join(lines))


def _handle_providers(app: Any, text: str) -> None:
    """Route /providers subcommands."""
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        _show_providers(app)
        return

    rest = parts[1].strip()
    tokens = rest.split()
    subcmd = tokens[0].lower()

    if subcmd == "add":
        flags = parse_flags(tokens[1:])
        ptype = flags.get("type", "")
        model = flags.get("model", "")
        if not ptype or not model:
            app._append_log(
                "[yellow]Usage: /providers add --type <ollama|api> "
                "--model <model> [--name <n>] [--url <u>] [--key <k>][/yellow]"
            )
            return
        try:
            name, position = add_provider(
                ptype,
                model,
                name=flags.get("name"),
                url=flags.get("url"),
                api_key=flags.get("key"),
            )
            app._append_log(
                f"[green]Added provider '{name}' ({ptype}/{model}) "
                f"at position {position}[/green]"
            )
        except ValueError as e:
            app._append_log(f"[red]Error: {e}[/red]")

    elif subcmd == "remove":
        if len(tokens) < 2:
            app._append_log("[yellow]Usage: /providers remove <name>[/yellow]")
            return
        try:
            remove_provider(tokens[1])
            app._append_log(f"[green]Removed provider '{tokens[1]}'[/green]")
        except ValueError as e:
            app._append_log(f"[red]Error: {e}[/red]")

    elif subcmd == "move":
        if len(tokens) < 3:
            app._append_log("[yellow]Usage: /providers move <name> <position>[/yellow]")
            return
        try:
            pos = int(tokens[2])
        except ValueError:
            app._append_log(f"[red]Error: Position must be a number[/red]")
            return
        try:
            move_provider(tokens[1], pos)
            app._append_log(
                f"[green]Moved provider '{tokens[1]}' to position {pos}[/green]"
            )
        except ValueError as e:
            app._append_log(f"[red]Error: {e}[/red]")

    elif subcmd == "edit":
        if len(tokens) < 2:
            app._append_log(
                "[yellow]Usage: /providers edit <name> "
                "--model <m> --url <u> --key <k>[/yellow]"
            )
            return
        name = tokens[1]
        flags = parse_flags(tokens[2:])
        if not flags:
            app._append_log(
                "[yellow]No fields to update. "
                "Use --model, --url, --key, or --type[/yellow]"
            )
            return
        try:
            updated = edit_provider(name, **flags)
            app._append_log(
                f"[green]Updated provider '{name}': {', '.join(updated)}[/green]"
            )
        except ValueError as e:
            app._append_log(f"[red]Error: {e}[/red]")

    else:
        app._append_log(
            f"[yellow]Unknown subcommand '{subcmd}'. "
            "Use: add, remove, move, edit[/yellow]"
        )


def _handle_model(app: Any, text: str) -> None:
    """Show or switch the current model."""
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        model = getattr(Config, "LLM_MODEL", None) or "(unset)"
        pool = None
        if app.jarvis is not None and hasattr(app.jarvis, "llm"):
            pool = getattr(app.jarvis.llm, "provider", None)
        if pool is not None:
            model = getattr(pool, "model", model)
        app._append_log(f"[bold cyan]Model:[/bold cyan] {model}")
        return

    new_model = parts[1].strip()
    from .status_bar import update_status

    try:
        from ..cli import _update_env_setting

        _update_env_setting("LLM_MODEL", new_model)
        app._append_log(f"[green]Model set to: {new_model}[/green]")
        app._append_log("[dim]Note: takes effect on next LLM request or restart.[/dim]")
        update_status(app)
    except Exception as e:
        app._append_log(f"[red]Error setting model: {e}[/red]")


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
