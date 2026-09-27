"""Confirmations review panel — the non-modal half of the confirmation UI (#235).

``ConfirmModal`` handles the *interrupt*: a tool is blocked right now and the
user is being asked. This panel handles the *review*: everything still pending
(including entries restored from a previous run, which no modal will ever fire
for again) plus what was recently resolved.

Two properties shape the design:

* **Resolving never leaves the keyboard and never steals the chat.** The panel
  is an ordinary focusable widget, not a screen, so the transcript stays
  visible and the message input stays one Tab away.
* **Decisions take the same route as every other client's.** A row builds the
  same socket-protocol message a GUI client would send and hands it to
  ``io.apply_confirmation_decision`` — so per-task indices, approve-all
  expansion, the GUI broadcast and the #146 goal-gone resume path behave
  identically here. With no engine, that message is appended to the offline
  queue instead, exactly as ``jarvis confirm`` does.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView

# Scope of the pending list: everything, or only the active chat session.
SCOPE_ALL = "all"
SCOPE_SESSION = "session"

_OUTCOME_MARK = {"approved": "[green]✓[/green]", "denied": "[red]✗[/red]"}
_PARTIAL_MARK = "[yellow]◑[/yellow]"


def format_age(created_at: float, now: Optional[float] = None) -> str:
    """Compact age for a confirmation row: 12s, 4m, 3h, 2d."""
    delta = max(0.0, (now if now is not None else time.time()) - (created_at or 0))
    if delta < 60:
        return f"{int(delta)}s"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return f"{int(delta // 86400)}d"


def filter_by_scope(
    entries: List[Dict[str, Any]],
    scope: str,
    current_session_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Apply the All / This-session scope to a pending or history list.

    Entries predating the ``chat_session_id`` field (#235) carry None and are
    kept in session scope rather than hidden: an unattributable confirmation
    that silently disappears from the only filtered view is worse than one
    shown in a view it may not belong to.
    """
    if scope != SCOPE_SESSION or not current_session_id:
        return list(entries)
    return [
        e for e in entries if e.get("chat_session_id") in (None, current_session_id)
    ]


class _Row(ListItem):
    """Base for every row so the panel can ask what it is highlighting."""

    selectable_row = True

    def __init__(self, markup: str) -> None:
        super().__init__(Label(markup, markup=True))


class _SectionRow(_Row):
    """A non-actionable heading (``Pending (2)`` / ``History``)."""

    selectable_row = False

    def __init__(self, markup: str) -> None:
        super().__init__(markup)
        self.disabled = True


class _PendingRow(_Row):
    """One pending confirmation. Expands to reveal its tasks."""

    def __init__(self, entry: Dict[str, Any], expanded: bool, markup: str) -> None:
        super().__init__(markup)
        self.entry = entry
        self.confirmation_id: str = entry["id"]
        self.expanded = expanded


class _TaskRow(_Row):
    """One task inside an expanded confirmation, with its own checkbox."""

    def __init__(self, confirmation_id: str, index: int, markup: str) -> None:
        super().__init__(markup)
        self.confirmation_id = confirmation_id
        self.index = index


class _HistoryRow(_Row):
    """A resolved confirmation, read-only."""

    def __init__(self, record: Dict[str, Any], markup: str) -> None:
        super().__init__(markup)
        self.record = record


