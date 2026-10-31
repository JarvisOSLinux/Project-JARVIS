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
        contextor=None,
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


@pytest.mark.unit
def test_the_suggestions_show_stop_all(tmp_path):
    """The dropdown read its rows as markup, so "/stop [all]" lost "[all]"
    (and "/new [title]" its title): nothing said /stop all existed."""
    from jarvis.tui.command_input import CommandDropdown

    async def drive(app, pilot):
        app.query_one("#input").focus()
        await pilot.press(*"/sto")
        await pilot.pause()
        dropdown = app.query_one(CommandDropdown)
        return [str(o.prompt) for o in dropdown.options]

    (row,) = _tui(_jarvis(tmp_path, False), drive)
    assert row.startswith(
        "/stop         [all|<n>]  Stop this session's work; its running tasks keep "
        "running. /stop all ends them too"
    )


# --- one task ----------------------------------------------------------------


@pytest.mark.unit
class TestStopOneTask:
    def test_the_task_is_killed_and_its_request_closed(self, tmp_path):
        app, _ = _app(tmp_path)
        _request(app, command="make -j8")
        reply = _run(lambda: stop.stop_task(app, LOG, 7))
        assert reply == "Stopped run_job (shell): make -j8 (job u)."
        app.dispatch.kill_tasks.assert_awaited_once_with([7])
        assert app.goals.get_active_goals() == []

    def test_its_end_never_reaches_the_model(self, tmp_path):
        app, _ = _app(tmp_path)
        _request(app, command="make -j8")
        _run(lambda: stop.stop_task(app, LOG, 7))
        ask = AsyncMock()
        # Its KILL comes from dispatch's window, and an EXIT whose result was in
        # flight at the kill can come too, in either order.
        ends = (
            {"type": "KILL", "pid": 7, "timestamp": "k"},
            {"type": "EXIT", "pid": 7, "timestamp": "e", "data": "200 done"},
        )
        with patch.object(root_handlers, "ask_llm", ask):
            for end in ends:
                _run(lambda: root_handlers.on_dispatch_signal(app, LOG, dict(end)))
        ask.assert_not_awaited()
        app.output_manager.handle_response.assert_not_called()

    def test_a_request_with_more_under_way_carries_on_and_is_told(self, tmp_path):
        app, _ = _app(tmp_path, running=(7, 8))
        goal = _request(app, command="make -j8")
        app.goals.link_tasks(goal.id, [8])
        _run(lambda: stop.stop_task(app, LOG, 7))
        assert [g.id for g in app.goals.get_active_goals()] == [goal.id]

        contexts = []

        async def ask(app_, logger, context, **kwargs):
            contexts.append(context)
            return {"action": "respond", "output": "ok"}

        app._act_on_root_response = AsyncMock()
        with (
            patch.object(root_handlers, "ask_llm", ask),
            patch.object(root_handlers, "emit_activity", lambda *a, **k: None),
        ):
            for n in (1, 2):
                _run(
                    lambda: root_handlers.on_dispatch_signal(
                        app,
                        LOG,
                        {"type": "REMIND", "pid": 8, "data": "…", "timestamp": n},
                    )
                )
        assert "STOPPED_BY_USER: the user stopped run_job (shell): make" in contexts[0]
        assert "STOPPED_BY_USER" not in contexts[1]  # told once

    def test_a_package_manager_asks_twice(self, tmp_path):
        app, _ = _app(tmp_path)
        _request(app)
        warning = _run(lambda: stop.stop_task(app, LOG, 7))
        assert warning.startswith("run_job (shell): pacman -Syu (job u) is installing")
        assert "Stop it again within 15 s" in warning
        app.dispatch.kill_tasks.assert_not_awaited()
        assert _run(lambda: stop.stop_task(app, LOG, 7)).startswith("Stopped ")
        app.dispatch.kill_tasks.assert_awaited_once_with([7])

    def test_what_is_not_running_or_is_a_timer(self, tmp_path):
        app, _ = _app(tmp_path)
        assert _run(lambda: stop.stop_task(app, LOG, 9)) == (
            "No task 9 is running (F4 shows what is)."
        )
        app.dispatch.get_task_status.return_value = [
            {"pid": 9, "state": "running", "type": "timer"}
        ]
        assert "is a timer" in _run(lambda: stop.stop_task(app, LOG, 9))
        app.dispatch.kill_tasks.assert_not_awaited()

    def test_a_task_a_stop_left_running_says_nothing_more(self, tmp_path):
        app, _ = _app(tmp_path)
        _request(app, command="make -j8")
        _run(lambda: stop.stop_session(app, LOG))
        _run(lambda: stop.stop_task(app, LOG, 7))
        assert stop.detached(app, {"type": "KILL", "pid": 7})
        app.output_manager.handle_response.assert_not_called()

    def test_the_slash_command_takes_the_number(self, tmp_path):
        app, _ = _app(tmp_path)
        _request(app, command="make -j8")
        reply = _run(lambda: stop.handle_command(app, LOG, "/stop 7"))
        assert reply.startswith("Stopped ")
        assert "/stop <number>" in _run(
            lambda: stop.handle_command(app, LOG, "/stop x")
        )


@pytest.mark.unit
def test_the_socket_and_the_gateway_for_one_task(tmp_path):
    from jarvis.runtime import io
    from jarvis.server import web_gateway as wg

    assert not wg.sendable("guest", {"type": "stop_task", "pid": 7})
    assert wg.sendable("trusted", {"type": "stop_task", "pid": 7})

    app, _ = _app(tmp_path)
    _request(app, command="make -j8")
    written = []

    async def write(writer, msg):
        written.append(msg)

    with patch.object(io, "_gui_write", write):
        _run(
            lambda: io._process_gui_message(
                app, LOG, {"type": "stop_task", "pid": "7"}, None
            )
        )
        _run(
            lambda: io._process_gui_message(
                app, LOG, {"type": "stop_task", "pid": 7}, None
            )
        )
    assert written[0]["message"].startswith("stop_task needs the task's pid")
    assert written[1] == {
        "type": "ack",
        "for": "stop_task",
        "message": "Stopped run_job (shell): make -j8 (job u).",
    }


