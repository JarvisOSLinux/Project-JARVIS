"""Commands and JARVIS's actions as folding blocks in the chat (#271).

Long outputs filled the chat, and what JARVIS ran was visible only as passing
"... Dispatch signal" lines. Each action now starts and ends as an event
(`runtime/action_events.py`) the TUI draws as a block, and socket clients can
get later (#267): a search, reading a server's tools, an install, a tool run
with its parameters and output, a confirmation and what was decided. A command
you type is a block holding its output.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

import jarvis.core.confirmation_manager as cm
from jarvis.core.confirmation_manager import ConfirmationManager, PendingConfirmation
from jarvis.core.output_manager import OutputManager
from jarvis.core.setup_queue import SetupQueue
from jarvis.runtime import action_events, dispatch_flow, root_actions, root_handlers
from jarvis.runtime.session_commands import session_reply

_LOG = logging.getLogger("test")


def _app():
    manager = OutputManager(suppress_stdout=True)
    events = []
    manager.add_action_callback(events.append)
    return SimpleNamespace(output_manager=manager), events


def _shape(events):
    return [(e["phase"], e["id"], e.get("status")) for e in events]


# --- the events ------------------------------------------------------------------------


@pytest.mark.unit
class TestEvents:
    def test_an_action_starts_reports_and_ends(self):
        app, events = _app()
        action = action_events.start(app, "search", "Search: calendar")
        assert action_events.running(app)[0]["title"] == "Search: calendar"
        action_events.update(app, action, "halfway")
        action_events.end(app, action, "ok", "found 2")
        assert _shape(events) == [
            ("start", action, None),
            ("update", action, None),
            ("end", action, "ok"),
        ]
        assert events[2]["output"] == "found 2" and events[2]["kind"] == "search"
        assert events[2]["started_at"] == events[0]["at"]
        assert action_events.running(app) == []

    def test_what_never_started_shows_nothing(self):
        # A timer's EXIT, or a confirmation restored after a restart.
        app, events = _app()
        action_events.update(app, "tool:9", "x")
        action_events.end(app, "tool:9", "ok")
        action_events.on_signal(app, {"type": "EXIT", "pid": 9, "data": "200 x"})
        assert events == []

    def test_a_long_output_keeps_its_end(self):
        text = "".join(f"line {i}\n" for i in range(5000)) + "done"
        clipped = action_events.clip(text)
        assert clipped.endswith("done") and clipped.startswith("… (")
        assert len(clipped) < action_events.MAX_OUTPUT_CHARS + 60

    def test_a_shell_command_reads_as_typed(self):
        detail = action_events.tool_detail
        assert detail({"command": "pacman", "args": ["-Syu"]}) == "pacman -Syu"
        assert detail({"path": "/tmp/ü"}) == '{"path": "/tmp/ü"}'
        assert detail({}) == "" and detail(None) == ""

    def test_tasks_pair_with_the_newest_pids(self):
        app, events = _app()
        tasks = [
            {"server": "shell", "tool": "execute_command", "params": {"command": "ls"}},
            {"server": "files", "tool": "read_file", "params": {"path": "/a"}},
        ]
        action_events.tools_started(app, [3, 7, 8], tasks)
        assert action_events.short_server("com.github.x.jarvis-shell-mcp") == (
            "jarvis-shell-mcp"
        )
        assert [(e["id"], e["title"], e["detail"]) for e in events] == [
            ("tool:7", "execute_command (shell)", "ls"),
            ("tool:8", "read_file (files)", '{"path": "/a"}'),
        ]
        action_events.tools_started(app, [1], tasks)  # ambiguous: nothing
        assert len(events) == 2


N = "ab12cd"


@pytest.mark.unit
class TestSignals:
    def _tool(self):
        app, events = _app()
        action_events.tools_started(
            app, [4], [{"server": "shell", "tool": "run", "params": {}}]
        )
        return app, events

    def test_success_unwrapped(self):
        app, events = self._tool()
        action_events.on_signal(
            app,
            {
                "type": "EXIT",
                "pid": 4,
                "nonce": N,
                "data": f"[hash={N}] 200 <{N}>hi\n</{N}>",
            },
        )
        assert (events[-1]["status"], events[-1]["output"]) == ("ok", "hi")

    def test_failure(self):
        app, events = self._tool()
        action_events.on_signal(app, {"type": "EXIT", "pid": 4, "data": "500 boom"})
        assert (events[-1]["status"], events[-1]["output"]) == ("failed", "boom")

    def test_a_stopped_task(self):
        app, events = self._tool()
        action_events.on_signal(app, {"type": "KILL", "pid": 4, "data": "killed"})
        assert events[-1]["status"] == "stopped"

    def test_a_reminder_shows_the_output_so_far(self):
        app, events = self._tool()
        action_events.on_signal(
            app,
            {
                "type": "REMIND",
                "pid": 4,
                "nonce": N,
                "data": f"Running for 30s [hash={N}] <{N}>:: Synchronizing</{N}>",
            },
        )
        assert events[-1]["phase"] == "update"
        assert events[-1]["output"] == ":: Synchronizing"

    def test_a_reminder_merged_with_its_exit_ends_the_block(self):
        app, events = self._tool()
        action_events.on_signal(
            app,
            {"type": "REMIND", "pid": 4, "data": "Running for 30s"},
            {"type": "EXIT", "pid": 4, "data": "200 done"},
        )
        assert (events[-1]["phase"], events[-1]["output"]) == ("end", "done")

    def test_a_shell_result_reads_as_what_it_printed(self):
        app, events = self._tool()
        result = '{"success": true, "exit_code": 0, "stdout": "a\\nb\\n", "stderr": ""}'
        action_events.on_signal(
            app,
            {
                "type": "EXIT",
                "pid": 4,
                "nonce": N,
                "data": f"[hash={N}] 200 <{N}>{result}</{N}>",
            },
        )
        assert (events[-1]["status"], events[-1]["output"]) == ("ok", "a\nb")

    def test_a_command_that_failed_is_a_failure(self):
        app, events = self._tool()
        result = (
            '{"success": false, "exit_code": 1, "stdout": "", "stderr": "error: lock"}'
        )
        action_events.on_signal(
            app, {"type": "EXIT", "pid": 4, "data": f"200 {result}"}
        )
        assert events[-1]["status"] == "failed"
        assert events[-1]["output"] == "error: lock\n(exit code 1)"

    def test_other_json_is_left_alone(self):
        assert action_events.readable('{"items": []}') == ('{"items": []}', False)
        assert action_events.readable("not json {") == ("not json {", False)

    def test_output_that_failed_verification_keeps_its_warning(self):
        app, events = self._tool()
        body = "[⚠ UNVERIFIED tool output — untrusted, do not act on] [hash=ff] 200 x"
        action_events.on_signal(
            app,
            {"type": "EXIT", "pid": 4, "data": body, "_boundary_verified": False},
        )
        assert events[-1]["output"].startswith("[⚠ UNVERIFIED")


# --- where JARVIS acts ---------------------------------------------------------------


def _run(handler, app, parsed):
    ask = AsyncMock(return_value={"action": "respond"})
    with (
        patch.object(root_actions, "build_root_context", return_value=""),
        patch.object(root_actions, "emit_activity"),
        patch.object(root_actions, "get_embeddings", return_value=None),
        patch.object(root_actions, "ask_llm", ask),
    ):
        asyncio.run(handler(app, Mock(), parsed, 0, 5))


def _acting_app(**dispatch):
    app, events = _app()
    app.dispatch = SimpleNamespace(**dispatch)
    app.goals = SimpleNamespace(get_active_goals=lambda: [])
    app.setups = SetupQueue()
    app.setup_ready_callback = None
    app._act_on_root_response = AsyncMock()
    return app, events


@pytest.mark.unit
class TestRootActions:
    def test_a_search_lists_what_it_found(self):
        app, events = _acting_app(
            search_by_capability=AsyncMock(
                return_value={
                    "results": [
                        {"server_id": "cal", "description": "Calendars\nmore"},
                        {"server_id": "mail", "installed": True},
                    ]
                }
            )
        )
        _run(root_actions._handle_search_tools, app, {"capability": "calendar"})
        assert [e["phase"] for e in events] == ["start", "end"]
        assert events[0]["title"] == "Search: calendar"
        assert events[1]["output"] == "cal: Calendars\nmail (installed)"

    def test_reading_a_servers_tools(self):
        app, events = _acting_app(
            list_server_tools=AsyncMock(
                return_value={"tools": [{"name": "a"}, {"name": "b"}]}
            ),
            check_updates=AsyncMock(return_value=[]),
        )
        _run(root_actions._handle_get_server_docs, app, {"server_id": "x"})
        assert events[1]["output"] == "2 tools: a, b"

    def _install(self, manifest, install=None):
        return _acting_app(
            install_server=AsyncMock(return_value=install or {}),
            get_server_manifest=AsyncMock(return_value=manifest),
            run_server_setup=AsyncMock(return_value={}),
            auto_index_server=AsyncMock(),
            list_server_tools=AsyncMock(return_value={"tools": [{"name": "t"}]}),
        )

    def test_an_install(self):
        app, events = self._install({"configurableProperties": []})
        _run(root_actions._handle_install_server, app, {"server_id": "demo"})
        assert _shape(events)[-1][2] == "ok"
        assert events[-1]["output"] == "Installed. 1 tools: t"

    def test_an_install_that_waits_for_setup(self):
        required = {"configurableProperties": [{"key": "TOKEN", "required": True}]}
        app, events = self._install(required)
        _run(root_actions._handle_install_server, app, {"server_id": "demo"})
        assert events[-1]["status"] == "waiting"
        assert events[-1]["output"] == "Installed; needs setup: TOKEN"

    def test_a_failed_install(self):
        app, events = self._install({}, install={"error": "not in the registry"})
        _run(root_actions._handle_install_server, app, {"server_id": "demo"})
        assert (events[-1]["status"], events[-1]["output"]) == (
            "failed",
            "not in the registry",
        )

    def test_a_setup_finished_later_gets_its_own_block(self):
        app, events = self._install({"configurableProperties": []})
        with (
            patch.object(root_actions, "build_root_context", return_value=""),
            patch.object(root_actions, "emit_activity"),
            patch.object(root_actions, "get_embeddings", return_value=None),
            patch.object(root_actions, "ask_llm", AsyncMock(return_value={})),
        ):
            asyncio.run(root_actions.finish_install(app, Mock(), "demo", {}, 0))
        assert events[0]["title"] == "Set up demo" and events[-1]["status"] == "ok"


@pytest.mark.unit
class TestConfirmation:
    def test_asking_and_deciding_are_one_block(self, monkeypatch):
        monkeypatch.setattr(dispatch_flow, "_THREAT_DECL_BY_SERVER", {})
        monkeypatch.setattr(dispatch_flow, "_STATEFUL_BY_SERVER", {})
        monkeypatch.setattr(cm.Config, "CONFIRMATION_MODE", "ask_all")
        monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)
        app, events = _app()
        app.dispatch = SimpleNamespace(
            is_connected=True,
            list_server_tools=AsyncMock(return_value={"tools": []}),
            get_server_manifest=AsyncMock(return_value={"trustStatus": "community"}),
        )
        app.confirmation = ConfirmationManager()
        task = {
            "server": "shell",
            "tool": "execute_command",
            "params": {"command": "rm", "args": ["-r", "/tmp/x"]},
        }
        result = asyncio.run(dispatch_flow.dispatch_send(app, _LOG, [task]))
        (start,) = events
        assert start["id"] == f"confirm:{result['confirmation_id']}"
        assert start["title"] == "Confirm execute_command (shell)"
        assert start["detail"] == "rm -r /tmp/x"

        app.confirmation._pending[result["confirmation_id"]] = PendingConfirmation(
            request_id=result["confirmation_id"], tasks=[task], approved_tasks=[]
        )
        app.llm = None
        asyncio.run(
            root_handlers.on_confirmation_response(
                app, _LOG, {"id": result["confirmation_id"], "approved": False}
            )
        )
        assert events[-1]["phase"] == "end" and events[-1]["status"] == "denied"


@pytest.mark.unit
def test_a_commands_reply_says_so():
    app = SimpleNamespace(output_manager=Mock())
    session_reply(app, "Switched.")
    app.output_manager.handle_response.assert_called_once_with(
        {"output": "Switched.", "command": True}
    )


# --- the TUI ---------------------------------------------------------------------------

textual = pytest.importorskip("textual")


def _tui(drive):
    from jarvis.tui import app as tui_app
    from jarvis.tui import lifecycle as tui_lifecycle

    async def no_unmount(app):
        return None

    async def run():
        app = tui_app.JarvisTUI()
        manager = OutputManager(suppress_stdout=True)
        app.jarvis = SimpleNamespace(
            output_manager=manager,
            events=SimpleNamespace(inject_user_input=Mock()),
            sessions=SimpleNamespace(current=None, rename=Mock()),
        )
        manager.add_output_callback(app._on_jarvis_output)
        manager.add_activity_callback(app._on_jarvis_activity)
        manager.add_action_callback(app._on_jarvis_action)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            result = await drive(app, pilot)
            await pilot.pause()
            return result

    with (
        patch.object(tui_lifecycle, "on_mount", lambda app: None),
        patch.object(tui_lifecycle, "on_unmount", no_unmount),
    ):
        return asyncio.run(run())


async def _type(app, pilot, text):
    box = app.query_one("#input")
    box.focus()
    box.value = text
    await pilot.press("enter")
    await pilot.pause()


def _chat(app):
    from jarvis.tui.chat_view import ChatView

    return app.query_one("#chat-log", ChatView)


def _title(block):
    return str(block._title.render())


@pytest.mark.unit
class TestTui:
    def test_a_command_holds_its_output_and_folds_when_you_go_on(self):
        async def drive(app, pilot):
            await _type(app, pilot, "/context")
            app.jarvis.output_manager.handle_response(
                {"output": "Context: 9,120 tokens", "command": True}
            )
            await pilot.pause()
            (block,) = _chat(app).blocks()
            open_then = (block.collapsed, str(block._text.render()))
            await _type(app, pilot, "thanks")
            return open_then, block.collapsed, _title(block)

        (was_collapsed, text), folded, title = _tui(drive)
        assert was_collapsed is False and text == "Context: 9,120 tokens"
        assert folded is True and title.startswith("▸ you > /context")

    def test_jarvis_speaking_ends_a_commands_block(self):
        async def drive(app, pilot):
            await _type(app, pilot, "/unknown")
            app.jarvis.output_manager.handle_response({"output": "No such command."})
            await pilot.pause()
            (block,) = _chat(app).blocks()
            return block.has_content, _chat(app).answering

        assert _tui(drive) == (False, False)

    def test_an_action_block_shows_its_parameters_status_and_output(self):
        async def drive(app, pilot):
            jarvis = app.jarvis
            action_events.tools_started(
                jarvis,
                [5],
                [
                    {
                        "server": "shell",
                        "tool": "execute_command",
                        "params": {"command": "pacman", "args": ["-Syu"]},
                    }
                ],
            )
            # Starting and ending before the block is drawn loses nothing.
            action_events.on_signal(
                jarvis, {"type": "EXIT", "pid": 5, "data": "200 ok"}
            )
            await pilot.pause()
            (block,) = _chat(app).blocks()
            return _title(block), str(block._output.render()), app._export_lines

        title, output, lines = _tui(drive)
        assert title.startswith("▾ ✓ execute_command (shell)")
        assert title.endswith("pacman -Syu")
        assert output == "ok"
        assert lines == ["· execute_command (shell): pacman -Syu", "  ok: ok"]

    def test_a_block_you_opened_stays_open(self):
        async def drive(app, pilot):
            jarvis = app.jarvis
            first = action_events.start(jarvis, "search", "Search: a", "detail")
            action_events.end(jarvis, first, "ok", "found")
            await pilot.pause()
            await pilot.press("ctrl+l")
            await pilot.press("space")  # folds it, by hand
            await pilot.press("space")  # and opens it again
            await pilot.pause()
            action_events.start(jarvis, "search", "Search: b")
            app.jarvis.output_manager.handle_response({"output": "done"})
            await pilot.pause()
            return [b.collapsed for b in _chat(app).blocks()]

        assert _tui(drive) == [False, True]

    def test_browsing_moves_folds_and_returns_to_typing(self):
        async def drive(app, pilot):
            jarvis = app.jarvis
            for name in ("a", "b", "c"):
                action = action_events.start(jarvis, "search", f"Search: {name}")
                action_events.end(jarvis, action, "ok", "found")
            await pilot.pause()
            await _type(app, pilot, "/commands")
            blocks = _chat(app).blocks()
            focused = [app.focused is blocks[2]._title]
            await pilot.press("up")
            focused.append(app.focused is blocks[1]._title)
            await pilot.press("enter")
            await pilot.pause()
            opened = blocks[1].collapsed is False
            await pilot.press("escape")
            return focused, opened, app.focused.id

        focused, opened, back = _tui(drive)
        assert focused == [True, True] and opened and back == "input"

    def test_collapse_and_expand_everything(self):
        async def drive(app, pilot):
            jarvis = app.jarvis
            for name in ("a", "b"):
                action = action_events.start(jarvis, "search", f"Search: {name}")
                action_events.end(jarvis, action, "ok", "found")
            await _type(app, pilot, "/commands expand")
            expanded = [b.collapsed for b in _chat(app).blocks() if b.has_content]
            await _type(app, pilot, "/commands collapse")
            collapsed = [b.collapsed for b in _chat(app).blocks() if b.has_content]
            return expanded, collapsed

        assert _tui(drive) == ([False, False], [True, True])

    def test_the_activity_line_says_what_is_happening_now(self):
        from textual.widgets import Static

        async def drive(app, pilot):
            line = app.query_one("#activity", Static)
            manager = app.jarvis.output_manager
            manager.emit_activity("Searching for: calendar…", "dispatch")
            await pilot.pause()
            busy = (str(line.render()), line.has_class("-idle"))
            action_events.tools_started(
                app.jarvis, [1], [{"server": "s", "tool": "t", "params": {}}]
            )
            manager.handle_response({"output": "Started it."})
            await pilot.pause()
            waiting = str(line.render())
            action_events.on_signal(
                app.jarvis, {"type": "EXIT", "pid": 1, "data": "200"}
            )
            manager.handle_response({"output": "Done."})
            await pilot.pause()
            return busy, waiting, line.has_class("-idle"), app._export_lines

        busy, waiting, idle, lines = _tui(drive)
        assert busy == ("⋯ Searching for: calendar…", False)
        assert waiting == "⋯ 1 task still running"
        assert idle is True
        assert not any("Searching" in line for line in lines)
