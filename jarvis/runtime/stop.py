"""Stopping JARVIS's work in the current session (#270, decided 2026-10-24).

A stop (`/stop`, Esc on an empty input while JARVIS works, the phone's stop
button, the socket's `stop`) acts at once on the requests of the current
session: the model's turn in progress is cancelled and its answer dropped,
confirmations waiting are denied, reminders and timers are cancelled, and the
requests are closed. The tasks they started keep running, since a system
update killed mid-install can leave the machine unbootable; from then on
those tasks reach only the screen (their blocks, F4) and, when they end, one
line in the chat and the session. The model hears nothing from them,
reminders included.

`/stop all` (socket `stop_all`) also kills those tasks: what quitting does
already, without the exit and for this session only. When one of them runs a
package manager it asks once more first.

One task can be stopped too: `/stop <number>` (the number F4 shows), x in F4,
the phone's Stop on a task (socket `stop_task`). The task is killed, asking
once more first for a package manager, and the model hears nothing from it.
Its request is closed when nothing else of it is under way; otherwise its next
turn is told the user stopped the task, so the model does not run it again.
"""

from __future__ import annotations

import asyncio
import re
import time
from logging import Logger
from typing import Any, Dict, List, Optional

from . import action_events
from .output_hooks import persist_assistant_turn

# Killing one of these mid-transaction can leave the system unbootable.
_PACKAGE_MANAGER = re.compile(
    r"(?:^|[\s;&|(/])(?:pacman|yay|paru|pamac|apt|apt-get|aptitude|dpkg|dnf|yum|"
    r"rpm|zypper|emerge|xbps-install|apk|flatpak|snap|nix-env|mkinitcpio|dkms)\b"
)
# How long a /stop all warning waits for the second /stop all.
STOP_ALL_CONFIRM_SECONDS = 15.0


def is_package_manager(text: str) -> bool:
    return bool(_PACKAGE_MANAGER.search(text or ""))


# --- which turn works for which request --------------------------------------


def claim_turn(app: Any, goal_id: Optional[str]) -> None:
    """Note that the running event task works for `goal_id`, so a stop can cancel it."""
    task = asyncio.current_task()
    if task is None or not goal_id:
        return
    turns = getattr(app, "_turn_goals", None)
    if turns is None:
        turns = app._turn_goals = {}
    if task not in turns:
        task.add_done_callback(lambda t: turns.pop(t, None))
    turns[task] = goal_id


# --- tasks of stopped requests -----------------------------------------------


def _detached(app: Any) -> Dict[Any, str]:
    """PID -> title of tasks left running by a stop."""
    detached = getattr(app, "_detached_tasks", None)
    if detached is None:
        detached = app._detached_tasks = {}
    return detached


def _killed(app: Any) -> set:
    """PIDs of tasks the user stopped one by one, until their end arrives."""
    killed = getattr(app, "_killed_tasks", None)
    if killed is None:
        killed = app._killed_tasks = set()
    return killed


def detached(app: Any, signal: Dict[str, Any], exit_data: Any = None) -> bool:
    """True when the signal belongs to a task a stop left running, or killed.

    Such a signal never reaches the model. The end of a task left running is
    told to the user in one line and saved to the session, so a later
    question can be answered; a killed task was told when it was stopped.
    """
    pid = signal.get("pid")
    ended = exit_data if isinstance(exit_data, dict) else signal
    is_end = str(ended.get("type") or "").upper() in ("EXIT", "KILL", "TIMEOUT")
    if pid in _killed(app):
        if is_end:
            _killed(app).discard(pid)
        return True
    tasks = _detached(app)
    if pid not in tasks:
        return False
    if is_end:
        title = tasks.pop(pid)
        status, _ = action_events.outcome(ended)
        word = {"ok": "finished", "stopped": "was stopped"}.get(status, "failed")
        text = f"{title} {word} (it kept running after the stop)."
        app.output_manager.handle_response({"output": text})
        persist_assistant_turn(app, text)
    return True


def _title(app: Any, pid: Any) -> str:
    """How a task reads to the user: its block's title and command."""
    record = next(
        (a for a in action_events.running(app) if a["id"] == f"tool:{pid}"), None
    )
    if record is None:
        server_tool = (getattr(app, "_pid_tools", None) or {}).get(pid)
        return f"{server_tool[1]} ({server_tool[0]})" if server_tool else f"task {pid}"
    detail = " ".join(record.get("detail", "").split())
    if len(detail) > 60:
        detail = detail[:59] + "…"
    return f"{record['title']}" + (f": {detail}" if detail else "")


