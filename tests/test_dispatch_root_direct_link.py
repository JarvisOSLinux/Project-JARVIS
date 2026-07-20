"""#195 / #196 — ROOT-direct dispatch scopes the goal, links PIDs, then goes silent.

The direct path (root -> dispatch_execute_tasks -> dispatch_send) must:
  - #196: pass session_id=<active goal id> so the dispatched PIDs link back to the
    goal and every signal turn is goal-scoped (no "No goal found for PID").
  - #195: NOT ask the LLM right after send on the happy path — the tasks' EXIT
    signals drive the next ROOT turn once real results exist. A synchronous send
    error still gets surfaced, because no signal will ever come for it.
"""

import asyncio
import logging

from jarvis.dispatch.goal_manager import GoalManager
from jarvis.runtime import dispatch_flow

_LOG = logging.getLogger("test")


class _FakeDispatch:
    is_connected = True


class _App:
    def __init__(self, goals):
        self.goals = goals
        self.dispatch = _FakeDispatch()
        self.llm = _LLM()
        self.acted = None

    async def _act_on_root_response(self, response, depth=0):
        self.acted = (response, depth)


class _LLM:
    def switch_mode(self, mode):
        pass


def _task():
    return {
        "server": "sys",
        "tool": "execute_command",
        "params": {"command": "python3", "args": ["--version"]},
    }


def test_direct_dispatch_scopes_goal_and_stays_silent(tmp_path, monkeypatch):
    goals = GoalManager(archive_dir=str(tmp_path))
    goal = goals.add_goal("check python and update system")

    seen = {}

    async def _fake_send(app, logger, tasks, session_id=None):
        seen["session_id"] = session_id
        return {
            "output": (
                "Signal window (last 1):\n"
                '[10:00:00] PID 7 INIT sys/execute_command {"command":"python3"}\n'
            )
        }

    monkeypatch.setattr(dispatch_flow, "dispatch_send", _fake_send)
    monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)

    app = _App(goals)
    asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, [_task()], depth=0))

    # #196: dispatch scoped to the active goal; the returned PID is owned by it.
    assert seen["session_id"] == goal.id
    assert goals.find_goal_by_task_pid(7) is goal
    # #195: no immediate LLM turn — the EXIT signal will drive it.
    assert app.acted is None


def test_direct_dispatch_error_still_drives_a_turn(tmp_path, monkeypatch):
    goals = GoalManager(archive_dir=str(tmp_path))
    goals.add_goal("do a thing")

    async def _fake_send(app, logger, tasks, session_id=None):
        return {"error": "Dispatch not connected"}

    async def _fake_ask(app, logger, context, **k):
        return {"action": "respond", "output": "surfaced"}

    monkeypatch.setattr(dispatch_flow, "dispatch_send", _fake_send)
    monkeypatch.setattr(dispatch_flow, "build_root_context", lambda a, l, **k: "")
    monkeypatch.setattr(dispatch_flow, "ask_llm", _fake_ask)

    app = _App(goals)
    asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, [_task()], depth=0))

    # A synchronous send error has no signal coming, so it must still be surfaced.
    assert app.acted is not None
    assert app.acted[0] == {"action": "respond", "output": "surfaced"}