@pytest.mark.unit
def test_x_in_f4_stops_the_chosen_task(tmp_path):
    from jarvis.tui.tasks_view import TasksScreen

    work = {
        "tasks": [
            {"pid": 7, "kind": "tool", "title": "run_job (shell)", "detail": "make"},
            {"pid": 8, "kind": "tool", "title": "run_job (shell)", "detail": "pytest"},
        ]
    }
    stopped = []

    async def collect():
        return work

    async def stop_one(pid):
        stopped.append(pid)
        return f"Stopped task {pid}."

    async def drive(app, pilot):
        screen = TasksScreen(collect, stop_one)
        app.push_screen(screen)
        await pilot.pause()
        await pilot.press("down", "x")
        await pilot.pause()
        return str(screen._note.render()), str(screen._text.render())

    note, text = _tui(_jarvis(tmp_path, False), drive)
    assert stopped == [8]
    assert note == "Stopped task 8."
    assert "#7" in text and "#8" in text


# --- killed tasks close their blocks (2026-10-25) ------------------------------
# dispatch keeps a KILL in its signal window but never pushes it, so after x in
# F4 the block stayed open: "1 task still running" under the chat, F4 empty.


@pytest.mark.unit
def test_a_stopped_task_closes_its_block(tmp_path):
    app, events = _app(tmp_path)
    _request(app, command="sleep 300")
    _run(lambda: stop.stop_task(app, LOG, 7))
    assert action_events.running(app) == []
    assert events[-1]["phase"] == "end" and events[-1]["status"] == "stopped"


@pytest.mark.unit
def test_stop_all_closes_the_blocks_and_tells_nothing_later(tmp_path):
    app, _ = _app(tmp_path)
    _request(app, command="sleep 300")
    _run(lambda: stop.stop_session(app, LOG, kill=True))
    assert action_events.running(app) == []
    assert stop.detached(app, {"type": "KILL", "pid": 7})
    app.output_manager.handle_response.assert_not_called()


@pytest.mark.unit
def test_a_new_task_with_a_stopped_tasks_number_is_heard(tmp_path):
    """dispatch counts from 1 again after a restart."""
    from jarvis.runtime import dispatch_flow

    app, _ = _app(tmp_path)
    _request(app, command="sleep 300")
    _run(lambda: stop.stop_task(app, LOG, 7))
    dispatch_flow.note_dispatched_tools(
        app, [7], [{"server": "shell", "tool": "run_job", "params": {"command": "ls"}}]
    )
    assert not stop.detached(app, {"type": "EXIT", "pid": 7})


@pytest.mark.unit
def test_the_models_own_kill_closes_the_block(tmp_path):
    from jarvis.runtime import dispatch_flow

    app, _ = _app(tmp_path)
    _request(app, command="sleep 300")
    _run(lambda: dispatch_flow.do_kill(app, LOG, [7]))
    assert action_events.running(app) == []


# dispatch never pushes a KILL; the poll fallback reads it from the signal
# window, a few seconds after the kill.
_KILL = {"type": "KILL", "pid": 7, "timestamp": "k", "data": "Terminated by LLM"}


@pytest.mark.unit
def test_the_models_kill_read_back_from_dispatch_is_not_told_to_it(tmp_path):
    from jarvis.runtime import dispatch_flow

    app, events = _app(tmp_path, running=(7, 8))
    goal = _request(app, command="sleep 300")
    app.goals.link_tasks(goal.id, [8])
    _run(lambda: dispatch_flow.do_kill(app, LOG, [7]))
    ended = len(events)
    ask = AsyncMock()
    with patch.object(root_handlers, "ask_llm", ask):
        _run(lambda: root_handlers.on_dispatch_signal(app, LOG, dict(_KILL)))
    ask.assert_not_awaited()
    assert len(events) == ended
    # The request stops counting the killed task as running, so task 8's end
    # is answered at once instead of waiting for 7's.
    later = root_handlers._Arrived({"type": "EXIT", "pid": 8})
    assert not root_handlers._others_running(app, goal, [later])


@pytest.mark.unit
def test_a_kill_whose_reply_was_lost_still_closes_the_block(tmp_path):
    from jarvis.runtime import dispatch_flow

    app, events = _app(tmp_path)
    _request(app, command="sleep 300")
    app.dispatch.kill_tasks.return_value = {"error": "Kill timed out after 30s"}
    _run(lambda: dispatch_flow.do_kill(app, LOG, [7]))
    assert len(action_events.running(app)) == 1
    ask = AsyncMock()
    with patch.object(root_handlers, "ask_llm", ask):
        _run(lambda: root_handlers.on_dispatch_signal(app, LOG, dict(_KILL)))
    ask.assert_not_awaited()
    assert action_events.running(app) == []
    assert events[-1]["status"] == "stopped"


@pytest.mark.unit
def test_a_kill_that_fails_says_so(tmp_path):
    app, _ = _app(tmp_path)
    _request(app, command="sleep 300")
    app.dispatch.kill_tasks.return_value = {"error": "no such task"}
    assert _run(lambda: stop.stop_task(app, LOG, 7)) == (
        "Could not stop run_job (shell): sleep 300 (job u): no such task"
    )
    assert len(action_events.running(app)) == 1
