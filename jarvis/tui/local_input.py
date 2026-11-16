"""TUI-local input handlers and transcript export helper."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from ..config import Config
from ..core.providers import (
    add_provider,
    edit_provider,
    list_providers,
    move_provider,
    parse_flags,
    remove_provider,
    take_vision_flag,
)
from ..platform.ownership import keep_owner, make_dirs
from .help_screen import HelpScreen
from .output import escape
from .provider_modal import ProviderModal


def handle_local_input(app: Any, text: str, bindings: Any) -> bool:
    """Handle TUI-only slash commands. Return True when handled."""
    low = text.lower().strip()
    if low.split(maxsplit=1)[0] in ("/help", "/?"):
        words = text.strip().split(maxsplit=1)
        app.push_screen(HelpScreen(bindings, words[1] if len(words) > 1 else ""))
        return True

    if low == "/export" or low.startswith("/export "):
        parts = text.split(maxsplit=1)
        name_arg = parts[1].strip() if len(parts) > 1 else None
        export_transcript_to_disk(app, name_arg)
        return True

    if low == "/commands" or low.startswith("/commands "):
        _handle_commands(app, low)
        return True

    if low == "/clear":
        from .actions import clear_transcript

        clear_transcript(app)
        return True

    if low in ("/quit", "/exit"):
        app.exit()
        return True

    if low in ("/status", "/status full"):
        _show_status(app, full=low.endswith("full"))
        return True

    if low == "/providers" or low.startswith("/providers "):
        _handle_providers(app, text)
        return True

    if low == "/model" or low.startswith("/model "):
        _handle_model(app, text)
        return True

    if low == "/settings":
        app._open_config("settings")
        return True

    if low == "/setups":
        app._open_setups()
        return True

    if low == "/tasks":
        app._open_tasks()
        return True

    if low == "/attach" or low.startswith("/attach "):
        _handle_attach(app, text)
        return True

    return False


def _handle_attach(app: Any, text: str) -> None:
    """`/attach`: pick a file, attach one by path, or `clear` what is attached."""
    from ..runtime import attachments

    parts = text.strip().split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        from .attach_picker import AttachPicker

        def picked(path: Optional[Path]) -> None:
            if path is not None:
                attach_paths(app, [path])

        app.push_screen(AttachPicker(), picked)
        return
    if arg.lower() == "clear":
        pending, app._pending_attachments = app._pending_attachments, []
        attachments.discard(pending)
        show_attached(app)
        app._append_log(
            f"[dim]Dropped {len(pending)} attachment(s).[/dim]"
            if pending
            else "[dim]Nothing is attached.[/dim]"
        )
        return
    attach_paths(app, [arg.strip("'\"")])


def attach_paths(app: Any, paths: Sequence[str | os.PathLike]) -> None:
    """Copy files into the pending attachments; a file that can't be attached
    says why in the chat."""
    from ..runtime import attachments

    for path in paths:
        if len(app._pending_attachments) >= attachments.MAX_PER_MESSAGE:
            app._append_log(
                f"[red]A message can carry at most {attachments.MAX_PER_MESSAGE} "
                "attachments.[/red]"
            )
            break
        try:
            app._pending_attachments.append(attachments.store_file(path))
        except attachments.AttachmentError as e:
            app._append_log(f"[red]{escape(str(e))}[/red]")
    show_attached(app)


def show_attached(app: Any) -> None:
    """The line above the input naming what goes with the next message."""
    from rich.text import Text
    from textual.widgets import Static

    from ..runtime.attachments import display_line

    line = display_line(app._pending_attachments)
    try:
        widget = app.query_one("#attached", Static)
    except Exception:
        return
    # Text, not markup: a file name may hold "[".
    widget.update(
        Text(f"{line} — sent with your next message; /attach clear drops them")
        if line
        else ""
    )
    widget.set_class(not line, "-idle")


def _handle_commands(app: Any, low: str) -> None:
    """`/commands`: browse the chat's blocks, or fold or unfold them all (#271)."""
    from .chat_view import ChatView

    chat = app.query_one("#chat-log", ChatView)
    arg = low.split(maxsplit=1)[1].strip() if " " in low else ""
    if arg not in ("", "collapse", "expand"):
        app._append_log("[yellow]Usage: /commands [collapse|expand][/yellow]")
        return
    chat.end_command()
    if arg:
        chat.set_all(arg == "collapse")
    elif not chat.browse():
        app._append_log("[dim]Nothing to browse yet.[/dim]")


_MARKS = {
    "ok": "[green]✓[/green]",
    "warn": "[yellow]![/yellow]",
    "fail": "[red]✗[/red]",
    "off": "[dim]–[/dim]",
}


def status_markup(sections: Any) -> str:
    """The health check (#272) as chat lines: one mark, name and reason per check."""
    lines = ["[bold cyan]Status[/bold cyan]"]
    for section in sections:
        lines.append(f"[bold]{escape(section.title)}[/bold]")
        for check in section.checks:
            mark = _MARKS.get(check.state, "?")
            detail = f": {escape(check.detail)}" if check.detail else ""
            lines.append(f"  {mark} {escape(check.name)}{detail}")
    return "\n".join(lines)