class ConfirmationsPanel(Vertical):
    """Right-hand review panel for pending and resolved confirmations."""

    BINDINGS = [
        # Enter is not bound here: ListView consumes it to emit Selected, which
        # on_list_view_selected() turns into the same toggle — and that also
        # makes a mouse click expand a row.
        Binding("space", "toggle_task", "Toggle task", show=False),
        Binding("a", "approve", "Approve", show=False),
        Binding("d", "deny", "Deny", show=False),
        Binding("s", "cycle_scope", "Scope", show=False),
        Binding("escape", "leave", "Back to input", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmationsPanel {
        width: 38;
        /* The transcript is still the main thing on screen: on a narrow
           terminal the panel gives way rather than squeezing chat into a
           column too thin to read. */
        max-width: 34%;
        border-left: solid $primary;
        padding: 0 1;
    }

    ConfirmationsPanel #confirmations-title {
        color: $accent;
        text-style: bold;
    }

    ConfirmationsPanel #confirmations-scope {
        color: $text-muted;
        padding: 0 0 1 0;
    }

    ConfirmationsPanel #confirmations-list {
        height: 1fr;
    }

    ConfirmationsPanel #confirmations-hint {
        height: auto;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.scope: str = SCOPE_ALL
        # Which confirmations are expanded, and which of their tasks are ticked.
        self._expanded: set[str] = set()
        self._checked: Dict[str, set[int]] = {}
        # Signature of the last render, so the refresh timer only rebuilds the
        # ListView when the data actually moved — a blind rebuild every tick
        # would fight the user's highlight and collapse what they just opened.
        self._signature: Optional[Tuple] = None
        # Direction of travel, so skipping a heading keeps going the way the
        # user was already moving instead of bouncing back.
        self._last_index: Optional[int] = None

    def compose(self) -> ComposeResult:
        yield Label("Confirmations  (F3)", id="confirmations-title")
        yield Label("", id="confirmations-scope")
        yield ListView(id="confirmations-list")
        yield Label("", id="confirmations-hint")

    def on_mount(self) -> None:
        self.refresh_entries()
        self.set_interval(1.0, self.refresh_entries)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _read(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """(pending, history) from the engine if there is one, else the store."""
        return self.app.read_confirmations()

    def _current_session_id(self) -> Optional[str]:
        return self.app.current_session_id()

    def refresh_entries(self) -> None:
        """Rebuild the rows, but only when something actually changed."""
        pending, history = self._read()
        session_id = self._current_session_id()
        pending = filter_by_scope(pending, self.scope, session_id)
        history = filter_by_scope(history, self.scope, session_id)

        signature = (
            self.scope,
            session_id,
            tuple(e["id"] for e in pending),
            tuple((r["id"], r.get("resolved_at")) for r in history),
            tuple(sorted(self._expanded)),
            tuple((k, tuple(sorted(v))) for k, v in sorted(self._checked.items())),
        )
        if signature == self._signature:
            return
        self._signature = signature
        self._rebuild(pending, history)

    def _rebuild(
        self, pending: List[Dict[str, Any]], history: List[Dict[str, Any]]
    ) -> None:
        # Not ``_render``: that is Widget's own paint hook, and shadowing it
        # breaks the panel's rendering outright.
        try:
            list_view = self.query_one("#confirmations-list", ListView)
            scope_label = self.query_one("#confirmations-scope", Label)
            hint = self.query_one("#confirmations-hint", Label)
        except Exception:
            return

        active = "This session" if self.scope == SCOPE_SESSION else "All"
        other = "All" if self.scope == SCOPE_SESSION else "This session"
        scope_label.update(f"[reverse] {active} [/reverse] [dim]s → {other}[/dim]")

        previous_index = list_view.index
        list_view.clear()

        list_view.append(_SectionRow(f"[bold]── Pending ({len(pending)}) ──[/bold]"))
        if not pending:
            list_view.append(_SectionRow("[dim]  nothing waiting[/dim]"))
        for entry in pending:
            expanded = entry["id"] in self._expanded
            list_view.append(
                _PendingRow(entry, expanded, _pending_markup(entry, expanded))
            )
            if expanded:
                checked = self._checked.setdefault(
                    entry["id"], set(range(len(entry.get("tool_lines") or [])))
                )
                for i, line in enumerate(entry.get("tool_lines") or []):
                    mark = "x" if i in checked else " "
                    list_view.append(
                        _TaskRow(
                            entry["id"],
                            i,
                            f"   \\[{mark}] [dim]{i}[/dim] {_escape(line)}",
                        )
                    )

        list_view.append(_SectionRow("[bold]── History ──[/bold]"))
        if not history:
            list_view.append(_SectionRow("[dim]  nothing resolved yet[/dim]"))
        for record in history[:20]:
            list_view.append(_HistoryRow(record, _history_markup(record)))

        # Land on something actionable: opening the panel should put the cursor
        # on a confirmation, not on the "── Pending ──" heading.
        target = previous_index if previous_index is not None else 0
        list_view.index = self._nearest_selectable(list_view, target, forward=True)

        hint.update(
            "[dim]enter·open space·task a·ok d·no[/dim]"
            if pending
            else "[dim]esc back to chat[/dim]"
        )

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _nearest_selectable(
        list_view: ListView, start: int, forward: bool
    ) -> Optional[int]:
        """First actionable row at or after (or before) *start*.

        Section headings share the list with real rows, so both the initial
        cursor placement and arrow navigation have to step over them; a cursor
        resting on "── History ──" has nothing to approve.
        """
        rows = list(list_view.children)
        if not rows:
            return None
        start = max(0, min(start, len(rows) - 1))
        order = range(start, len(rows)) if forward else range(start, -1, -1)
        for i in order:
            if getattr(rows[i], "selectable_row", False):
                return i
        # Nothing that way — try the other direction before giving up.
        order = range(start, -1, -1) if forward else range(start, len(rows))
        for i in order:
            if getattr(rows[i], "selectable_row", False):
                return i
        return start

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter (or a click) on a row expands/collapses its confirmation."""
        event.stop()
        self.action_toggle_expand()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Keep the cursor off headings as the user arrows through."""
        list_view = event.list_view
        index = list_view.index
        if index is None:
            self._last_index = None
            return
        rows = list(list_view.children)
        if index < len(rows) and getattr(rows[index], "selectable_row", False):
            self._last_index = index
            return
        # Carry on in whichever direction the user was already travelling.
        forward = self._last_index is None or index >= self._last_index
        nearest = self._nearest_selectable(list_view, index, forward)
        if nearest is not None and nearest != index:
            list_view.index = nearest

    def _highlighted(self) -> Optional[_Row]:
        try:
            child = self.query_one("#confirmations-list", ListView).highlighted_child
        except Exception:
            return None
        return child if isinstance(child, _Row) else None

    def _target_confirmation(self) -> Optional[str]:
        """The confirmation the highlighted row belongs to, task rows included."""
        row = self._highlighted()
        if isinstance(row, (_PendingRow, _TaskRow)):
            return row.confirmation_id
        return None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_toggle_expand(self) -> None:
        row = self._highlighted()
        if isinstance(row, _TaskRow):
            # Enter on a task row collapses its parent — the way back out.
            self._expanded.discard(row.confirmation_id)
        elif isinstance(row, _PendingRow):
            if row.confirmation_id in self._expanded:
                self._expanded.discard(row.confirmation_id)
            else:
                self._expanded.add(row.confirmation_id)
        else:
            return
        self.refresh_entries()

    def action_toggle_task(self) -> None:
        row = self._highlighted()
        if not isinstance(row, _TaskRow):
            return
        checked = self._checked.setdefault(row.confirmation_id, set())
        if row.index in checked:
            checked.discard(row.index)
        else:
            checked.add(row.index)
        self.refresh_entries()

    def action_approve(self) -> None:
        confirmation_id = self._target_confirmation()
        if confirmation_id is None:
            return
        entry = self._pending_entry(confirmation_id)
        total = len(entry.get("tool_lines") or []) if entry else 0
        checked = self._checked.get(confirmation_id)

        # A partial message only when the user actually deselected something;
        # otherwise send a plain approve, which is what every other client
        # sends and what the resume path treats as the simple case.
        if (
            confirmation_id in self._expanded
            and checked is not None
            and len(checked) < total
        ):
            message = {
                "type": "partial_approve_confirmation",
                "id": confirmation_id,
                "approved_indices": sorted(checked),
            }
        else:
            message = {"type": "approve_confirmation", "id": confirmation_id}
        self._submit(message, confirmation_id)

    def action_deny(self) -> None:
        confirmation_id = self._target_confirmation()
        if confirmation_id is None:
            return
        self._submit(
            {"type": "deny_confirmation", "id": confirmation_id}, confirmation_id
        )

    def action_cycle_scope(self) -> None:
        self.scope = SCOPE_ALL if self.scope == SCOPE_SESSION else SCOPE_SESSION
        self.refresh_entries()

    def action_leave(self) -> None:
        row = self._highlighted()
        if (
            isinstance(row, (_PendingRow, _TaskRow))
            and row.confirmation_id in self._expanded
        ):
            self._expanded.discard(row.confirmation_id)
            self.refresh_entries()
            return
        self.app.action_focus_input()

    def _pending_entry(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        pending, _ = self._read()
        for entry in pending:
            if entry["id"] == confirmation_id:
                return entry
        return None

    def _submit(self, message: Dict[str, Any], confirmation_id: str) -> None:
        self.app.submit_confirmation_decision(message)
        self._expanded.discard(confirmation_id)
        self._checked.pop(confirmation_id, None)
        self.refresh_entries()


# ----------------------------------------------------------------------
# Data source — the engine when there is one, the store file when there isn't
# ----------------------------------------------------------------------


def read_confirmations(app: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(pending, history) for the panel, from whichever source is available.

    With an engine the manager is in this very process, so this is a plain
    method call — no socket round-trip. Without one, the same records come off
    the same files ``jarvis confirm`` reads, which is the whole reason #224
    put them on disk.
    """
    from ..core.confirmation_manager import (
        history_path,
        load_history,
        load_pending_summaries,
        store_path,
    )

    engine = getattr(app, "jarvis", None)
    manager = getattr(engine, "confirmation", None) if engine is not None else None
    if manager is not None:
        try:
            return manager.list_pending(), manager.list_history()
        except Exception:
            return [], []
    return load_pending_summaries(store_path()), load_history(history_path())


def submit_decision(app: Any, message: Dict[str, Any]) -> str:
    """Apply one decision message, or queue it when there is no engine.

    Returns a line for the transcript. The message is the exact socket
    protocol shape in both branches, so the offline queue receives something
    the daemon's startup replay already knows how to apply.
    """
    from ..core.confirmation_manager import decision_queue_path, queue_decision

    engine = getattr(app, "jarvis", None)
    if engine is not None and getattr(engine, "confirmation", None) is not None:
        from ..runtime.io import apply_confirmation_decision

        ack = apply_confirmation_decision(engine, message)
        return ack or "Nothing to do."

    try:
        replaced = queue_decision(message, decision_queue_path())
    except Exception as e:
        return f"Could not queue decision: {e}"
    verb = "Replaced queued decision for" if replaced else "Queued"
    return f"{verb} {message.get('id', '')} — applies when the engine next starts."


def _escape(text: str) -> str:
    from . import output as tui_output

    return tui_output.escape(text)


def _pending_markup(entry: Dict[str, Any], expanded: bool) -> str:
    caret = "▾" if expanded else "▸"
    lines = entry.get("tool_lines") or []
    count = f"{len(lines)} tool" + ("s" if len(lines) != 1 else "")
    age = format_age(entry.get("created_at") or 0)
    head = f"{caret} [bold]{entry['id']}[/bold]  {count}  [dim]{age}[/dim]"
    if expanded:
        return head
    # Collapsed rows still show the first command — the tool name alone is what
    # #186 already rejected as too little to decide on.
    first = _escape(lines[0]) if lines else "[dim](no detail)[/dim]"
    return f"{head}\n   [dim]{first}[/dim]"


def _history_markup(record: Dict[str, Any]) -> str:
    outcome = record.get("outcome", "")
    mark = _OUTCOME_MARK.get(outcome, _PARTIAL_MARK)
    age = format_age(record.get("resolved_at") or 0)
    names = ", ".join(record.get("tool_names") or []) or "(no tools)"
    return (
        f"  {mark} [bold]{record['id']}[/bold] {_escape(names)[:28]} [dim]{age}[/dim]"
    )
