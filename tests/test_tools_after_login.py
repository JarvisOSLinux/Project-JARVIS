"""After a server's `login` succeeds, the model is shown the server's real tools.

A wrapper signed in on behalf of a hosted server (Notion) can only list
login/status/logout before the sign-in. The model learned that list at install
time and, once signed in, told the user the server "only provides
authentication tools". The manifest names the sign-in tool, so its successful
EXIT is when JARVIS fetches the list again.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.runtime import dispatch_flow, llm_bridge, root_handlers

SERVER = "com.example.mcp.notion"
MANIFEST = {"login": {"tool": "login"}, "configurableProperties": []}
FULL = {
    "tools": [
        {"name": "login", "description": "Sign in"},
        {"name": "notion-search", "description": "Search Notion"},
    ]
}


def _app():
    return SimpleNamespace(
        dispatch=SimpleNamespace(
            get_server_manifest=AsyncMock(return_value=MANIFEST),
            list_server_tools=AsyncMock(return_value=FULL),
        )
    )


def _exit(pid, ok=True):
    status = "200" if ok else "500"
    return {"type": "EXIT", "pid": pid, "data": f"[hash=h] {status} <h>done</h>"}


@pytest.mark.unit
class TestNoteDispatchedTools:
    def test_pairs_the_trailing_pids_with_the_tasks(self):
        app = SimpleNamespace()
        tasks = [{"server": SERVER, "tool": "login"}, {"server": "s2", "tool": "t"}]
        dispatch_flow.note_dispatched_tools(app, [1, 7, 8], tasks)
        assert app._pid_tools == {7: (SERVER, "login"), 8: ("s2", "t")}

    def test_an_ambiguous_pairing_records_nothing(self):
        app = SimpleNamespace()
        dispatch_flow.note_dispatched_tools(
            app, [7], [{"server": SERVER, "tool": "login"}] * 2
        )
        assert getattr(app, "_pid_tools", {}) == {}


@pytest.mark.unit
class TestToolsAfterLogin:
    def _run(self, app, signal):
        return asyncio.run(root_handlers._tools_after_login(app, Mock(), signal))

    def test_a_successful_login_shows_the_full_list(self):
        app = _app()
        app._pid_tools = {7: (SERVER, "login")}
        text = self._run(app, _exit(7))
        assert "TOOLS_AFTER_SIGN_IN" in text and "notion-search" in text
        app.dispatch.list_server_tools.assert_awaited_once_with(SERVER)
        assert 7 not in app._pid_tools

    @pytest.mark.parametrize(
        "pid_tools, signal",
        [
            ({7: (SERVER, "login")}, _exit(7, ok=False)),  # the sign-in failed
            ({7: (SERVER, "notion-search")}, _exit(7)),  # not the login tool
            (
                {7: (SERVER, "login")},
                {"type": "REMIND", "pid": 7, "data": "still waiting"},
            ),
            ({}, _exit(7)),  # a PID dispatched elsewhere
        ],
    )
    def test_anything_else_adds_nothing(self, pid_tools, signal):
        app = _app()
        app._pid_tools = dict(pid_tools)
        assert self._run(app, signal) == ""
        app.dispatch.list_server_tools.assert_not_awaited()


@pytest.mark.unit
def test_the_model_sees_the_tools_on_the_login_exit():
    app = _app()
    app._pid_tools = {7: (SERVER, "login")}
    app.llm = Mock(mode="root", ask=Mock(return_value={"action": "respond"}))
    app.llm_lock = asyncio.Lock()
    app.goals = SimpleNamespace(
        update_from_signal=Mock(), find_goal_by_task_pid=Mock(return_value=None)
    )
    app._act_on_root_response = AsyncMock()

    with (
        patch.object(root_handlers, "build_root_context", return_value=""),
        patch.object(
            root_handlers, "_config_hint_for_signals", AsyncMock(return_value="")
        ),
        patch.object(root_handlers, "emit_activity"),
        patch.object(llm_bridge, "emit_activity"),
    ):
        asyncio.run(root_handlers.on_dispatch_signal(app, Mock(), _exit(7)))

    told = app.llm.ask.call_args.args[0]
    assert "TOOLS_AFTER_SIGN_IN" in told and "notion-search" in told


INIT = "Signal window (last 1):\n[10:00:00] PID 7 INIT notion/login {}\n"


@pytest.mark.unit
def test_the_direct_dispatch_path_records_the_tool(monkeypatch):
    app = SimpleNamespace(
        dispatch=SimpleNamespace(is_connected=True),
        goals=SimpleNamespace(get_active_goals=lambda: []),
    )
    monkeypatch.setattr(
        dispatch_flow, "dispatch_send", AsyncMock(return_value={"output": INIT})
    )
    monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)
    task = {"server": SERVER, "tool": "login", "params": {}}
    asyncio.run(dispatch_flow.dispatch_execute_tasks(app, Mock(), [task], depth=0))
    assert app._pid_tools == {7: (SERVER, "login")}


@pytest.mark.unit
def test_a_confirmed_login_is_recorded_too(tmp_path, monkeypatch):
    # login is elevated, so in smart mode it always goes through a confirmation.
    from jarvis.core.confirmation_manager import (
        ConfirmationManager,
        PendingConfirmation,
    )
    from jarvis.dispatch.goal_manager import GoalManager

    task = {"server": SERVER, "tool": "login", "params": {}}
    conf = ConfirmationManager()
    conf._pending["r1"] = PendingConfirmation(
        request_id="r1", tasks=[task], approved_tasks=[], session_id=None
    )
    app = SimpleNamespace(
        confirmation=conf,
        goals=GoalManager(archive_dir=str(tmp_path)),
        dispatch=SimpleNamespace(send_tasks=AsyncMock(return_value={"output": INIT})),
        llm=object(),
        _gui_clients=None,
        _act_on_root_response=AsyncMock(),
    )
    monkeypatch.setattr(root_handlers, "build_root_context", lambda a, l, **k: "")
    monkeypatch.setattr(root_handlers, "emit_activity", lambda *a, **k: None)
    monkeypatch.setattr(
        root_handlers, "ask_llm", AsyncMock(return_value={"action": "respond"})
    )
    asyncio.run(
        root_handlers.on_confirmation_response(
            app, Mock(), {"id": "r1", "approved": True}
        )
    )
    assert app._pid_tools == {7: (SERVER, "login")}
