"""The chat as messages and folding blocks (#271).

It was one append-only RichLog, so a long output (a package upgrade,
`/status`) pushed the conversation out of sight. Now a message is a line; a
command you type is a block holding its output; and each action JARVIS takes
(a search, a tool run, an install, a confirmation) is a block with its
parameters and its result. A block folds to its title once something newer
arrives, unless you opened or closed it yourself; a long one scrolls inside a
capped height.

Browse mode (Ctrl+L or `/commands`): ↑ and ↓ move between blocks, Enter or
Space folds or unfolds one, Esc goes back to typing. Clicking a title does the
same. `/commands collapse` and `/commands expand` do every block at once.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

from .output import escape

_MARKS = {
    "running": "[yellow]⋯[/yellow]",
    "ok": "[green]✓[/green]",
    "failed": "[red]✗ failed[/red]",
    "stopped": "[yellow]■ stopped[/yellow]",
    "waiting": "[cyan]… waiting for you[/cyan]",
    "approved": "[green]✓ approved[/green]",
    "denied": "[red]✗ denied[/red]",
    "unverified": "[yellow]! unverified output[/yellow]",
}


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest:02d}s"


def _one_line(text: str, limit: int = 70) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


class BlockTitle(Static, can_focus=True):
    """A block's one line: click, Enter or Space folds it; ↑ and ↓ move on."""

    BINDINGS = [
        Binding("enter,space", "toggle", "Fold/unfold", show=False),
        Binding("up", "move(-1)", "Previous block", show=False),
        Binding("down", "move(1)", "Next block", show=False),
        Binding("escape", "leave", "Back to typing", show=False),
    ]

    def _block(self) -> "Block":
        return self.parent  # type: ignore[return-value]

    def action_toggle(self) -> None:
        self._block().toggle(by_user=True)

    def action_move(self, step: int) -> None:
        view = self._block().view
        if view is not None:
            view.focus_next_block(self._block(), step)

    def action_leave(self) -> None:
        try:
            self.app.query_one("#input").focus()
        except Exception:
            pass

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.focus()
        self._block().toggle(by_user=True)


class Block(Vertical):
    """A command and its output, or one action JARVIS took.

    Its content lives in two Statics made up front, so an action that starts
    and ends before the block is on screen loses nothing.
    """

    collapsed: reactive[bool] = reactive(False)

    def __init__(
        self,
        kind: str,
        title: str,
        detail: str = "",
        block_id: Optional[str] = None,
        started_at: Optional[float] = None,
    ) -> None:
        super().__init__(classes=f"block block-{kind}")
        self.kind = kind
        self.block_id = block_id
        self.title_markup = title
        self.status = "running" if kind != "command" else ""
        self.started_at = started_at
        self.ended_at: Optional[float] = None
        # Once you fold or unfold a block yourself, new messages leave it be.
        self.touched = False
        self.view: Optional[ChatView] = None
        self._detail = detail
        self._lines: List[Text] = [Text(detail, style="dim")] if detail else []
        self._has_output = False
        self._title = BlockTitle()
        self._text = Static(Text("\n").join(self._lines), classes="block-text")
        self._output = Static("", classes="block-output")
        self._body = VerticalScroll(self._text, self._output, classes="block-body")
        self._text.display = bool(self._lines)
        self._output.display = False
        self._render_title()

    def compose(self):
        yield self._title
        yield self._body

    @property
    def has_content(self) -> bool:
        return bool(self._lines) or self._has_output

    # --- content -----------------------------------------------------------

    def add_line(self, markup: str) -> None:
        self._lines.append(Text.from_markup(markup, emoji=False))
        self._text.update(Text("\n").join(self._lines))
        self._text.display = True
        self._render_title()

    def set_output(self, text: str) -> None:
        if not text:
            return
        self._output.update(Text(text))
        self._output.display = self._has_output = True
        if self._body.is_mounted:
            self._body.call_after_refresh(self._body.scroll_end, animate=False)
        self._render_title()

    def finish(self, status: str, output: str, ended_at: Optional[float]) -> None:
        self.status = status
        self.ended_at = ended_at
        self.set_output(output)
        self._render_title()

    # --- folding -----------------------------------------------------------

    def toggle(self, by_user: bool = False) -> None:
        if by_user:
            self.touched = True
        self.collapsed = not self.collapsed

    def watch_collapsed(self, collapsed: bool) -> None:
        self.set_class(collapsed, "-collapsed")
        self._render_title()

    def _render_title(self) -> None:
        parts = []
        if self.has_content:
            parts.append("▸" if self.collapsed else "▾")
        mark = _MARKS.get(self.status)
        if mark:
            parts.append(mark)
        parts.append(self.title_markup)
        if self.started_at is not None and self.ended_at is not None:
            parts.append(f"[dim]{_duration(self.ended_at - self.started_at)}[/dim]")
        # Last, as the title is cut to the width: a command line can be long.
        if self.kind == "tool" and self._detail:
            parts.append(f"[dim]{escape(_one_line(self._detail))}[/dim]")
        self._title.update(Text.from_markup(" ".join(parts), emoji=False))


