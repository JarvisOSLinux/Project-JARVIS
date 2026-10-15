"""Quitting with work running asks first, then stops it all at once.

Quitting used to wait, without limit, for every in-flight goal: 30 queued
reminders meant minutes of "Waiting for 30 in-flight goal(s)", and more
Ctrl+C presses only asked for the same graceful stop again. Now, like a
desktop with apps open, the first Ctrl+C (or Ctrl+Q in the TUI) lists what is
running, and confirming stops it: running tasks are killed and in-flight turns
cancelled. With nothing running, quitting is immediate as before.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.runtime import lifecycle

STATUS = [
    {"pid": 7, "type": "mcp", "server": "m365", "tool": "login", "state": "running"},
    {"pid": 8, "type": "mcp", "server": "m365", "tool": "list", "state": "exited"},
    {
        "pid": 9,
        "type": "timer",
        "label": "check back",
        "fires_in": 40,
        "state": "running",
    },
]


def _app(status=STATUS, connected=True):
    goal = SimpleNamespace(description="Sign in to Microsoft 365", task_pids=[7])
    return SimpleNamespace(
        goals=SimpleNamespace(get_active_goals=lambda: [goal]),
        dispatch=SimpleNamespace(
            is_connected=connected,
            get_task_status=AsyncMock(return_value=status),
            kill_tasks=AsyncMock(),
        ),
        _event_tasks=set(),
    )


@pytest.mark.unit
def test_pending_work_lists_running_tasks_timers_and_waiting_turns():
    app = _app()

    async def run():
        waiting = asyncio.create_task(asyncio.sleep(60))
        app._event_tasks = {waiting}
        lines = await lifecycle.pending_work(app)
        waiting.cancel()
        return lines

    lines = asyncio.run(run())
    assert lines == [
        "m365 · login: Sign in to Microsoft 365",
        'timer "check back" (fires in 40 s)',
        "1 request(s) still waiting on the model",
    ]


@pytest.mark.unit
def test_nothing_running_means_nothing_pending():
    assert asyncio.run(lifecycle.pending_work(_app(status=[], connected=True))) == []
    assert asyncio.run(lifecycle.pending_work(_app(connected=False))) == []


@pytest.mark.unit
def test_force_stop_kills_running_tasks_and_cancels_turns():
    app = _app()
    stop = Mock()

    async def run():
        turn = asyncio.create_task(asyncio.sleep(60))
        app._event_tasks = {turn}
        await lifecycle.force_stop(app, logging.getLogger("t"))
        await asyncio.sleep(0)
        return turn

    with patch.object(lifecycle, "request_stop", stop):
        turn = asyncio.run(run())
    app.dispatch.kill_tasks.assert_awaited_once_with([7, 9])
    assert turn.cancelled()
    stop.assert_called_once_with(app)


def _press(app, times, *, between=0.0):
    """Press Ctrl+C `times` times; return (request_stop mock, force_stop mock, log)."""
    stop, force = Mock(), AsyncMock()
    log = Mock()

    async def run():
        handler = lifecycle.make_interrupt_handler(app, log)
        for i in range(times):
            handler()
            for _ in range(5):
                await asyncio.sleep(0)
            if between and i < times - 1:
                await asyncio.sleep(between)

    with (
        patch.object(lifecycle, "request_stop", stop),
        patch.object(lifecycle, "force_stop", force),
    ):
        asyncio.run(run())
    return stop, force, log


@pytest.mark.unit
def test_ctrl_c_with_nothing_running_quits_at_once():
    stop, force, _ = _press(_app(status=[]), 1)
    stop.assert_called_once()
    force.assert_not_awaited()


@pytest.mark.unit
def test_first_ctrl_c_lists_and_changes_nothing():
    stop, force, log = _press(_app(), 1)
    stop.assert_not_called()
    force.assert_not_awaited()
    message = log.warning.call_args.args[0]
    assert "m365 · login" in message and "Ctrl+C again" in message


@pytest.mark.unit
def test_second_ctrl_c_forces_the_quit():
    stop, force, _ = _press(_app(), 2)
    force.assert_awaited_once()
    stop.assert_not_called()


@pytest.mark.unit
def test_a_late_second_ctrl_c_asks_again():
    with patch.object(lifecycle, "QUIT_CONFIRM_WINDOW_SECONDS", 0.01):
        stop, force, log = _press(_app(), 2, between=0.05)
    force.assert_not_awaited()
    assert log.warning.call_count == 2


# --- the TUI ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTuiQuit:
    @pytest.fixture(autouse=True)
    def _textual(self):
        # The TUI is an optional extra and CI's test job does not install it.
        pytest.importorskip("textual")

    def _drive(self, pending, keys):
        from textual.app import App

        from jarvis.tui.quit_modal import QuitConfirmModal

        answers = []

        class Host(App):
            def on_mount(self):
                self.push_screen(QuitConfirmModal(pending), answers.append)

        async def run():
            app = Host()
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                shown = [str(w.render()) for w in app.screen.query("#quit-list Static")]
                await pilot.press(*keys)
                await pilot.pause()
                return shown

        return asyncio.run(run()), answers

    def test_lists_pending_work_without_markup(self):
        shown, _ = self._drive(["m365 · login", "[bold]x[/bold]"], ["escape"])
        assert shown == ["• m365 · login", "• [bold]x[/bold]"]

    def test_stay_is_the_default(self):
        _, answers = self._drive(["m365 · login"], ["enter"])
        assert answers == [False]

    def test_escape_stays_and_quit_anyway_quits(self):
        assert self._drive(["a"], ["escape"])[1] == [False]
        assert self._drive(["a"], ["tab", "enter"])[1] == [True]

    def _real_tui(self, pending, keys, click=None):
        """Drive the real JarvisTUI with its engine startup stubbed out."""
        from jarvis.tui import app as tui_app
        from jarvis.tui import lifecycle as tui_lifecycle

        force = AsyncMock()
        seen = []

        async def no_unmount(app):
            return None

        async def run():
            app = tui_app.JarvisTUI()
            app.jarvis = SimpleNamespace()
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                for key in keys:
                    await pilot.press(key)
                    await pilot.pause()
                    seen.append(type(app.screen).__name__)
                if click:
                    await pilot.click(click)
                await pilot.pause(0.3)
                return app.is_running

        with (
            patch.object(tui_lifecycle, "on_mount", lambda app: None),
            patch.object(tui_lifecycle, "on_unmount", no_unmount),
            patch(
                "jarvis.runtime.lifecycle.pending_work",
                AsyncMock(return_value=pending),
            ),
            patch("jarvis.runtime.lifecycle.force_stop", force),
        ):
            running = asyncio.run(run())
        return running, force, seen

    def test_ctrl_q_with_nothing_running_exits(self):
        running, force, seen = self._real_tui([], ["ctrl+q"])
        assert not running
        force.assert_not_awaited()

    def test_ctrl_q_with_work_running_asks_first(self):
        running, force, seen = self._real_tui(["m365 · login"], ["ctrl+q", "escape"])
        assert seen == ["QuitConfirmModal", "Screen"]
        assert running
        force.assert_not_awaited()

    def test_second_ctrl_q_forces_the_quit(self):
        running, force, seen = self._real_tui(["m365 · login"], ["ctrl+q", "ctrl+q"])
        assert seen[0] == "QuitConfirmModal"
        assert not running
        force.assert_awaited_once()

    def test_quit_anyway_forces_the_quit(self):
        running, force, _ = self._real_tui(
            ["m365 · login"], ["ctrl+q"], click="#btn-quit"
        )
        assert not running
        force.assert_awaited_once()
