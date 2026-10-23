"""What JARVIS is doing now, as plain data (#267).

The TUI's tasks view (F4, `/tasks`) shows it, and socket clients (jarvis-app,
the phone) can ask for the same dicts. Running tasks come from dispatch, which
knows every PID; the action blocks add what dispatch does not report, when a
task started and its parameters as JARVIS sent them.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from . import action_events

# Enough of a running task's output to see where it is.
TAIL_CHARS = 2_000


def _goal_of_pid(app: Any) -> Dict[Any, str]:
    goals = getattr(app, "goals", None)
    owner: Dict[Any, str] = {}
    for goal in goals.get_active_goals() if goals is not None else []:
        for pid in goal.task_pids:
            owner[pid] = goal.description
    return owner


async def collect_work(app: Any, *, tail: int = TAIL_CHARS) -> Dict[str, Any]:
    """Requests, running tasks and timers, and what waits on the user."""
    now = time.time()
    goals = getattr(app, "goals", None)
    requests = [
        {"id": g.id, "text": g.description, "since": g.created_at}
        for g in (goals.get_active_goals() if goals is not None else [])
    ]
    started = {a["id"]: a for a in action_events.running(app)}
    owner = _goal_of_pid(app)
    tasks: List[Dict[str, Any]] = []
    dispatch = getattr(app, "dispatch", None)
    if getattr(dispatch, "is_connected", False):
        try:
            listed = await dispatch.get_task_status(tail=tail)
        except Exception:
            listed = []
        for t in listed:
            if t.get("state") != "running":
                continue
            pid = t.get("pid")
            if t.get("type") == "timer":
                tasks.append(
                    {
                        "pid": pid,
                        "kind": "timer",
                        "title": str(t.get("label") or "timer"),
                        "detail": "",
                        "fires_in": t.get("fires_in"),
                        "request": owner.get(pid, ""),
                    }
                )
                continue
            record = started.get(f"tool:{pid}", {})
            output = ""
            if t.get("tail"):
                output = action_events.signal_output({"data": t["tail"]})[1]
            tasks.append(
                {
                    "pid": pid,
                    "kind": "tool",
                    "title": record.get("title") or action_events.tool_title(t),
                    "detail": record.get("detail", ""),
                    "running_for": (
                        round(now - record["at"], 1) if record.get("at") else None
                    ),
                    "output": output,
                    "request": owner.get(pid, ""),
                }
            )
    confirmation = getattr(app, "confirmation", None)
    setups = getattr(app, "setups", None)
    thinking = sum(1 for t in getattr(app, "_event_tasks", ()) if not t.done())
    return {
        "requests": requests,
        "tasks": tasks,
        "thinking": thinking,
        "confirmations": len(confirmation.list_pending()) if confirmation else 0,
        "setups": len(setups.list()) if setups is not None else 0,
    }
