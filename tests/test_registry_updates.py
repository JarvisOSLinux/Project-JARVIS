"""#39 — the daemon can heal a stale MCP server and refuses a revoked one.

dmcp gained `update` / `update --check --json`; until now the daemon installed
servers and never looked at them again, so a drifted server stayed broken and a
server whose registry trust was withdrawn kept being dispatched to. These tests
cover the whole loop — the dmcp wrappers, the ROOT action, the drift marker on
SERVER_DOCS, the periodic sweep, and the deterministic dispatch refusal.

Nothing here runs a real dmcp binary: run_dmcp and the adapter methods are
monkeypatched, so the suite stays offline and dmcp-free.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.config import Config
from jarvis.core.command_parser import TaskParser
from jarvis.dispatch import dmcp_registry
from jarvis.runtime import dispatch_flow, lifecycle
from jarvis.runtime.root_actions import _handle_get_server_docs, _handle_update_server

_LOG = logging.getLogger("test")


class _RecordingLogger:
    """Minimal logger stand-in that keeps what the sweep said."""

    def __init__(self):
        self.warnings = []
        self.debugs = []

    def warning(self, message, *args, **kwargs):
        self.warnings.append(message)

    def debug(self, message, *args, **kwargs):
        self.debugs.append(message)

    def info(self, message, *args, **kwargs):
        pass


def _fake_run_dmcp(responses, calls):
    """run_dmcp replacement returning canned stdout keyed by the subcommand."""

    async def _run(logger, *args):
        calls.append(args)
        return responses.get(args[0])

    return _run


def _root_app(**dispatch_attrs):
    app = MagicMock()
    app.goals.get_context = MagicMock(return_value=[])
    app.sessions.load_summary = MagicMock(return_value=None)
    app.contextor = None
    app.mcp_dispatch_docs = {}
    app._act_on_root_response = AsyncMock()
    for name, value in dispatch_attrs.items():
        setattr(app.dispatch, name, value)
    return app


def _capture_llm(monkeypatch, captured):
    async def fake_ask_llm(app, logger, context, tag=None, mode=None):
        captured["context"] = context
        return {"action": "respond", "output": "ok"}

    monkeypatch.setattr("jarvis.runtime.root_actions.ask_llm", fake_ask_llm)


# ----------------------------------------------------------------------
# dmcp wrappers
# ----------------------------------------------------------------------


class TestUpdateWrappers:
    def test_update_server_reports_success_like_install(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            dmcp_registry, "run_dmcp", _fake_run_dmcp({"update": "updated 1\n"}, calls)
        )

        result = asyncio.run(dmcp_registry.update_server(_LOG, "com.example.mcp"))

        assert result == {"updated": "com.example.mcp", "output": "updated 1"}
        assert calls == [("update", "com.example.mcp")]

    def test_update_server_failure_is_an_error_dict(self, monkeypatch):
        monkeypatch.setattr(dmcp_registry, "run_dmcp", _fake_run_dmcp({}, []))

        result = asyncio.run(dmcp_registry.update_server(_LOG, "com.example.mcp"))

        assert "error" in result
        assert "com.example.mcp" in result["error"]

    def test_check_updates_for_one_server_parses_the_array(self, monkeypatch):
        calls = []
        payload = (
            '[{"id": "com.example.mcp", "installed_hash": "aa", '
            '"registry_hash": "bb", "trust_status": "community", '
            '"update_available": true}]'
        )
        monkeypatch.setattr(
            dmcp_registry, "run_dmcp", _fake_run_dmcp({"update": payload}, calls)
        )

        rows = asyncio.run(dmcp_registry.check_updates(_LOG, "com.example.mcp"))

        assert calls == [("update", "--check", "--json", "com.example.mcp")]
        assert rows[0]["update_available"] is True
        assert rows[0]["registry_hash"] == "bb"

    def test_check_updates_without_id_asks_for_all(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            dmcp_registry, "run_dmcp", _fake_run_dmcp({"update": "[]"}, calls)
        )

        assert asyncio.run(dmcp_registry.check_updates(_LOG)) == []
        assert calls == [("update", "--check", "--all", "--json")]

    @pytest.mark.parametrize(
        "payload",
        [None, "", "not json at all", '{"id": "x"}', "[1, 2, 3]"],
    )
    def test_check_updates_tolerates_garbage(self, monkeypatch, payload):
        monkeypatch.setattr(
            dmcp_registry, "run_dmcp", _fake_run_dmcp({"update": payload}, [])
        )

        assert asyncio.run(dmcp_registry.check_updates(_LOG)) == []


# ----------------------------------------------------------------------
# ROOT update_server action
# ----------------------------------------------------------------------


class TestUpdateServerAction:
    def test_parses_like_install_server(self):
        assert TaskParser.parse(
            {"action": "update_server", "server_id": "com.example.mcp"}
        ) == {
            "action": "update_server",
            "server_id": "com.example.mcp",
            "goal_updates": [],
        }

    def test_requires_a_server_id(self):
        result = TaskParser.parse({"action": "update_server"})
        assert "error" in result

    def test_handler_feeds_success_summary(self, monkeypatch):
        app = _root_app()

        async def fake_update(server_id):
            return {"updated": server_id, "output": "ok"}

        app.dispatch.update_server = fake_update
        captured = {}
        _capture_llm(monkeypatch, captured)

        asyncio.run(
            _handle_update_server(app, _LOG, {"server_id": "com.example.mcp"}, 0, 5)
        )

        assert "UPDATE_RESULT: com.example.mcp updated" in captured["context"]
        assert "get_server_docs" in captured["context"]
        app._act_on_root_response.assert_awaited_once()

    def test_handler_feeds_the_error_text(self, monkeypatch):
        app = _root_app()

        async def fake_update(server_id):
            return {"error": "registry unreachable"}

        app.dispatch.update_server = fake_update
        captured = {}
        _capture_llm(monkeypatch, captured)

        asyncio.run(
            _handle_update_server(app, _LOG, {"server_id": "com.example.mcp"}, 0, 5)
        )

        assert "UPDATE_ERROR: registry unreachable" in captured["context"]


# ----------------------------------------------------------------------
# Drift at the point of use — get_server_docs
# ----------------------------------------------------------------------


class TestServerDocsMarker:
    def _docs_app(self, rows):
        app = _root_app()

        async def fake_tools(server_id):
            return {"server": server_id, "tools": [{"name": "run", "description": "d"}]}

        async def fake_check(server_id=None):
            if isinstance(rows, Exception):
                raise rows
            return rows

        app.dispatch.list_server_tools = fake_tools
        app.dispatch.check_updates = fake_check
        return app

    def _run_docs(self, app, monkeypatch):
        captured = {}
        _capture_llm(monkeypatch, captured)
        asyncio.run(
            _handle_get_server_docs(app, _LOG, {"server_id": "com.example.mcp"}, 0, 5)
        )
        return captured["context"]

    def test_drifted_server_gets_a_note(self, monkeypatch):
        app = self._docs_app(
            [
                {
                    "id": "com.example.mcp",
                    "update_available": True,
                    "trust_status": "community",
                }
            ]
        )

        context = self._run_docs(app, monkeypatch)

        assert "NOTE: update available for this server" in context
        assert "update_server and retry" in context
        assert "REVOKED" not in context

    def test_revoked_server_gets_a_warning(self, monkeypatch):
        app = self._docs_app(
            [
                {
                    "id": "com.example.mcp",
                    "update_available": False,
                    "trust_status": "removed",
                }
            ]
        )

        context = self._run_docs(app, monkeypatch)

        assert "WARNING: this server was REVOKED by the registry" in context
        assert "NOTE: update available" not in context

    def test_clean_server_is_unmarked(self, monkeypatch):
        app = self._docs_app(
            [
                {
                    "id": "com.example.mcp",
                    "update_available": False,
                    "trust_status": "official",
                }
            ]
        )

        context = self._run_docs(app, monkeypatch)

        assert "REVOKED" not in context
        assert "update available" not in context
        assert "SERVER_DOCS: com.example.mcp" in context

    def test_failed_check_never_breaks_the_docs(self, monkeypatch):
        app = self._docs_app(RuntimeError("dmcp missing"))

        context = self._run_docs(app, monkeypatch)

        assert "SERVER_DOCS: com.example.mcp" in context
        assert "REVOKED" not in context
        assert "update available" not in context


# ----------------------------------------------------------------------
# Periodic sweep
# ----------------------------------------------------------------------


class _SweepDispatch:
    def __init__(self, rows):
        self.rows = rows
        self.update_cache = {}
        self.calls = 0

    async def check_updates(self, server_id=None):
        self.calls += 1
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


class TestUpdateSweep:
    def _app(self, rows):
        app = MagicMock()
        app.dispatch = _SweepDispatch(rows)
        return app

    def test_sweep_caches_rows_and_warns_once_per_change(self):
        app = self._app(
            [
                {"id": "gone", "trust_status": "removed", "update_available": False},
                {"id": "stale", "trust_status": "community", "update_available": True},
                {"id": "fine", "trust_status": "official", "update_available": False},
            ]
        )
        logger = _RecordingLogger()

        reported = asyncio.run(lifecycle.update_sweep_once(app, logger, set()))

        assert set(app.dispatch.update_cache) == {"gone", "stale", "fine"}
        assert app.dispatch.update_cache["gone"]["trust_status"] == "removed"
        assert len(logger.warnings) == 1
        assert "gone REVOKED" in logger.warnings[0]
        assert "stale update available" in logger.warnings[0]

        # Same state again: cache refreshed, log stays quiet.
        asyncio.run(lifecycle.update_sweep_once(app, logger, reported))
        assert len(logger.warnings) == 1

        # A new revocation is a change, so it is named.
        app.dispatch.rows = app.dispatch.rows + [
            {"id": "also-gone", "trust_status": "removed", "update_available": False}
        ]
        asyncio.run(lifecycle.update_sweep_once(app, logger, reported))
        assert len(logger.warnings) == 2
        assert "also-gone REVOKED" in logger.warnings[1]

    def test_clean_registry_says_nothing(self):
        app = self._app([{"id": "fine", "trust_status": "official"}])
        logger = _RecordingLogger()

        assert asyncio.run(lifecycle.update_sweep_once(app, logger, set())) == set()
        assert logger.warnings == []

    def test_failed_sweep_keeps_the_previous_cache(self):
        app = self._app(RuntimeError("dmcp missing"))
        app.dispatch.update_cache = {"gone": {"trust_status": "removed"}}
        logger = _RecordingLogger()

        reported = asyncio.run(
            lifecycle.update_sweep_once(app, logger, {"gone REVOKED"})
        )

        assert app.dispatch.update_cache == {"gone": {"trust_status": "removed"}}
        assert reported == {"gone REVOKED"}
        assert logger.warnings == []

    def test_zero_interval_disables_the_loop(self, monkeypatch):
        monkeypatch.setattr(Config, "UPDATE_CHECK_INTERVAL_MIN", 0)
        app = self._app([{"id": "gone", "trust_status": "removed"}])

        asyncio.run(lifecycle.run_update_sweep(app, _RecordingLogger()))

        assert app.dispatch.calls == 0

    def test_interval_default_is_present(self):
        assert Config.UPDATE_CHECK_INTERVAL_MIN == 360


# ----------------------------------------------------------------------
# Dispatch-level refusal
# ----------------------------------------------------------------------


class _FakeConfirmation:
    def should_confirm(self, tool_meta, tool_name=None, params=None):
        return False

    async def request_confirmation(self, **kwargs):
        raise AssertionError("no confirmation expected")


class _RefusalDispatch:
    is_connected = True

    def __init__(self, update_cache):
        self.update_cache = update_cache
        self.sent = None

    async def get_server_manifest(self, server_id):
        return {}

    async def send_tasks(self, tasks, session_id=None):
        self.sent = tasks
        return {"output": "ok"}


class TestRevokedDispatchRefusal:
    def _send(self, monkeypatch, update_cache, server="com.example.mcp"):
        async def _no_meta(app, logger, task):
            return {}

        monkeypatch.setattr(dispatch_flow, "get_tool_metadata", _no_meta)
        monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)
        app = MagicMock()
        app.dispatch = _RefusalDispatch(update_cache)
        app.confirmation = _FakeConfirmation()
        tasks = [{"server": server, "tool": "run", "params": {}}]
        result = asyncio.run(dispatch_flow.dispatch_send(app, _LOG, tasks))
        return app, result

    def test_revoked_server_is_refused_and_named(self, monkeypatch):
        app, result = self._send(
            monkeypatch, {"com.example.mcp": {"trust_status": "removed"}}
        )

        assert app.dispatch.sent is None
        assert "com.example.mcp" in result["error"]
        assert "REVOKED" in result["error"]
        assert "uninstall_server" in result["error"]

    def test_drifted_server_still_dispatches(self, monkeypatch):
        app, result = self._send(
            monkeypatch,
            {
                "com.example.mcp": {
                    "trust_status": "community",
                    "update_available": True,
                }
            },
        )

        assert app.dispatch.sent is not None
        assert "error" not in result

    def test_unknown_server_is_unaffected(self, monkeypatch):
        app, result = self._send(
            monkeypatch, {"other.server": {"trust_status": "removed"}}
        )

        assert app.dispatch.sent is not None

    def test_empty_cache_is_unaffected(self, monkeypatch):
        app, result = self._send(monkeypatch, {})

        assert app.dispatch.sent is not None


# ----------------------------------------------------------------------
# Search-result annotation from the sweep cache
# ----------------------------------------------------------------------


class TestSearchAnnotation:
    def _search(self, monkeypatch, update_cache):
        browse = (
            '[{"id": "stale.mcp", "name": "Stale", "summary": "does things", '
            '"installed": true}, '
            '{"id": "gone.mcp", "name": "Gone", "summary": "did things", '
            '"installed": true}, '
            '{"id": "fine.mcp", "name": "Fine", "summary": "still fine", '
            '"installed": false}]'
        )
        monkeypatch.setattr(
            dmcp_registry,
            "run_dmcp",
            _fake_run_dmcp({"browse": browse, "list": "[]"}, []),
        )
        result = asyncio.run(
            dmcp_registry.search_servers(_LOG, ["things"], update_cache)
        )
        return {s["id"]: s for s in result["servers"]}

    def test_cache_marks_drifted_and_revoked(self, monkeypatch):
        servers = self._search(
            monkeypatch,
            {
                "stale.mcp": {"trust_status": "community", "update_available": True},
                "gone.mcp": {"trust_status": "removed", "update_available": False},
            },
        )

        assert servers["stale.mcp"]["summary"] == "does things [update available]"
        assert servers["gone.mcp"]["summary"] == "[REVOKED — do not use] did things"
        assert servers["fine.mcp"]["summary"] == "still fine"

    def test_revoked_server_stays_visible(self, monkeypatch):
        servers = self._search(monkeypatch, {"gone.mcp": {"trust_status": "removed"}})

        assert "gone.mcp" in servers

    def test_no_cache_leaves_summaries_alone(self, monkeypatch):
        servers = self._search(monkeypatch, None)

        assert servers["stale.mcp"]["summary"] == "does things"