# --- the stop ----------------------------------------------------------------


def _session_goals(app: Any) -> list:
    sessions = getattr(app, "sessions", None)
    current = getattr(sessions, "current_id", None)
    goals = getattr(app, "goals", None)
    active = goals.get_active_goals() if goals is not None else []
    # A goal from before goals recorded their session counts as this one's.
    return [g for g in active if getattr(g, "chat_session", None) in (current, None)]


async def _running_pids(app: Any, goals: list) -> List[Any]:
    """PIDs of the goals' tasks that dispatch still runs."""
    pids = [pid for g in goals for pid in g.task_pids]
    finished = getattr(app, "_signal_ledger", (set(), {}))[0]
    dispatch = getattr(app, "dispatch", None)
    if getattr(dispatch, "is_connected", False):
        try:
            running = {
                t.get("pid")
                for t in await dispatch.get_task_status(tail=0)
                if t.get("state") == "running"
            }
            return [p for p in pids if p in running]
        except Exception:
            pass
    return [p for p in pids if p not in finished]


async def stop_session(app: Any, logger: Logger, *, kill: bool = False) -> str:
    """Stop the current session's requests; returns what to tell the user."""
    goals = _session_goals(app)
    goal_ids = {g.id for g in goals}
    pids = await _running_pids(app, goals)
    titles = {pid: _title(app, pid) for pid in pids}

    current = asyncio.current_task()
    turns = [
        t
        for t, goal_id in list((getattr(app, "_turn_goals", None) or {}).items())
        if goal_id in goal_ids and t is not current and not t.done()
    ]
    for turn in turns:
        turn.cancel()

    denied = 0
    confirmation = getattr(app, "confirmation", None)
    for item in confirmation.list_pending() if confirmation is not None else []:
        if item.get("session_id") in goal_ids:
            confirmation.resolve({"id": item["id"], "approved": False})
            action_events.end(app, f"confirm:{item['id']}", "denied", "Stopped.")
            denied += 1
    if denied and getattr(app, "_gui_clients", None):
        from .io import broadcast_to_gui_clients, enrich_pending_with_goals

        await broadcast_to_gui_clients(
            app,
            {
                "type": "confirmation_list",
                "confirmations": enrich_pending_with_goals(app),
            },
        )

    timers = [g.timer_pid for g in goals if g.timer_pid]
    dispatch = getattr(app, "dispatch", None)
    to_kill = timers + (pids if kill else [])
    if to_kill and getattr(dispatch, "is_connected", False):
        await dispatch.kill_tasks(to_kill)
    for pid in pids:
        _detached(app)[pid] = titles[pid]

    for goal in goals:
        app.goals.fail_goal(goal.id, "Stopped by the user")
    if goals:
        app.goals.dismiss_failed()

    logger.info(
        f"JARVIS: stopped {len(goals)} request(s): {len(turns)} turn(s) cancelled, "
        f"{denied} confirmation(s) denied, {len(pids)} task(s) "
        + ("killed" if kill else "left running")
    )
    if not goals and not turns:
        return "Nothing to stop."
    if not pids:
        return "Stopped."
    listing = "; ".join(titles[p] for p in pids)
    if kill:
        return f"Stopped, and ended: {listing}."
    return (
        f"Stopped. Still running in the background: {listing}. F4 shows "
        f"{'it' if len(pids) == 1 else 'them'}; /stop all ends "
        f"{'it' if len(pids) == 1 else 'them'}."
    )