class ChatView(VerticalScroll):
    """The chat: lines, command blocks and action blocks, newest at the bottom."""

    def __init__(self, *, id: Optional[str] = None) -> None:
        super().__init__(id=id)
        self._command: Optional[Block] = None
        self._actions: Dict[str, Block] = {}

    def on_mount(self) -> None:
        self.anchor()

    # --- adding --------------------------------------------------------------

    def _add(self, widget: Any) -> None:
        self._fold_older(widget)
        self.mount(widget)

    def write(self, markup: str) -> None:
        """A line: into the command block being answered, else on its own."""
        if self._command is not None:
            self._command.add_line(markup)
        else:
            self._add(Static(Text.from_markup(markup, emoji=False), classes="line"))

    def begin_command(self, title_markup: str) -> None:
        block = Block("command", title_markup)
        block.view = self
        self._add(block)
        self._command = block

    def end_command(self) -> None:
        self._command = None

    @property
    def answering(self) -> bool:
        """True while a command block takes the lines that follow."""
        return self._command is not None

    def on_action(self, event: Dict[str, Any]) -> Optional[Block]:
        """Draw an action event (runtime/action_events.py); returns its block."""
        action_id = str(event.get("id") or "")
        phase = event.get("phase")
        if phase == "start":
            self.end_command()
            block = Block(
                str(event.get("kind") or "action"),
                escape(str(event.get("title") or "")),
                str(event.get("detail") or ""),
                block_id=action_id,
                started_at=event.get("at"),
            )
            block.view = self
            self._actions[action_id] = block
            self._add(block)
            return block
        block = self._actions.get(action_id)
        if block is None:
            return None
        if phase == "update":
            block.set_output(str(event.get("output") or ""))
        elif phase == "end":
            self._actions.pop(action_id, None)
            block.finish(
                str(event.get("status") or "ok"),
                str(event.get("output") or ""),
                event.get("at"),
            )
        return block

    def clear(self) -> None:
        self._command = None
        self._actions.clear()
        self.remove_children()

    # --- folding and browsing -------------------------------------------------

    def blocks(self) -> List[Block]:
        return [w for w in self.children if isinstance(w, Block)]

    def _fold_older(self, newest: Any) -> None:
        for block in self.blocks():
            if block is not newest and not block.touched and not block.collapsed:
                block.collapsed = True

    def set_all(self, collapsed: bool) -> int:
        blocks = self.blocks()
        for block in blocks:
            block.touched = True
            block.collapsed = collapsed
        return len(blocks)

    def _browsable(self) -> List[Block]:
        return [b for b in self.blocks() if b.has_content]

    def browse(self) -> bool:
        """Focus the newest block; False when there is none."""
        blocks = self._browsable()
        if not blocks:
            self.focus()
            return False
        blocks[-1]._title.focus()
        return True

    def focus_next_block(self, current: Block, step: int) -> None:
        blocks = self._browsable()
        if current not in blocks:
            return
        index = blocks.index(current) + step
        if 0 <= index < len(blocks):
            blocks[index]._title.focus()


def export_line(event: Dict[str, Any]) -> str:
    """An action event as a plain transcript line (for /export)."""
    if event.get("phase") == "start":
        detail = _one_line(str(event.get("detail") or ""), 200)
        return f"· {event.get('title', '')}" + (f": {detail}" if detail else "")
    status = str(event.get("status") or "")
    output = str(event.get("output") or "").strip()
    return f"  {status}" + (f": {output}" if output else "")
