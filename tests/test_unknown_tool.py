"""A tool the server does not list is refused before anyone is asked about it.

Session 43940ebc (2026-11-20): the model sent `status` to the root shell
server, which has no such tool, five times. Each went to the confirmation gate
as an undeclared tool, and each approved one cost a polkit password prompt
before the server answered "Unknown tool: status". The model then told the user
the calendar failed for want of a password.
"""

import asyncio
import logging

import pytest

import jarvis.core.confirmation_manager as cm
from jarvis.core.confirmation_manager import ConfirmationManager
from jarvis.runtime import dispatch_flow

_LOG = logging.getLogger("test")

_SHELL = "com.github.yakupatahanov.jarvis-shell-system-mcp"
_CALDAV = "com.github.yakupatahanov.mcp.caldav"

_LISTINGS = {
    _SHELL: {
        "tools": [
            {"name": n, "description": n, "inputSchema": {}}
            for n in (
                "execute_command",
                "execute_script",
                "run_job",
                "send_input",
                "read_output",
                "kill_job",
            )
        ]
    },
    _CALDAV: {
        "tools": [
            {"name": n, "description": n, "inputSchema": {}}
            for n in ("list_calendars", "list_events")
        ]
    },
}
_MANIFESTS = {
    _SHELL: {
        "id": _SHELL,
        "scope": "system",
        "trustStatus": "community",
        "tools": [{"name": "execute_command", "threat_level": "dangerous"}],
    },
    _CALDAV: {
        "id": _CALDAV,
        "trustStatus": "community",
        "tools": [
            {"name": "list_calendars", "threat_level": "elevated"},
            {"name": "list_events", "threat_level": "elevated"},
        ],
    },
}


class _Adapter:
    is_connected = True

    def __init__(self, listings):
        self.listings = listings
        self.sent = None

    async def list_server_tools(self, server_id):
        return self.listings[server_id]

    async def get_server_manifest(self, server_id):
        return _MANIFESTS[server_id]

    async def send_tasks(self, tasks, session_id=None):
        self.sent = tasks
        return {"output": "ok"}


class _Confirmation(ConfirmationManager):
    def __init__(self):
        super().__init__()
        self.requested = False

    async def request_confirmation(self, *args, **kwargs):
        self.requested = True


class _App:
    def __init__(self, listings):
        self.dispatch = _Adapter(listings)
        self.confirmation = _Confirmation()


def _task(server, tool):
    return {"server": server, "tool": tool, "params": {}}


@pytest.fixture
def send(monkeypatch):
    monkeypatch.setattr(dispatch_flow, "_THREAT_DECL_BY_SERVER", {})
    monkeypatch.setattr(dispatch_flow, "_STATEFUL_BY_SERVER", {})
    monkeypatch.setattr(dispatch_flow, "_ROOT_BY_SERVER", {})
    monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)

    def run(*tasks, mode="smart", listings=_LISTINGS):
        monkeypatch.setattr(cm.Config, "CONFIRMATION_MODE", mode)
        app = _App(listings)
        result = asyncio.run(dispatch_flow.dispatch_send(app, _LOG, list(tasks)))
        return app, result

    return run


@pytest.mark.unit
class TestUnknownTool:
    @pytest.mark.parametrize("mode", ["smart", "ask_all", "allow_all"])
    def test_refused_before_confirmation_in_every_mode(self, send, mode):
        app, result = send(_task(_SHELL, "uptime_report"), mode=mode)
        assert f"{_SHELL} has no tool named 'uptime_report'" in result["error"]
        assert app.confirmation.requested is False
        assert app.dispatch.sent is None

    def test_the_refusal_lists_the_servers_tools(self, send):
        _, result = send(_task(_SHELL, "uptime_report"))
        assert (
            "execute_command, execute_script, run_job, send_input, read_output, "
            "kill_job" in result["error"]
        )
        assert "your own actions" not in result["error"]

    def test_the_sessions_batch_is_refused_whole(self, send):
        app, result = send(
            _task(_CALDAV, "list_calendars"),
            _task(_CALDAV, "list_events"),
            _task(_SHELL, "status"),
        )
        assert "has no tool named 'status'" in result["error"]
        assert app.confirmation.requested is False
        assert app.dispatch.sent is None

    def test_an_action_name_is_pointed_at_the_action(self, send):
        _, result = send(_task(_SHELL, "status"))
        assert "'status' is one of your own actions" in result["error"]
        assert '{"action": "status"}' in result["error"]

    def test_a_listed_tool_still_goes_to_confirmation(self, send):
        app, result = send(_task(_CALDAV, "list_calendars"))
        assert app.confirmation.requested is True
        assert "error" not in result

    def test_a_listing_that_failed_refuses_nothing(self, send):
        failed = dict(_LISTINGS)
        failed[_CALDAV] = {"error": "CALDAV_BASE_URL is not set", "tools": []}
        app, result = send(_task(_CALDAV, "list_calendars"), listings=failed)
        assert app.confirmation.requested is True
        assert "error" not in result

    def test_an_error_beside_a_partial_listing_refuses_nothing(self, send):
        partial = dict(_LISTINGS)
        partial[_CALDAV] = {
            "error": "timed out",
            "tools": [{"name": "list_events"}],
        }
        app, _ = send(_task(_CALDAV, "list_calendars"), listings=partial)
        assert app.confirmation.requested is True

    def test_an_empty_listing_refuses_nothing(self, send):
        empty = dict(_LISTINGS)
        empty[_CALDAV] = {"tools": []}
        app, _ = send(_task(_CALDAV, "list_calendars"), listings=empty)
        assert app.confirmation.requested is True

    def test_the_tool_list_stays_out_of_the_metadata(self, monkeypatch):
        monkeypatch.setattr(dispatch_flow, "_THREAT_DECL_BY_SERVER", {})
        app = _App(_LISTINGS)
        meta = asyncio.run(
            dispatch_flow.get_tool_metadata(app, _LOG, _task(_CALDAV, "list_events"))
        )
        assert meta["server_tools"] == ["list_calendars", "list_events"]
        refusal, metas = asyncio.run(
            dispatch_flow._refuse_forbidden(app, _LOG, [_task(_CALDAV, "list_events")])
        )
        assert refusal == ""
        assert "server_tools" not in metas[0]

    def test_an_approved_confirmation_is_refused_too(self, monkeypatch):
        # One restored from confirmations.json, asked for before this check.
        monkeypatch.setattr(dispatch_flow, "_THREAT_DECL_BY_SERVER", {})
        monkeypatch.setattr(dispatch_flow, "_ROOT_BY_SERVER", {})
        monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)
        refused = asyncio.run(
            dispatch_flow.refuse_approved(
                _App(_LISTINGS), _LOG, [_task(_SHELL, "status")]
            )
        )
        assert "has no tool named 'status'" in refused
