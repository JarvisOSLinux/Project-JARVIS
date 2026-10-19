"""A FORBIDDEN tool never runs, in any CONFIRMATION_MODE (mcp-registry#88, #250).

The block sits in ``dispatch_send`` ahead of ``should_confirm``: ``allow_all``
returns False from that gate, so a refusal inside it could not hold.
"""

import asyncio
import logging

import pytest

import jarvis.core.confirmation_manager as cm
from jarvis.core.confirmation_manager import ConfirmationManager
from jarvis.runtime import dispatch_flow

_LOG = logging.getLogger("test")

_TOOLS = [
    {"name": "read_item", "description": "read", "inputSchema": {}},
    {"name": "wire_money", "description": "irreversible", "inputSchema": {}},
    {"name": "delete_item", "description": "delete one", "inputSchema": {}},
]
_MANIFEST = {
    "id": "bank",
    "trustStatus": "community",
    "tools": [
        {"name": "read_item", "threat_level": "safe"},
        {"name": "wire_money", "threat_level": "forbidden"},
        {"name": "delete_item", "threat_level": "dangerous"},
    ],
}


class _Adapter:
    is_connected = True

    def __init__(self):
        self.sent = None

    async def list_server_tools(self, server_id):
        return {"server": server_id, "tools": _TOOLS}

    async def get_server_manifest(self, server_id):
        return _MANIFEST

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
    def __init__(self):
        self.dispatch = _Adapter()
        self.confirmation = _Confirmation()


def _send(monkeypatch, mode, *tools):
    monkeypatch.setattr(dispatch_flow, "_THREAT_DECL_BY_SERVER", {})
    monkeypatch.setattr(dispatch_flow, "_STATEFUL_BY_SERVER", {})
    monkeypatch.setattr(cm.Config, "CONFIRMATION_MODE", mode)
    monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)
    app = _App()
    tasks = [{"server": "bank", "tool": t, "params": {}} for t in tools]
    result = asyncio.run(dispatch_flow.dispatch_send(app, _LOG, tasks))
    return app, result


@pytest.mark.unit
class TestForbiddenBlock:
    @pytest.mark.parametrize("mode", ["smart", "ask_all", "allow_all"])
    def test_refused_in_every_mode(self, monkeypatch, mode):
        app, result = _send(monkeypatch, mode, "wire_money")
        assert "FORBIDDEN" in result["error"]
        assert "bank.wire_money" in result["error"]
        assert app.dispatch.sent is None
        assert app.confirmation.requested is False

    def test_one_forbidden_task_refuses_the_whole_batch(self, monkeypatch):
        app, result = _send(monkeypatch, "allow_all", "read_item", "wire_money")
        assert "error" in result
        assert app.dispatch.sent is None

    def test_dangerous_is_still_confirmed_not_refused(self, monkeypatch):
        app, result = _send(monkeypatch, "smart", "delete_item")
        assert app.confirmation.requested is True
        assert "FORBIDDEN" not in str(result)

    def test_safe_still_runs(self, monkeypatch):
        app, _ = _send(monkeypatch, "smart", "read_item")
        assert app.dispatch.sent is not None

    def test_dangerous_still_runs_unprompted_in_allow_all(self, monkeypatch):
        app, _ = _send(monkeypatch, "allow_all", "delete_item")
        assert app.dispatch.sent is not None
