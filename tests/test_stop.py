"""Stopping JARVIS's work in the current session (#270, decided 2026-10-24).

A stop ends JARVIS's work on the session's requests at once: the model's turn
in progress is cancelled, confirmations waiting are denied, timers cancelled,
the requests closed. The tasks they started keep running, and from then on
reach only the screen, never the model (reminders included). `/stop all`
kills them too, asking once more when a package manager is among them.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.core.confirmation_manager import ConfirmationManager, PendingConfirmation
from jarvis.dispatch.goal_manager import GoalManager
from jarvis.runtime import action_events, root_handlers, stop

LOG = logging.getLogger("t")


def _app(tmp_path, running=(7,)):
    app = SimpleNamespace(
        goals=GoalManager(archive_dir=str(tmp_path)),
        sessions=SimpleNamespace(current_id="s1", load_summary=lambda: ""),
        confirmation=ConfirmationManager(),
        dispatch=SimpleNamespace(
            is_connected=True,
            get_task_status=AsyncMock(
                return_value=[{"pid": p, "state": "running"} for p in running]
            ),
            kill_tasks=AsyncMock(return_value={}),
        ),
        output_manager=Mock(),
        llm=object(),
        _gui_clients={},
        _event_tasks=set(),
    )
    events = []
    app.output_manager.emit_action.side_effect = events.append
    return app, events


def _request(app, chat_session="s1", pid=7, command="pacman -Syu"):
    goal = app.goals.add_goal("update my system", chat_session=chat_session)
    app.goals.link_tasks(goal.id, [pid])
    action_events.tools_started(
        app,
        [pid],
        [
            {
                "server": "shell",
                "tool": "run_job",
                "params": {"command": command, "job": "u"},
            }
        ],
    )
    return goal


async def _turn(app, goal_id, started):
    stop.claim_turn(app, goal_id)
    started.set()
    await asyncio.Event().wait()


def _run(coro_fn):
    return asyncio.run(coro_fn())


@pytest.mark.unit
class TestStop:
    def test_the_turn_stops_and_the_task_keeps_running(self, tmp_path):
        app, _ = _app(tmp_path)
        goal = _request(app)

        async def go():
            started = asyncio.Event()
            turn = asyncio.create_task(_turn(app, goal.id, started))
            await started.wait()
            reply = await stop.stop_session(app, LOG)
            await asyncio.sleep(0)
            return turn, reply

        turn, reply = _run(go)
        assert turn.cancelled()
        assert app.goals.get_active_goals() == []
        app.dispatch.kill_tasks.assert_not_awaited()
        assert 7 in app._detached_tasks
        assert reply == (
            "Stopped. Still running in the background: run_job (shell): pacman -Syu (job u). "
            "F4 shows it; /stop all ends it."
        )

    def test_another_sessions_request_is_left_alone(self, tmp_path):
        app, _ = _app(tmp_path)
        other = _request(app, chat_session="s2", pid=8)

        async def go():
            started = asyncio.Event()
            turn = asyncio.create_task(_turn(app, other.id, started))
            await started.wait()
            reply = await stop.stop_session(app, LOG)
            alive = not turn.done()
            turn.cancel()
            return reply, alive

        reply, alive = _run(go)
        assert reply == "Nothing to stop." and alive
        assert [g.id for g in app.goals.get_active_goals()] == [other.id]

    def test_a_waiting_confirmation_is_denied_without_asking_the_model(self, tmp_path):
        app, events = _app(tmp_path, running=())
        goal = _request(app)
        app.confirmation._pending["c1"] = PendingConfirmation(
            request_id="c1", tasks=[], approved_tasks=[], session_id=goal.id
        )
        action_events.start(app, "confirm", "Confirm run_job", action_id="confirm:c1")
        reply = _run(lambda: stop.stop_session(app, LOG))
        assert app.confirmation.list_pending() == []
        assert events[-1]["id"] == "confirm:c1" and events[-1]["status"] == "denied"
        assert reply == "Stopped."

    def test_a_deferred_requests_timer_is_cancelled(self, tmp_path):
        app, _ = _app(tmp_path, running=())
        goal = app.goals.add_goal("remind me", chat_session="s1")
        app.goals.defer_goal(goal.id, 9)
        _run(lambda: stop.stop_session(app, LOG))
        app.dispatch.kill_tasks.assert_awaited_once_with([9])

    def test_nothing_running(self, tmp_path):
        app, _ = _app(tmp_path, running=())
        assert _run(lambda: stop.stop_session(app, LOG)) == "Nothing to stop."


def _signal(app, signal):
    ask = AsyncMock(return_value={"action": "respond", "output": "x"})
    with (
        patch.object(root_handlers, "ask_llm", ask),
        patch.object(root_handlers, "emit_activity"),
        patch.object(stop, "persist_assistant_turn") as saved,
    ):
        asyncio.run(root_handlers.on_dispatch_signal(app, LOG, signal))
    return ask, saved


@pytest.mark.unit
class TestAfterTheStop:
    def test_a_stopped_tasks_reminder_and_end_never_reach_the_model(self, tmp_path):
        app, events = _app(tmp_path)
        _request(app)
        _run(lambda: stop.stop_session(app, LOG))

        ask, _ = _signal(app, {"type": "REMIND", "pid": 7, "data": "Running for 30s"})
        ask.assert_not_awaited()
        ask, saved = _signal(app, {"type": "EXIT", "pid": 7, "data": "200 done"})
        ask.assert_not_awaited()
        told = app.output_manager.handle_response.call_args.args[0]["output"]
        assert told == (
            "run_job (shell): pacman -Syu (job u) finished (it kept running after the stop)."
        )
        saved.assert_called_once()
        assert events[-1]["phase"] == "end" and events[-1]["status"] == "ok"
        assert 7 not in app._detached_tasks

    def test_a_batch_of_stopped_tasks_wakes_nothing(self, tmp_path):
        app, _ = _app(tmp_path)
        _request(app)
        _run(lambda: stop.stop_session(app, LOG))
        ask = AsyncMock()
        with (
            patch.object(root_handlers, "ask_llm", ask),
            patch.object(root_handlers, "emit_activity"),
            patch.object(stop, "persist_assistant_turn"),
        ):
            asyncio.run(
                root_handlers.on_dispatch_signals(
                    app, LOG, [{"type": "EXIT", "pid": 7, "data": "200 ok"}]
                )
            )
        ask.assert_not_awaited()


@pytest.mark.unit
class TestStopAll:
    def test_a_package_manager_asks_once_more(self, tmp_path):
        app, _ = _app(tmp_path)
        _request(app)
        first = _run(lambda: stop.handle_command(app, LOG, "/stop all"))
        assert first.startswith(
            "run_job (shell): pacman -Syu (job u) is installing or changing"
        )
        app.dispatch.kill_tasks.assert_not_awaited()
        second = _run(lambda: stop.handle_command(app, LOG, "/stop all"))
        app.dispatch.kill_tasks.assert_awaited_once_with([7])
        assert second == "Stopped, and ended: run_job (shell): pacman -Syu (job u)."

    def test_other_tasks_end_at_once(self, tmp_path):
        app, _ = _app(tmp_path)
        _request(app, command="sleep 600")
        reply = _run(lambda: stop.handle_command(app, LOG, "/stop all"))
        app.dispatch.kill_tasks.assert_awaited_once_with([7])
        assert reply.startswith("Stopped, and ended")

    def test_usage(self, tmp_path):
        app, _ = _app(tmp_path)
        assert _run(lambda: stop.handle_command(app, LOG, "/stop now")).startswith(
            "Usage"
        )
        assert _run(lambda: stop.handle_command(app, LOG, "/status")) is None


@pytest.mark.unit
def test_typed_stop_goes_through_the_input_funnel(tmp_path):
    app, _ = _app(tmp_path, running=())
    app.goals.add_goal("x", chat_session="s1")
    app.output_manager.handle_response = Mock()
    with patch.object(root_handlers, "set_gui_state", AsyncMock()):
        asyncio.run(root_handlers.on_user_input(app, LOG, "/stop"))
    out = app.output_manager.handle_response.call_args.args[0]
    assert out == {"output": "Stopped.", "command": True}


@pytest.mark.unit
def test_package_managers_are_recognised():
    for text in ("pacman -Syu", "sudo apt-get upgrade", "x && dnf update", "yay"):
        assert stop.is_package_manager(text), text
    for text in ("ls -la", "grep pacmanlog", "cat notes.txt"):
        assert not stop.is_package_manager(text), text


@pytest.mark.unit
def test_the_phone_may_stop_and_trusted_may_stop_all():
    from jarvis.server import web_gateway as wg

    assert wg.sendable("guest", {"type": "stop"})
    assert not wg.sendable("guest", {"type": "stop_all"})
    assert wg.sendable("trusted", {"type": "stop_all"})


# --- the TUI -----------------------------------------------------------------

textual = pytest.importorskip("textual")


def _tui(jarvis, drive):
    from jarvis.tui import app as tui_app
    from jarvis.tui import lifecycle as tui_lifecycle

    async def no_unmount(app):
        return None

    async def run():
        app = tui_app.JarvisTUI()
        app.jarvis = jarvis
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            return await drive(app, pilot)

    with (
        patch.object(tui_lifecycle, "on_mount", lambda app: None),
        patch.object(tui_lifecycle, "on_unmount", no_unmount),
    ):
        return asyncio.run(run())


def _jarvis(tmp_path, busy):
    goals = GoalManager(archive_dir=str(tmp_path))
    if busy:
        goals.add_goal("update", chat_session="s1")
    return SimpleNamespace(
        goals=goals,
        sessions=SimpleNamespace(current_id="s1"),
        events=SimpleNamespace(inject_user_input=Mock()),
        _event_tasks=set(),
    )


@pytest.mark.unit
@pytest.mark.parametrize("busy", [True, False])
def test_esc_on_an_empty_input_stops_only_while_jarvis_works(tmp_path, busy):
    jarvis = _jarvis(tmp_path, busy)

    async def drive(app, pilot):
        app.query_one("#input").focus()
        await pilot.press("escape")
        await pilot.pause()
        return app._export_lines

    _tui(jarvis, drive)
    if busy:
        jarvis.events.inject_user_input.assert_called_once_with("/stop")
    else:
        jarvis.events.inject_user_input.assert_not_called()


@pytest.mark.unit
def test_esc_with_text_typed_does_not_stop(tmp_path):
    jarvis = _jarvis(tmp_path, True)

    async def drive(app, pilot):
        box = app.query_one("#input")
        box.focus()
        box.value = "half a sentence"
        await pilot.press("escape")
        await pilot.pause()

    _tui(jarvis, drive)
    jarvis.events.inject_user_input.assert_not_called()


@pytest.mark.unit
def test_a_confirmation_the_stop_denied_closes_its_window(tmp_path):
    from jarvis.tui.confirm_modal import ConfirmModal

    jarvis = _jarvis(tmp_path, True)

    async def drive(app, pilot):
        app.push_screen(ConfirmModal("c1", []))
        await pilot.pause()
        app._on_jarvis_action(
            {"type": "action", "phase": "end", "id": "confirm:c1", "status": "denied"}
        )
        await pilot.pause()
        return isinstance(app.screen, ConfirmModal)

    assert _tui(jarvis, drive) is False


@pytest.mark.unit
def test_quitting_warns_about_a_package_manager(tmp_path):
    from jarvis.runtime.lifecycle import pending_work

    app, _ = _app(tmp_path)
    _request(app)
    app.dispatch.get_task_status = AsyncMock(
        return_value=[
            {
                "pid": 7,
                "type": "mcp",
                "server": "shell",
                "tool": "run_job",
                "state": "running",
            }
        ]
    )
    (line,) = asyncio.run(pending_work(app))
    assert "changing packages; quitting now stops it mid-way" in line
