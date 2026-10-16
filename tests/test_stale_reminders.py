"""A reminder whose task has finished, or that a newer one replaced, never reaches the model.

In the first live Microsoft 365 sign-in, dispatch reminded every 5 s while the
model took 10-26 s per turn. Reminders queued behind app.llm_lock, and when the
sign-in succeeded about 30 of them were still waiting, each carrying the old
"enter the code" output. JARVIS answered every one, telling the user to sign in
again. Freshness is now judged once the turn comes.
"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.runtime import daemon_thread, llm_bridge, root_handlers


def _app():
    return SimpleNamespace(
        llm=Mock(mode="root", ask=Mock(return_value={"action": "respond"})),
        llm_lock=asyncio.Lock(),
        goals=SimpleNamespace(
            update_from_signal=Mock(), find_goal_by_task_pid=Mock(return_value=None)
        ),
        _act_on_root_response=AsyncMock(),
    )


@pytest.mark.unit
class TestNoteSignal:
    def test_a_newer_reminder_replaces_an_older_one(self):
        app = _app()
        first = root_handlers._note_signal(app, {"type": "REMIND", "pid": 3})
        assert not first()
        second = root_handlers._note_signal(app, {"type": "REMIND", "pid": 3})
        assert first() and not second()

    def test_an_exit_makes_waiting_reminders_stale(self):
        app = _app()
        remind = root_handlers._note_signal(app, {"type": "REMIND", "pid": 3})
        exit_check = root_handlers._note_signal(app, {"type": "EXIT", "pid": 3})
        assert remind() and not exit_check()

    def test_a_kill_counts_as_finished(self):
        app = _app()
        remind = root_handlers._note_signal(app, {"type": "REMIND", "pid": 3})
        root_handlers._note_signal(app, {"type": "KILL", "pid": 3})
        assert remind()

    def test_a_reminder_merged_with_its_exit_is_the_outcome(self):
        app = _app()
        merged = root_handlers._note_signal(
            app, {"type": "REMIND", "pid": 3}, exit_data={"type": "EXIT", "pid": 3}
        )
        assert not merged()

    def test_other_tasks_are_unaffected(self):
        app = _app()
        other = root_handlers._note_signal(app, {"type": "REMIND", "pid": 4})
        root_handlers._note_signal(app, {"type": "EXIT", "pid": 3})
        assert not other()


@pytest.mark.unit
def test_skip_if_is_checked_after_the_lock_is_taken():
    app = _app()
    finished = {"yes": False}

    async def run():
        await app.llm_lock.acquire()
        turn = asyncio.create_task(
            llm_bridge.ask_llm(
                app, Mock(), "ctx", mode="root", skip_if=lambda: finished["yes"]
            )
        )
        await asyncio.sleep(0)
        finished["yes"] = True  # the task ends while the turn waits
        app.llm_lock.release()
        return await turn

    with patch.object(llm_bridge, "emit_activity"):
        assert asyncio.run(run()) is None
    app.llm.ask.assert_not_called()


@pytest.mark.unit
def test_queued_reminders_are_dropped_once_the_task_finishes():
    app = _app()

    async def run():
        await app.llm_lock.acquire()  # the model is busy with something else
        queued = [
            asyncio.create_task(
                root_handlers.on_dispatch_signal(
                    app, Mock(), {"type": "REMIND", "pid": 9, "data": "enter the code"}
                )
            )
            for _ in range(3)
        ]
        await asyncio.sleep(0)
        done = asyncio.create_task(
            root_handlers.on_dispatch_signal(
                app, Mock(), {"type": "EXIT", "pid": 9, "data": "Signed in"}
            )
        )
        await asyncio.sleep(0)
        app.llm_lock.release()
        await asyncio.gather(*queued, done)

    with (
        patch.object(
            root_handlers,
            "build_root_context",
            side_effect=lambda app, logger, signal=None: str(signal),
        ),
        patch.object(
            root_handlers, "_config_hint_for_signals", AsyncMock(return_value="")
        ),
        patch.object(root_handlers, "emit_activity"),
        patch.object(llm_bridge, "emit_activity"),
    ):
        asyncio.run(run())

    assert app.llm.ask.call_count == 1
    assert "Signed in" in app.llm.ask.call_args.args[0]


@pytest.mark.unit
def test_blocking_work_runs_in_daemon_threads():
    seen = {}

    def work(x):
        seen["daemon"] = threading.current_thread().daemon
        return x * 2

    def boom():
        raise ValueError("model failed")

    async def run():
        assert await daemon_thread.run_in_daemon_thread(work, 21) == 42
        with pytest.raises(ValueError, match="model failed"):
            await daemon_thread.run_in_daemon_thread(boom)

    asyncio.run(run())
    assert seen["daemon"] is True
