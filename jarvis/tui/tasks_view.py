"""What JARVIS is doing now (#267): F4 or /tasks.

Requests still being worked on, the tools running for them with how long
they have run and their latest output, timers, and what waits on you. It
refreshes every second while open. Stopping a request or a task is #270,
still being specified; Ctrl+Q stops everything when quitting.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from .output import escape

OUTPUT_LINES = 6


def _ago(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def work_markup(work: Dict[str, Any], now: Optional[float] = None) -> str:
    """The tasks view's text, from runtime.work.collect_work."""
    now = time.time() if now is None else now
    lines = []
    requests = work.get("requests") or []
    lines.append("[bold]Requests[/bold]")
    for r in requests:
        since = _ago(now - r["since"]) if r.get("since") else ""
        lines.append(
            f"  • {escape(r['text'][:100])}"
            + (f"  [dim]{since}[/dim]" if since else "")
        )
    if not requests:
        lines.append("  [dim]none[/dim]")
    thinking = work.get("thinking") or 0
    if thinking:
        lines.append(
            f"  [dim]{thinking} waiting on the model[/dim]"
            if thinking > 1
            else "  [dim]1 waiting on the model[/dim]"
        )

    tasks = work.get("tasks") or []
    lines.append("")
    lines.append("[bold]Running[/bold]")
    for t in tasks:
        if t.get("kind") == "timer":
            lines.append(
                f"  [cyan]⏲[/cyan] {escape(t['title'])}  "
                f"[dim]fires in {_ago(t.get('fires_in'))}[/dim]"
            )
            continue
        ran = _ago(t.get("running_for"))
        lines.append(
            f"  [yellow]⋯[/yellow] {escape(t['title'])}"
            + (f"  [dim]running {ran}[/dim]" if ran else "")
        )
        if t.get("detail"):
            lines.append(f"    [dim]{escape(t['detail'][:200])}[/dim]")
        if t.get("request"):
            lines.append(f"    [dim]for: {escape(t['request'][:100])}[/dim]")
        output = [ln for ln in str(t.get("output") or "").splitlines() if ln.strip()]
        for line in output[-OUTPUT_LINES:]:
            lines.append(f"    │ {escape(line[:160])}")
    if not tasks:
        lines.append("  [dim]nothing[/dim]")

    waiting = []
    if work.get("confirmations"):
        n = work["confirmations"]
        waiting.append(f"{n} confirmation{'s' if n != 1 else ''} (jarvis confirm)")
    if work.get("setups"):
        n = work["setups"]
        waiting.append(f"{n} setup{'s' if n != 1 else ''} (F3)")
    lines.append("")
    lines.append("[bold]Waiting on you[/bold]")
    lines.append(f"  {', '.join(waiting)}" if waiting else "  [dim]nothing[/dim]")
    return "\n".join(lines)


class TasksScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("f4", "close", "Close", show=False),
    ]

    DEFAULT_CSS = """
    TasksScreen {
        align: center middle;
    }
    TasksScreen > #tasks-dialog {
        width: 100;
        max-width: 95%;
        height: auto;
        max-height: 85%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    TasksScreen #tasks-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    TasksScreen #tasks-body {
        height: auto;
        max-height: 30;
    }
    TasksScreen .tasks-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self, collect: Callable[[], Awaitable[Dict[str, Any]]]) -> None:
        super().__init__()
        self._collect = collect
        self._text = Static("Checking…", id="tasks-text")

    def compose(self) -> ComposeResult:
        with Vertical(id="tasks-dialog"):
            yield Label("What JARVIS is doing", id="tasks-title")
            with VerticalScroll(id="tasks-body"):
                yield self._text
            yield Static(
                "Updates every second · Esc or F4 closes · Ctrl+Q stops everything "
                "and quits",
                classes="tasks-hint",
            )

    async def on_mount(self) -> None:
        await self.refresh_work()
        self.set_interval(1.0, self.refresh_work)

    async def refresh_work(self) -> None:
        try:
            work = await self._collect()
        except Exception as e:  # a failing check must not take the view down
            self._text.update(Text(f"Could not read what is running: {e}"))
            return
        self._text.update(Text.from_markup(work_markup(work), emoji=False))

    def action_close(self) -> None:
        self.dismiss(None)