def _show_status(app: Any, *, full: bool = False) -> None:
    """Check JARVIS's health without blocking the TUI, then print the result."""
    if app.jarvis is None:
        app._append_log("[yellow]JARVIS is still starting up.[/yellow]")
        return
    from ..runtime.health import collect_status

    if full:
        app._append_log("[dim]Checking, including registry updates…[/dim]")

    async def run() -> None:
        try:
            sections = await collect_status(app.jarvis, full=full)
        except Exception as e:  # a broken check must not take the TUI down
            app._append_log(f"[red]Status check failed: {escape(str(e))}[/red]")
            return
        app._append_log(status_markup(sections))

    app.run_worker(run(), exclusive=False)


def _handle_providers(app: Any, text: str) -> None:
    """Route /providers subcommands."""
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        app._open_config("providers")
        return

    rest = parts[1].strip()
    tokens = rest.split()
    subcmd = tokens[0].lower()

    if subcmd == "add":
        args, vision = take_vision_flag(tokens[1:])
        flags = parse_flags(args)
        ptype = flags.get("type", "")
        model = flags.get("model", "")
        if not ptype or not model:
            # No flags — open the modal form
            def _on_add(result) -> None:
                if not result or not result.confirmed:
                    return
                try:
                    pname, position = add_provider(
                        result.ptype,
                        result.model,
                        name=result.name or None,
                        url=result.url or None,
                        api_key=result.api_key or None,
                        temperature=result.temperature,
                        vision=result.vision,
                    )
                    app._append_log(
                        f"[green]Added provider '{pname}' ({result.ptype}/{result.model}) "
                        f"at position {position}[/green]"
                    )
                except ValueError as e:
                    app._append_log(f"[red]Error: {e}[/red]")

            app.push_screen(ProviderModal(mode="add"), _on_add)
            return
        try:
            name, position = add_provider(
                ptype,
                model,
                name=flags.get("name"),
                url=flags.get("url"),
                api_key=flags.get("key"),
                vision=bool(vision),
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
                "[--model <m>] [--url <u>] [--key <k>] [--vision <true|false>][/yellow]"
            )
            return
        name = tokens[1]
        args, vision = take_vision_flag(tokens[2:])
        flags = parse_flags(args)
        if vision is not None:
            flags["vision"] = vision
        if not flags:
            # No flags — open pre-filled modal
            existing = next(
                (p for p in list_providers() if p.get("name") == name), None
            )
            if existing is None:
                app._append_log(f"[red]Error: Provider '{name}' not found[/red]")
                return

            def _on_edit(result) -> None:
                if not result or not result.confirmed:
                    return
                fields: dict = {}
                if result.model:
                    fields["model"] = result.model
                if result.url:
                    fields["url"] = result.url
                if result.api_key:
                    fields["key"] = result.api_key
                if result.ptype:
                    fields["type"] = result.ptype
                if result.temperature is not None:
                    fields["temperature"] = result.temperature
                fields["vision"] = result.vision
                try:
                    updated = edit_provider(name, **fields)
                    app._append_log(
                        f"[green]Updated provider '{name}': {', '.join(updated)}[/green]"
                    )
                except ValueError as e:
                    app._append_log(f"[red]Error: {e}[/red]")

            app.push_screen(ProviderModal(mode="edit", existing=existing), _on_edit)
            return
        try:
            updated = edit_provider(name, **flags)
            app._append_log(
                f"[green]Updated provider '{name}': {', '.join(updated)}[/green]"
            )
        except TypeError:  # `--name`: the name is edit_provider's own argument
            app._append_log(
                "[red]Error: A provider's name can't be edited; remove it and "
                "add it again under the new name.[/red]"
            )
        except ValueError as e:
            app._append_log(f"[red]Error: {e}[/red]")

    else:
        app._append_log(
            f"[yellow]Unknown subcommand '{subcmd}'. "
            "Use: add, remove, move, edit[/yellow]"
        )


def _handle_model(app: Any, text: str) -> None:
    """Show the current model; changing one is `/providers edit`."""
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 1:
        model = "(unset)"
        pool = None
        if app.jarvis is not None and hasattr(app.jarvis, "llm"):
            pool = getattr(app.jarvis.llm, "provider", None)
        if pool is not None:
            model = getattr(pool, "model", model)
        app._append_log(f"[bold cyan]Model:[/bold cyan] {model}")
        return

    app._append_log(
        "[yellow]Use /providers edit <name> --model <model> to change a provider's model.[/yellow]"
    )


def export_transcript_to_disk(app: Any, filename: Optional[str]) -> None:
    """Save plain transcript as Markdown under ``JARVIS_DATA_DIR/transcripts``."""
    root = Path(Config.JARVIS_DATA_DIR).expanduser().resolve()
    out_dir = root / "transcripts"
    try:
        make_dirs(out_dir)
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
    model = "(unset)"
    if app.jarvis is not None and hasattr(app.jarvis, "llm"):
        pool = getattr(app.jarvis.llm, "provider", None)
        if pool is not None:
            model = getattr(pool, "model", model)
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
        keep_owner(path)
    except OSError as e:
        app._append_log(f"[red]Export failed: {e}[/red]")
        return

    app._append_log(f"[green]Exported transcript to[/green] {path}")