async def handle_command(app: Any, logger: Logger, text: str) -> Optional[str]:
    """`/stop` or `/stop all`: the reply, or None when `text` is something else."""
    words = text.strip().lower().split()
    if not words or words[0] != "/stop":
        return None
    if len(words) == 2 and words[1].isdigit():
        return await stop_task(app, logger, int(words[1]))
    if words[1:] not in ([], ["all"]):
        return (
            "Usage: /stop, /stop all to end the running tasks too, or /stop "
            "<number> to end one task (F4 shows the numbers)."
        )
    if words[1:] == []:
        return await stop_session(app, logger)

    goals = _session_goals(app)
    pids = await _running_pids(app, goals)
    risky = [p for p in pids if is_package_manager(_title(app, p))]
    armed = getattr(app, "_stop_all_armed_at", None)
    now = time.monotonic()
    if risky and not (armed and now - armed < STOP_ALL_CONFIRM_SECONDS):
        app._stop_all_armed_at = now
        names = "; ".join(_title(app, p) for p in risky)
        return (
            f"{names} is installing or changing packages; ending it now can leave "
            f"the system broken. Send /stop all again within "
            f"{STOP_ALL_CONFIRM_SECONDS:.0f} s to end it anyway, or /stop to leave "
            "it running."
        )
    app._stop_all_armed_at = None
    return await stop_session(app, logger, kill=True)


# --- one task ----------------------------------------------------------------


def _in_flight(app: Any, goal: Any, running: List[Any], pending: List[Any]) -> bool:
    """True when the request has something under way besides its tasks."""
    from ..dispatch.goal_manager import GoalStatus
    from . import turns

    if any(p in running for p in goal.task_pids):
        return True
    if goal.timer_pid or turns.active(app, goal.id):
        return True
    under_way = (GoalStatus.PENDING, GoalStatus.ACTIVE, GoalStatus.DEFERRED)
    for cid in goal.child_goal_ids:
        child = app.goals.get_goal(cid)
        if child is not None and child.status in under_way:
            return True
    return any(item.get("session_id") == goal.id for item in pending)


def stopped_note(app: Any, goal_id: Optional[str]) -> str:
    """For a request's next turn: the tasks of it the user stopped since the last."""
    notes = getattr(app, "_stopped_notes", None) or {}
    titles = notes.pop(goal_id, None) if goal_id else None
    if not titles:
        return ""
    return (
        f"\nSTOPPED_BY_USER: the user stopped {'; '.join(titles)}. Do not run "
        f"{'it' if len(titles) == 1 else 'them'} again unless the user asks."
    )


async def stop_task(app: Any, logger: Logger, pid: int) -> str:
    """Kill one running task; returns what to tell the user."""
    dispatch = getattr(app, "dispatch", None)
    if not getattr(dispatch, "is_connected", False):
        return "Cannot reach the task runner (dispatch), so nothing was stopped."
    tasks = {
        t.get("pid"): t
        for t in await dispatch.get_task_status(tail=0)
        if t.get("state") == "running"
    }
    task = tasks.get(pid)
    if task is None:
        return f"No task {pid} is running (F4 shows what is)."
    if task.get("type") == "timer":
        return f"Task {pid} is a timer, not a task; /stop ends its request."
    title = _title(app, pid)

    armed = getattr(app, "_stop_task_armed", None)
    now = time.monotonic()
    if is_package_manager(title) and not (
        armed and armed[0] == pid and now - armed[1] < STOP_ALL_CONFIRM_SECONDS
    ):
        app._stop_task_armed = (pid, now)
        return (
            f"{title} is installing or changing packages; ending it now can leave "
            f"the system broken. Stop it again within "
            f"{STOP_ALL_CONFIRM_SECONDS:.0f} s to end it anyway."
        )
    app._stop_task_armed = None

    _killed(app).add(pid)
    _detached(app).pop(pid, None)
    await dispatch.kill_tasks([pid])

    goals = getattr(app, "goals", None)
    goal = goals.find_goal_by_task_pid(pid) if goals is not None else None
    closed = False
    if goal is not None:
        confirmation = getattr(app, "confirmation", None)
        pending = confirmation.list_pending() if confirmation is not None else []
        others = [p for p in tasks if p != pid]
        if _in_flight(app, goal, others, pending):
            notes = getattr(app, "_stopped_notes", None)
            if notes is None:
                notes = app._stopped_notes = {}
            notes.setdefault(goal.id, []).append(title)
        else:
            goals.fail_goal(goal.id, "Stopped by the user")
            goals.dismiss_failed()
            closed = True
    logger.info(
        f"JARVIS: stopped task {pid} ({title})"
        + (f"; closed goal [{goal.id}]" if closed else "")
    )
    text = f"Stopped {title}."
    persist_assistant_turn(app, text)
    return text
