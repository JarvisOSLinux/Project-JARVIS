"""#206 — the daemon derives per-task `stateful` from the server manifest.

dispatch only routes a task through dmcp's session broker when the task says
`stateful: true` AND a session_id is present. The manifest already declares
statefulness, so the daemon stamps the flag in dispatch_send — the LLM never
has to remember a transport detail whose omission silently degrades a browser
server to the state-losing one-shot lifecycle (the navigate → about:blank bug).
"""

import asyncio
import logging
from types import SimpleNamespace

from jarvis.runtime import dispatch_flow

_LOG = logging.getLogger("test")


class _FakeConfirmation:
    def __init__(self, confirm=False):
        self.confirm = confirm
        self.requested = None

    def should_confirm(self, tool_meta, tool_name=None, params=None):
        return self.confirm

    async def request_confirmation(self, **kwargs):
        self.requested = kwargs


class _FakeAdapter:
    is_connected = True

    def __init__(self, manifests):
        self.manifests = manifests
        self.lookups = []
        self.sent = None

    async def get_server_manifest(self, server_id):
        self.lookups.append(server_id)
        result = self.manifests.get(server_id, {})
        if isinstance(result, Exception):
            raise result
        return result

    async def send_tasks(self, tasks, session_id=None):
        self.sent = tasks
        return {"output": "ok"}


class _App:
    def __init__(self, adapter, confirmation):
        self.dispatch = adapter
        self.confirmation = confirmation
        self.sessions = SimpleNamespace(current_id=None)


def _run_send(app, tasks):
    return asyncio.run(dispatch_flow.dispatch_send(app, _LOG, tasks))


def _fresh_cache(monkeypatch):
    monkeypatch.setattr(dispatch_flow, "_STATEFUL_BY_SERVER", {})


def _browser_task(**extra):
    task = {
        "server": "io.github.microsoft.playwright-mcp",
        "tool": "browser_navigate",
        "params": {"url": "https://example.org"},
    }
    task.update(extra)
    return task


async def _no_meta(app, logger, task):
    return {}


def test_stateful_manifest_stamps_task(monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(dispatch_flow, "get_tool_metadata", _no_meta)
    adapter = _FakeAdapter({"io.github.microsoft.playwright-mcp": {"stateful": True}})
    app = _App(adapter, _FakeConfirmation())

    _run_send(app, [_browser_task()])

    assert adapter.sent[0]["stateful"] is True


def test_stateless_manifest_leaves_task_untouched(monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(dispatch_flow, "get_tool_metadata", _no_meta)
    adapter = _FakeAdapter({"sys": {"id": "sys", "version": "1.0"}})
    app = _App(adapter, _FakeConfirmation())

    _run_send(app, [{"server": "sys", "tool": "execute_command", "params": {}}])

    assert "stateful" not in adapter.sent[0]


def test_manifest_read_cached_across_dispatches(monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(dispatch_flow, "get_tool_metadata", _no_meta)
    adapter = _FakeAdapter({"io.github.microsoft.playwright-mcp": {"stateful": True}})
    app = _App(adapter, _FakeConfirmation())

    _run_send(app, [_browser_task()])
    _run_send(app, [_browser_task(), _browser_task(tool="browser_snapshot")])

    assert adapter.lookups == ["io.github.microsoft.playwright-mcp"]
    assert all(t["stateful"] is True for t in adapter.sent)


def test_failed_lookup_is_not_cached_and_does_not_crash(monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(dispatch_flow, "get_tool_metadata", _no_meta)
    adapter = _FakeAdapter({"flaky": RuntimeError("dmcp unreachable")})
    app = _App(adapter, _FakeConfirmation())

    _run_send(app, [{"server": "flaky", "tool": "t", "params": {}}])
    assert "stateful" not in adapter.sent[0]

    # Server becomes readable (e.g. installed mid-session): retried, then stamped.
    adapter.manifests["flaky"] = {"stateful": True}
    _run_send(app, [{"server": "flaky", "tool": "t", "params": {}}])
    assert adapter.lookups == ["flaky", "flaky"]
    assert adapter.sent[0]["stateful"] is True


def test_empty_manifest_is_not_cached(monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(dispatch_flow, "get_tool_metadata", _no_meta)
    adapter = _FakeAdapter({"ghost": {}})
    app = _App(adapter, _FakeConfirmation())

    _run_send(app, [{"server": "ghost", "tool": "t", "params": {}}])
    _run_send(app, [{"server": "ghost", "tool": "t", "params": {}}])

    assert adapter.lookups == ["ghost", "ghost"]
    assert "stateful" not in adapter.sent[0]


def test_explicit_stateful_false_is_respected(monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(dispatch_flow, "get_tool_metadata", _no_meta)
    adapter = _FakeAdapter({"io.github.microsoft.playwright-mcp": {"stateful": True}})
    app = _App(adapter, _FakeConfirmation())

    _run_send(app, [_browser_task(stateful=False)])

    # An explicit LLM choice wins; no manifest lookup for an already-set flag.
    assert adapter.sent[0]["stateful"] is False
    assert adapter.lookups == []


def test_stamp_rides_through_confirmation_gate(monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(dispatch_flow, "get_tool_metadata", _no_meta)
    monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)
    adapter = _FakeAdapter({"io.github.microsoft.playwright-mcp": {"stateful": True}})
    confirmation = _FakeConfirmation(confirm=True)
    app = _App(adapter, confirmation)

    result = _run_send(app, [_browser_task()])

    assert result.get("awaiting_confirmation")
    gated = confirmation.requested["tools_needing_confirmation"][0]["task"]
    assert gated["stateful"] is True
