"""#189 — a merged fire_wake=false batch is handled as ONE ROOT turn.

on_dispatch_signals must fold the whole group into a single goal-scoped LLM
turn (not one turn per signal), so ROOT sees every outcome at once.
"""

import asyncio
import logging

from jarvis.dispatch.goal_manager import GoalManager
from jarvis.runtime import root_handlers

_LOG = logging.getLogger("test")


class _Sessions:
    def load_summary(self):
        return ""


class _App:
    def __init__(self, goals):
        self.goals = goals
        self.llm = object()  # not None
        self.sessions = _Sessions()
        self.acted = None

    async def _act_on_root_response(self, response):
        self.acted = response


def _signals():
    return [
        {"type": "EXIT", "pid": 1, "data": "200 Python 3.14.6", "timestamp": "t1"},
        {"type": "EXIT", "pid": 2, "data": "500 not root", "timestamp": "t2"},
    ]


def test_batch_is_one_goal_scoped_turn(tmp_path, monkeypatch):
    goals = GoalManager(archive_dir=str(tmp_path))
    goal = goals.add_goal("check python and update system")
    goals.link_tasks(goal.id, [1, 2])

    contexts = []

    async def _fake_ask(app, logger, context, **k):
        contexts.append(context)
        return {"action": "respond", "output": "ok"}

    monkeypatch.setattr(root_handlers, "ask_llm", _fake_ask)
    monkeypatch.setattr(root_handlers, "emit_activity", lambda *a, **k: None)

    app = _App(goals)
    asyncio.run(root_handlers.on_dispatch_signals(app, _LOG, _signals()))

    # Exactly ONE turn for the whole batch, scoped to the goal, with all signals.
    assert len(contexts) == 1
    assert "INTENT: check python and update system" in contexts[0]
    assert "SIGNALS:" in contexts[0]
    assert '"pid": 1' in contexts[0] and '"pid": 2' in contexts[0]
    assert app.acted == {"action": "respond", "output": "ok"}


def test_empty_batch_is_noop(tmp_path, monkeypatch):
    goals = GoalManager(archive_dir=str(tmp_path))
    called = []

    async def _fake_ask(app, logger, context, **k):
        called.append(context)
        return {}

    monkeypatch.setattr(root_handlers, "ask_llm", _fake_ask)
    monkeypatch.setattr(root_handlers, "emit_activity", lambda *a, **k: None)

    app = _App(goals)
    asyncio.run(root_handlers.on_dispatch_signals(app, _LOG, []))
    assert called == []
    assert app.acted is None
