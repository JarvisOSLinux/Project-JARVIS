"""One model turn at a time per request (#284).

Found twice: "kernel version and uptime" got two answers, the first saying
uptime was still running when both tasks had ended in the same second; and
during Yakup's system update the send_input result's turn answered "in
progress, 89%" while a reminder that came in meanwhile answered again nine
seconds later. Signals now wait for their request's turn, are answered
together, and a plain reminder that comes in during the turn is dropped.
"""

import asyncio
import logging

import pytest

from jarvis.dispatch.goal_manager import GoalManager
from jarvis.runtime import root_handlers, stop, turns

_LOG = logging.getLogger("test")


class _Sessions:
    current_id = "s1"

    def load_summary(self):
        return ""


class _App:
    def __init__(self, goals):
        self.goals = goals
        self.llm = object()
        self.sessions = _Sessions()
        self.acted = []

    async def _act_on_root_response(self, response):
        self.acted.append(response)


class _Model:
    """Records each turn's context; `gate` holds a turn until it is set."""

    def __init__(self):
        self.contexts = []
        self.gate = asyncio.Event()
        self.gate.set()
        self.started = asyncio.Event()
        self.fail_next = False

    async def ask(self, app, logger, context, skip_if=None, **kwargs):
        if skip_if is not None and skip_if():
            return None
        self.contexts.append(context)
        self.started.set()
        await self.gate.wait()
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("model fell over")
        return {"action": "respond", "output": f"answer {len(self.contexts)}"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    goals = GoalManager(archive_dir=str(tmp_path))
    model = _Model()
    monkeypatch.setattr(root_handlers, "ask_llm", model.ask)
    monkeypatch.setattr(root_handlers, "emit_activity", lambda *a, **k: None)
    monkeypatch.setattr(turns, "COALESCE_SECONDS", 0.2)
    return _App(goals), goals, model


def _goal(goals, pids, text="update the system"):
    goal = goals.add_goal(text, chat_session="s1")
    goals.link_tasks(goal.id, pids)
    return goal


def _exit(pid, out="200 done"):
    return {"type": "EXIT", "pid": pid, "data": out, "timestamp": f"e{pid}"}


def _remind(pid, n=1):
    return {"type": "REMIND", "pid": pid, "data": "Running", "timestamp": f"r{pid}.{n}"}


def _signal(app, sig):
    return asyncio.create_task(root_handlers.on_dispatch_signal(app, _LOG, dict(sig)))


async def _settle(*tasks):
    await asyncio.wait_for(asyncio.gather(*tasks), 5)


@pytest.mark.unit
def test_tasks_that_end_together_get_one_answer(setup):
    app, goals, model = setup
    _goal(goals, [1, 2], "kernel version and uptime")

    async def run():
        first = _signal(app, _exit(1, "200 7.2.9-2-cachyos"))
        await asyncio.sleep(0.05)
        second = _signal(app, _exit(2, "200 up 3 days"))
        await _settle(first, second)

    asyncio.run(run())
    (context,) = model.contexts
    assert "SIGNALS:" in context
    assert "7.2.9-2-cachyos" in context and "up 3 days" in context
    assert len(app.acted) == 1


@pytest.mark.unit
def test_a_reminder_during_the_requests_turn_is_dropped(setup):
    """Yakup's run: send_input's EXIT was being answered when pacman's REMIND came."""
    app, goals, model = setup
    _goal(goals, [1, 2])

    async def run():
        model.gate.clear()
        answering = _signal(app, _exit(2, '200 {"delivered_bytes": 2}'))
        await asyncio.wait_for(model.started.wait(), 5)
        reminder = _signal(app, _remind(1))
        await reminder
        model.gate.set()
        await _settle(answering)

    asyncio.run(run())
    assert len(model.contexts) == 1
    assert len(app.acted) == 1


@pytest.mark.unit
def test_a_result_during_the_turn_is_answered_after_it(setup):
    app, goals, model = setup
    _goal(goals, [1, 2])

    async def run():
        model.gate.clear()
        answering = _signal(app, _exit(2))
        await asyncio.wait_for(model.started.wait(), 5)
        finished = _signal(app, _exit(1, "200 upgraded 16 packages"))
        await asyncio.sleep(0.05)
        assert len(model.contexts) == 1  # waits for the turn, not beside it
        model.gate.set()
        await _settle(answering, finished)

    asyncio.run(run())
    assert len(model.contexts) == 2
    assert "upgraded 16 packages" in model.contexts[1]


@pytest.mark.unit
def test_results_during_another_turn_of_the_request_wait_for_it(setup):
    """A user, confirmation or setup turn holds the lane too."""
    app, goals, model = setup
    goal = _goal(goals, [1])

    async def run():
        async with turns.turn(app, goal.id):
            result = _signal(app, _exit(1))
            await asyncio.sleep(0.05)
            assert model.contexts == []
            reminder = _signal(app, _remind(1, 2))
            await reminder
        await _settle(result)

    asyncio.run(run())
    assert len(model.contexts) == 1


@pytest.mark.unit
def test_other_requests_do_not_wait(setup):
    app, goals, model = setup
    _goal(goals, [1], "update")
    _goal(goals, [7], "weather")

    async def run():
        model.gate.clear()
        update = _signal(app, _exit(1))
        await asyncio.wait_for(model.started.wait(), 5)
        weather = _signal(app, _exit(7, "200 sunny"))
        for _ in range(20):
            if len(model.contexts) == 2:
                break
            await asyncio.sleep(0.01)
        assert len(model.contexts) == 2
        model.gate.set()
        await _settle(update, weather)

    asyncio.run(run())


@pytest.mark.unit
def test_a_lone_task_does_not_wait(setup, monkeypatch):
    app, goals, model = setup
    monkeypatch.setattr(turns, "COALESCE_SECONDS", 30)
    _goal(goals, [1])

    async def run():
        await asyncio.wait_for(_signal(app, _exit(1)), 2)

    asyncio.run(run())
    assert len(model.contexts) == 1


@pytest.mark.unit
def test_a_failed_turn_does_not_strand_what_came_in_meanwhile(setup):
    app, goals, model = setup
    _goal(goals, [1, 2])

    async def run():
        model.gate.clear()
        model.fail_next = True
        answering = _signal(app, _exit(2))
        await asyncio.wait_for(model.started.wait(), 5)
        finished = _signal(app, _exit(1, "200 the rest"))
        await asyncio.sleep(0.05)
        model.gate.set()
        await _settle(answering, finished)

    asyncio.run(run())
    assert len(model.contexts) == 2
    assert len(app.acted) == 1 and "the rest" in model.contexts[1]


@pytest.mark.unit
def test_a_stop_drops_the_waiting_results_and_the_lane(setup):
    app, goals, model = setup
    goal = _goal(goals, [1, 2])

    async def run():
        waiting = _signal(app, _exit(1))  # pid 2 still runs: waits a moment
        await asyncio.sleep(0.05)
        reply = await stop.stop_session(app, _LOG)
        await asyncio.gather(waiting, return_exceptions=True)
        return reply, waiting

    reply, waiting = asyncio.run(run())
    assert waiting.cancelled()
    assert model.contexts == []
    assert app._turn_lanes == {}
    assert goals.get_goal(goal.id) is None
    assert reply.startswith("Stopped.")


@pytest.mark.unit
def test_a_finished_request_leaves_no_lane(setup):
    app, goals, model = setup
    _goal(goals, [1])

    async def run():
        await _settle(_signal(app, _exit(1)))

    asyncio.run(run())
    assert app._turn_lanes == {}
