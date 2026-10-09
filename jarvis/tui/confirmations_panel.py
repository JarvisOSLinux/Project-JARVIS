"""Review pending and recently resolved confirmations without a modal (#235).

The modal (``confirm_modal``) is for the interrupt: something just asked.
This panel is for review: what is waiting, what did I approve earlier. Tab
focuses it, and resolving a request never needs the mouse or a modal that
steals the chat.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set

from textual.binding import Binding
from textual.widgets import Static

from . import offline
from .confirmations_view import (
    SCOPE_ALL,
    SCOPE_SESSION,
    SCOPES,
    Row,
    build_rows,
    decision_for,
    deny_message,
)

_SELECTABLE = ("pending", "task", "history")


class ConfirmationsPanel(Static, can_focus=True):
    """Keyboard-driven list of pending and resolved confirmations."""

    BINDINGS = [
        Binding("up,w", "cursor_up", "Up", show=False),
        Binding("down,s", "cursor_down", "Down", show=False),
        Binding("left,a", "scope_prev", "All", show=False),
        Binding("right,d", "scope_next", "This session", show=False),
        Binding("space", "toggle", "Toggle task", show=False),
        Binding("y,enter", "approve", "Approve", show=False),
        Binding("n", "deny", "Deny", show=False),
        Binding("escape", "leave", "Back to input", show=False),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", markup=True, **kwargs)
        self.scope = SCOPE_ALL
        self.cursor = 0
        self._rows: List[Row] = []
        # request id -> task indices left checked; absent means all of them.
        self._selections: Dict[str, Set[int]] = {}

    # ------------------------------------------------------------------
    # State -> rows -> text
    # ------------------------------------------------------------------

    def refresh_view(self) -> None:
        app = self.app
        pending = list(app.pending_confirmations)
        live = {p.get("id") for p in pending}
        self._selections = {k: v for k, v in self._selections.items() if k in live}
        queued = [] if app.connected else offline.queued_decisions()
        self._rows = build_rows(
            pending,
            app.confirmation_history,
            scope=self.scope,
            current_session_id=app.current_session_id,
            selections=self._selections,
            queued=queued,
        )
        selectable = self._selectable()
        self.cursor = min(self.cursor, max(0, len(selectable) - 1))
        self.display = bool(pending or app.confirmation_history)
        self.update(self._render_text(selectable))

    def _selectable(self) -> List[int]:
        return [i for i, r in enumerate(self._rows) if r.kind in _SELECTABLE]

    def _current_row(self) -> Optional[Row]:
        selectable = self._selectable()
        if not selectable:
            return None
        return self._rows[selectable[min(self.cursor, len(selectable) - 1)]]

    def _render_text(self, selectable: List[int]) -> str:
        names = {SCOPE_ALL: "All", SCOPE_SESSION: "This session"}
        tabs = "  ".join(
            f"[reverse] {names[s]} [/reverse]" if s == self.scope else names[s]
            for s in SCOPES
        )
        at = selectable[self.cursor] if selectable else -1
        lines = [f"[bold]Confirmations[/bold]  {tabs}", ""]
        for i, row in enumerate(self._rows):
            text = row.text.replace("[", r"\[")
            if row.kind == "note":
                lines.append(f"[bold dim]{text}[/bold dim]")
            elif i == at:
                lines.append(f"[reverse]{text}[/reverse]")
            else:
                lines.append(text)
        lines.append("")
        lines.append(
            "[dim]↑↓ move · space toggle · y apply · n deny · ←→ scope · Esc back[/dim]"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_cursor_up(self) -> None:
        self.cursor = max(0, self.cursor - 1)
        self.refresh_view()

    def action_cursor_down(self) -> None:
        self.cursor = min(max(0, len(self._selectable()) - 1), self.cursor + 1)
        self.refresh_view()

    def action_scope_prev(self) -> None:
        self._set_scope(-1)

    def action_scope_next(self) -> None:
        self._set_scope(1)

    def _set_scope(self, step: int) -> None:
        self.scope = SCOPES[(SCOPES.index(self.scope) + step) % len(SCOPES)]
        self.cursor = 0
        self.refresh_view()

    def action_leave(self) -> None:
        self.app.action_focus_input()

    def _entry(self, request_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return next(
            (p for p in self.app.pending_confirmations if p.get("id") == request_id),
            None,
        )

    def action_toggle(self) -> None:
        row = self._current_row()
        entry = self._entry(row.request_id) if row else None
        if row is None or entry is None:
            return
        count = len(entry.get("tool_lines") or [])
        chosen = set(self._selections.get(row.request_id, range(count)))
        if row.kind == "task" and row.task_index is not None:
            chosen ^= {row.task_index}
        elif row.kind == "pending":
            chosen = set() if len(chosen) == count else set(range(count))
        else:
            return
        self._selections[row.request_id] = chosen
        self.refresh_view()

    def action_approve(self) -> None:
        row = self._current_row()
        entry = self._entry(row.request_id) if row else None
        if entry is not None and row.kind in ("pending", "task"):
            self._send(decision_for(entry, self._selections.get(entry["id"])))

    def action_deny(self) -> None:
        row = self._current_row()
        entry = self._entry(row.request_id) if row else None
        if entry is not None and row.kind in ("pending", "task"):
            self._send(deny_message(entry))

    def _send(self, message: Dict[str, Any]) -> None:
        """Through the daemon when attached; otherwise onto the offline queue
        the daemon replays at its next start (the same one ``jarvis confirm``
        writes)."""
        app = self.app
        if app.connected:
            asyncio.ensure_future(app.client.send(message))
            return
        replaced = offline.queue_offline_decision(message)
        app._append_log(
            f"[dim]Queued {message['type'].split('_')[0]} for {message['id']}"
            f"{' (replaced an earlier decision)' if replaced else ''}; "
            "applied when the daemon starts.[/dim]"
        )
        self.refresh_view()
