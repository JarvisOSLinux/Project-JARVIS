"""#190 — tasks dispatched through a confirmation must stay linked to their goal.

The direct dispatch path passes session_id=goal.id and link_tasks() the returned
PIDs; the confirmation-resume path used to do neither, so a confirmed task was
detached from its goal ("No goal found for PID"). These tests pin the fix.
"""

import asyncio
import logging

from jarvis.core.confirmation_manager import ConfirmationManager, PendingConfirmation
from jarvis.dispatch.goal_manager import GoalManager
from jarvis.runtime import root_handlers

_LOG = logging.getLogger("test")


def _task():
    return {
        "server": "sys",
        "tool": "execute_command",
        "params": {"command": "pacman", "args": ["-Syu", "--noconfirm"]},
    }


def test_request_confirmation_carries_session_id():
    mgr = ConfirmationManager()
    detail = {"tool_name": "sys.execute_command", "task": _task(), "params": {}}

    async def run():
        await mgr.request_confirmation(
            request_id="r1",
            tasks=[_task()],
            tools_needing_confirmation=[detail],
            approved_tasks=[],
            denied_tools=[],
            timeout=0,
            session_id="goal-abc",
        )

    asyncio.run(run())
    pending = mgr.resolve({"id": "r1", "approved": True})
    assert pending is not None
    assert pending.session_id == "goal-abc"


class _FakeDispatch:
    def __init__(self):
        self.session_id = "UNSET"

    async def send_tasks(self, tasks, session_id=None):
        self.session_id = session_id
        # Shape dispatch actually returns: the INIT signal window as text.
        return {
            "output": (
                "Signal window (last 1):\n"
                '[10:00:00] PID 7 INIT sys/execute_command {"command":"pacman"}\n'
            )
        }


class _FakeApp:
    def __init__(self, goals, confirmation):
        self.goals = goals
        self.confirmation = confirmation
        self.dispatch = _FakeDispatch()
        self.llm = object()  # not None
        self._gui_clients = None
        self.acted = None

    async def _act_on_root_response(self, response):
        self.acted = response


def test_confirmation_resume_links_pids_to_goal(tmp_path, monkeypatch):
    goals = GoalManager(archive_dir=str(tmp_path))
    goal = goals.add_goal("check python and update system")

    conf = ConfirmationManager()
    conf._pending["r1"] = PendingConfirmation(
        request_id="r1",
        tasks=[_task()],
        approved_tasks=[],
        session_id=goal.id,
    )

    app = _FakeApp(goals, conf)

    # Keep the test to the linkage: stub context building and the LLM call.
    monkeypatch.setattr(root_handlers, "build_root_context", lambda a, l, **k: "")

    async def _fake_ask(a, l, c, **k):
        return {"action": "respond", "output": "ok"}

    monkeypatch.setattr(root_handlers, "ask_llm", _fake_ask)

    asyncio.run(
        root_handlers.on_confirmation_response(
            app, _LOG, {"id": "r1", "approved": True}
        )
    )

    # The dispatch was scoped to the goal, and the returned PID is now owned by it.
    assert app.dispatch.session_id == goal.id
    assert goals.find_goal_by_task_pid(7) is goal


def test_confirmation_resume_without_goal_does_not_crash(tmp_path, monkeypatch):
    # A confirmation with no owning goal (session_id=None) must still resume.
    goals = GoalManager(archive_dir=str(tmp_path))
    conf = ConfirmationManager()
    conf._pending["r2"] = PendingConfirmation(
        request_id="r2",
        tasks=[_task()],
        approved_tasks=[],
        session_id=None,
    )
    app = _FakeApp(goals, conf)
    monkeypatch.setattr(root_handlers, "build_root_context", lambda a, l, **k: "")

    async def _fake_ask(a, l, c, **k):
        return {"action": "respond", "output": "ok"}

    monkeypatch.setattr(root_handlers, "ask_llm", _fake_ask)

    asyncio.run(
        root_handlers.on_confirmation_response(
            app, _LOG, {"id": "r2", "approved": True}
        )
    )
    assert app.dispatch.session_id is None
    assert app.acted == {"action": "respond", "output": "ok"}
