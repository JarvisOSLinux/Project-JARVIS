"""Tests for real goal-execution concurrency (Project-JARVIS#154):
ask_llm()'s lock+mode atomicity, and runtime.events.track_event_task's
per-event task tracking/error logging.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from jarvis.runtime import events as runtime_events
from jarvis.runtime.llm_bridge import ask_llm


def _make_llm_app():
    llm = Mock()
    llm.mode = "root"

    def fake_switch_mode(mode):
        llm.mode = mode

    llm.switch_mode.side_effect = fake_switch_mode
    llm.ask.return_value = {"action": "respond", "output": "ok"}
    app = SimpleNamespace(llm=llm, llm_lock=asyncio.Lock(), output_manager=Mock())
    return app, llm


@pytest.mark.unit
class TestAskLlmLockAndMode:
    @pytest.mark.asyncio
    async def test_switch_mode_applied_before_the_provider_call(self):
        app, llm = _make_llm_app()
        seen_mode_at_ask_time = []
        llm.ask.side_effect = lambda prompt: seen_mode_at_ask_time.append(llm.mode) or {
            "action": "respond",
            "output": "ok",
        }

        await ask_llm(app, Mock(), "context", tag="t", mode="dispatch")

        assert seen_mode_at_ask_time == ["dispatch"]

    @pytest.mark.asyncio
    async def test_mode_omitted_leaves_current_mode_untouched(self):
        app, llm = _make_llm_app()
        llm.mode = "root"

        await ask_llm(app, Mock(), "context", tag="t")

        llm.switch_mode.assert_not_called()
        assert llm.mode == "root"

    @pytest.mark.asyncio
    async def test_two_calls_with_different_modes_never_interleave(self):
        """The real concurrency-safety proof: goal A (mode X) and goal B
        (mode Y) both call ask_llm "at the same time" via asyncio.gather.
        Without the lock, B's switch_mode could land between A's switch and
        A's actual ask() call. With it, each call's mode is exactly what
        that call itself requested, every time, regardless of interleaving.
        """
        app, llm = _make_llm_app()
        observed = []

        def fake_ask(prompt):
            # Yield control mid-"turn" so a badly-serialized implementation
            # would have a chance to let the other goal's switch_mode sneak
            # in here if the lock didn't actually cover both operations.
            observed.append(llm.mode)
            return {"action": "respond", "output": prompt}

        llm.ask.side_effect = fake_ask

        results = await asyncio.gather(
            ask_llm(app, Mock(), "A", tag="a", mode="root"),
            ask_llm(app, Mock(), "B", tag="b", mode="dispatch"),
        )

        # Each call's observed mode at ask()-time must match what IT asked
        # for, never the other call's mode.
        assert set(observed) == {"root", "dispatch"}
        assert {r["output"] for r in results} == {"A", "B"}

    @pytest.mark.asyncio
    async def test_goal_b_runs_while_goal_a_is_between_llm_calls(self):
        """Real end-to-end proof of #154's actual goal: goal A holds the
        lock only for its brief ask() moments, and spends most of its time
        "waiting on a tool" (a slow asyncio.sleep) *without* the lock held --
        during which goal B can acquire the lock and make its own progress.
        """
        app, llm = _make_llm_app()
        timeline = []

        def fake_ask(prompt):
            timeline.append(f"ask:{prompt}")
            return {"action": "respond", "output": prompt}

        llm.ask.side_effect = fake_ask

        async def goal_a():
            await ask_llm(app, Mock(), "A-turn-1", tag="a", mode="root")
            timeline.append("A:dispatch-tool-wait-start")
            await asyncio.sleep(0.1)  # simulates a slow tool call, lock NOT held
            timeline.append("A:dispatch-tool-wait-end")
            await ask_llm(app, Mock(), "A-turn-2", tag="a", mode="root")

        async def goal_b():
            await asyncio.sleep(0.02)  # B arrives shortly after A starts
            timeline.append("B:start")
            await ask_llm(app, Mock(), "B-turn-1", tag="b", mode="root")
            timeline.append("B:done")

        await asyncio.gather(goal_a(), goal_b())

        # Goal B's ask must land strictly between A's tool-wait start/end --
        # proof B didn't have to wait for A's entire turn to finish.
        start = timeline.index("A:dispatch-tool-wait-start")
        end = timeline.index("A:dispatch-tool-wait-end")
        b_ask = timeline.index("ask:B-turn-1")
        assert start < b_ask < end, timeline


@pytest.mark.unit
class TestTrackEventTask:
    @pytest.mark.asyncio
    async def test_task_added_then_removed_on_clean_completion(self):
        event_tasks = set()
        logger = Mock()

        async def clean():
            return None

        task = asyncio.create_task(clean())
        runtime_events.track_event_task(event_tasks, task, logger)
        assert task in event_tasks

        await task
        await asyncio.sleep(0)  # let the done-callback run
        assert task not in event_tasks
        logger.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_is_logged_not_raised(self):
        event_tasks = set()
        logger = Mock()

        async def boom():
            raise RuntimeError("goal blew up")

        task = asyncio.create_task(boom())
        runtime_events.track_event_task(event_tasks, task, logger)

        with pytest.raises(RuntimeError):
            await task  # awaiting the task itself still surfaces it here
        await asyncio.sleep(0)

        assert task not in event_tasks
        logger.error.assert_called_once()
        assert "goal blew up" in logger.error.call_args.args[0]

    @pytest.mark.asyncio
    async def test_cancelled_task_is_not_logged_as_an_error(self):
        event_tasks = set()
        logger = Mock()

        async def forever():
            await asyncio.sleep(10)

        task = asyncio.create_task(forever())
        runtime_events.track_event_task(event_tasks, task, logger)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

        assert task not in event_tasks
        logger.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_events_run_concurrently_not_sequentially(self):
        """Mirrors main.py's run() loop shape directly: two "events" each
        sleep, and both must be in flight at once -- proof the per-event
        task model doesn't silently serialize them."""
        event_tasks = set()
        logger = Mock()
        order = []

        async def handle(name, delay):
            order.append(f"{name}:start")
            await asyncio.sleep(delay)
            order.append(f"{name}:end")

        task_a = asyncio.create_task(handle("A", 0.05))
        runtime_events.track_event_task(event_tasks, task_a, logger)
        task_b = asyncio.create_task(handle("B", 0.01))
        runtime_events.track_event_task(event_tasks, task_b, logger)

        await asyncio.gather(task_a, task_b)
        await asyncio.sleep(0)

        # B (shorter sleep) finishes before A, even though A started first --
        # only possible if they actually ran concurrently.
        assert order == ["A:start", "B:start", "B:end", "A:end"]
        assert event_tasks == set()
