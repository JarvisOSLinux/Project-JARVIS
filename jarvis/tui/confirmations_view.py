"""The confirmations panel's rows and decisions, as plain data (#235).

No Textual here: what the panel shows and what a keypress sends are decided in
these functions, so they are tested directly and the widget is only a shell.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

SCOPE_ALL = "all"
SCOPE_SESSION = "session"
SCOPES = (SCOPE_ALL, SCOPE_SESSION)


@dataclass(frozen=True)
class Row:
    """One line of the panel. ``kind`` is pending (a request), task (one tool
    call inside it), history (a resolved request), or note (not selectable)."""

    kind: str
    text: str
    request_id: Optional[str] = None
    task_index: Optional[int] = None


def format_age(created_at: float, now: Optional[float] = None) -> str:
    delta = max(0, (time.time() if now is None else now) - created_at)
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def in_scope(
    entry: Dict[str, Any], scope: str, current_session_id: Optional[str]
) -> bool:
    """``This session`` keeps entries requested while the active chat session
    was current. An entry with no session recorded (older store) shows under
    ``All`` only, rather than being guessed into a session."""
    if scope == SCOPE_ALL:
        return True
    return (
        bool(current_session_id) and entry.get("chat_session_id") == current_session_id
    )


def selection_for(entry: Dict[str, Any], chosen: Optional[Set[int]]) -> Set[int]:
    """Indices of the tasks that will be approved: every task unless the user
    unchecked some."""
    count = len(entry.get("tool_lines") or [])
    return (
        set(range(count)) if chosen is None else {i for i in chosen if 0 <= i < count}
    )


def decision_for(entry: Dict[str, Any], chosen: Optional[Set[int]]) -> Dict[str, Any]:
    """The socket-protocol message that applies the current selection."""
    request_id = entry["id"]
    selected = selection_for(entry, chosen)
    total = len(entry.get("tool_lines") or [])
    if total == 0 or len(selected) == total:
        return {"type": "approve_confirmation", "id": request_id}
    if not selected:
        return {"type": "deny_confirmation", "id": request_id}
    return {
        "type": "partial_approve_confirmation",
        "id": request_id,
        "approved_indices": sorted(selected),
    }


def deny_message(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "deny_confirmation", "id": entry["id"]}


def _queued_label(decision: Dict[str, Any]) -> str:
    kind = decision.get("type")
    if kind == "approve_confirmation":
        return "queued: approve"
    if kind == "deny_confirmation":
        return "queued: deny"
    if kind == "partial_approve_confirmation":
        return f"queued: approve {len(decision.get('approved_indices') or [])}"
    return "queued"


def build_rows(
    pending: Iterable[Dict[str, Any]],
    history: Iterable[Dict[str, Any]],
    *,
    scope: str,
    current_session_id: Optional[str],
    selections: Dict[str, Set[int]],
    queued: Iterable[Dict[str, Any]] = (),
    now: Optional[float] = None,
) -> List[Row]:
    queued_by_id = {d.get("id"): d for d in queued if d.get("id")}
    rows: List[Row] = []

    shown = [p for p in pending if in_scope(p, scope, current_session_id)]
    rows.append(Row("note", f"PENDING ({len(shown)})"))
    if not shown:
        rows.append(Row("note", "  nothing waiting for you"))
    for entry in shown:
        request_id = entry["id"]
        header = f"{request_id}  {format_age(entry.get('created_at', 0), now)}"
        goal = entry.get("goal_description")
        if goal:
            header += f"  {goal}"
        if request_id in queued_by_id:
            header += f"  [{_queued_label(queued_by_id[request_id])}]"
        rows.append(Row("pending", header, request_id))
        selected = selection_for(entry, selections.get(request_id))
        for i, line in enumerate(entry.get("tool_lines") or []):
            mark = "x" if i in selected else " "
            rows.append(Row("task", f"  [{mark}] {line}", request_id, i))

    resolved = [h for h in history if in_scope(h, scope, current_session_id)]
    rows.append(Row("note", f"HISTORY ({len(resolved)})"))
    for entry in resolved:
        outcome = entry.get("outcome", "?")
        if outcome == "partial":
            outcome = f"partial {entry.get('approved', 0)}/{entry.get('total', 0)}"
        first = (entry.get("tool_lines") or ["?"])[0]
        more = len(entry.get("tool_lines") or []) - 1
        suffix = f" (+{more})" if more > 0 else ""
        rows.append(
            Row(
                "history",
                f"  {outcome:<10} {format_age(entry.get('resolved_at', 0), now)}  {first}{suffix}",
                entry.get("id"),
            )
        )
    return rows
