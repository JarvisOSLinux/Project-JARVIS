"""What JARVIS is doing now: F4 or /tasks in the TUI (#267, phase 1).

Nothing showed what was running: Ctrl+Q listed it only when quitting.
`runtime/work.py` collects requests, running tasks (with how long they ran,
their parameters and latest output) and timers as plain data, for the TUI now
and socket clients next; `tui/tasks_view.py` shows it and refreshes.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.runtime import action_events
from jarvis.runtime.work import collect_work

N = "ab12"


def _app():
    goal = SimpleNamespace(
        id="g1",
        description="update my system",
        created_at=time.time() - 75,
        task_pids=[4],
    )
    app = SimpleNamespace(
        output_manager=None,
        goals=SimpleNamespace(get_active_goals=lambda: [goal]),
        dispatch=SimpleNamespace(
            is_connected=True,
            get_task_status=AsyncMock(
                return_value=[
                    {
                        "pid": 4,
                        "type": "mcp",
                        "server": "com.x.shell",
                        "tool": "execute_command",
                        "state": "running",
                        "tail": f"[hash={N}] <{N}>:: Synchronizing\n core 1/40</{N}>",
                        "tail_hash": N,
                    },
                    {
                        "pid": 5,
                        "type": "timer",
                        "label": "goal_reminder:g1",
                        "fires_in": 120,
                        "state": "running",
                    },
                    {
                        "pid": 3,
                        "type": "mcp",
                        "server": "s",
                        "tool": "t",
                        "state": "exited",
                    },
                ]
            ),
        ),
        confirmation=SimpleNamespace(list_pending=lambda: [object()]),
        setups=SimpleNamespace(list=lambda: []),
        _event_tasks=[SimpleNamespace(done=lambda: False)],
    )
    action_events.tools_started(
        app,
        [4],
        [
            {
                "server": "com.x.shell",
                "tool": "execute_command",
                "params": {"command": "pacman", "args": ["-Syu"]},
            }
        ],
    )
    app._open_actions["tool:4"]["at"] -= 65
    return app


@pytest.mark.unit
def test_collect_work():
    work = asyncio.run(collect_work(_app()))
    assert work["requests"][0]["text"] == "update my system"
    tool, timer = work["tasks"]
    assert tool["title"] == "execute_command (shell)"
    assert tool["detail"] == "pacman -Syu"
    assert 64 < tool["running_for"] < 70
    assert tool["output"] == ":: Synchronizing\n core 1/40"
    assert tool["request"] == "update my system"
    assert (timer["kind"], timer["fires_in"]) == ("timer", 120)
    assert (work["thinking"], work["confirmations"], work["setups"]) == (1, 1, 0)


@pytest.mark.unit
def test_a_task_jarvis_did_not_start_still_shows():
    app = _app()
    app._open_actions.clear()
    tool, _ = asyncio.run(collect_work(app))["tasks"]
    assert tool["title"] == "execute_command (shell)" and tool["running_for"] is None


@pytest.mark.unit
def test_without_dispatch_there_is_still_an_answer():
    app = _app()
    app.dispatch.is_connected = False
    assert asyncio.run(collect_work(app))["tasks"] == []


@pytest.mark.unit
def test_the_view_reads_like_a_list():
    pytest.importorskip("textual")
    from rich.text import Text

    from jarvis.tui.tasks_view import work_markup

    work = asyncio.run(collect_work(_app()))
    text = Text.from_markup(work_markup(work)).plain
    assert "• update my system  1m 1" in text
    assert "⋯ execute_command (shell)  #4  running 1m 0" in text
    assert "    pacman -Syu" in text and "    │  core 1/40" in text
    assert "⏲ goal_reminder:g1  fires in 2m 00s" in text
    assert "1 confirmation (jarvis confirm)" in text
    empty = Text.from_markup(work_markup({})).plain
    assert empty.count("nothing") == 2 and "none" in empty


textual = pytest.importorskip("textual")


@pytest.mark.unit
def test_f4_and_slash_tasks_open_it():
    from jarvis.tui import app as tui_app
    from jarvis.tui import lifecycle as tui_lifecycle
    from jarvis.tui.tasks_view import TasksScreen

    async def no_unmount(app):
        return None

    async def run():
        app = tui_app.JarvisTUI()
        app.jarvis = _app()
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            await pilot.press("f4")
            await pilot.pause(0.3)
            opened = isinstance(app.screen, TasksScreen)
            text = str(app.screen.query_one("#tasks-text").render())
            await pilot.press("escape")
            await pilot.pause()
            closed = not isinstance(app.screen, TasksScreen)
            box = app.query_one("#input")
            box.focus()
            box.value = "/tasks"
            await pilot.press("enter")
            await pilot.pause(0.3)
            again = isinstance(app.screen, TasksScreen)
            return opened, text, closed, again

    with (
        patch.object(tui_lifecycle, "on_mount", lambda app: None),
        patch.object(tui_lifecycle, "on_unmount", no_unmount),
    ):
        opened, text, closed, again = asyncio.run(run())
    assert opened and closed and again
    assert "pacman -Syu" in text
