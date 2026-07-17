"""
Tests for the `jarvis confirmations` CLI (#192): age formatting, request
building (whole-batch vs per-index approve), and goal/session enrichment.
"""

import json
from unittest.mock import MagicMock

import pytest

import jarvis.cli as cli
from jarvis.runtime.io import enrich_pending_with_goals


class TestFormatAge:
    def test_seconds(self):
        assert cli._format_age(__import__("time").time() - 40) == "40s ago"

    def test_minutes(self):
        assert cli._format_age(__import__("time").time() - 300) == "5m ago"

    def test_hours(self):
        assert cli._format_age(__import__("time").time() - 7200) == "2h ago"


class TestCmdConfirmationsRequestBuilding:
    """Verify the JSON request sent over the socket for each subcommand."""

    def _run(self, monkeypatch, argv, ack=True):
        monkeypatch.setattr(cli.sys, "argv", argv)
        monkeypatch.setattr(cli, "_find_ipc_endpoint", lambda: "/fake/sock")

        sent = {}
        sock = MagicMock()

        def fake_sendall(data):
            sent["request"] = json.loads(data.decode("utf-8"))

        sock.sendall.side_effect = fake_sendall
        response = {"type": "ack", "message": "ok"} if ack else {"type": "ack"}
        sock.makefile.return_value.readline.return_value = json.dumps(response)

        import jarvis.platform as platform_pkg

        monkeypatch.setattr(platform_pkg.current, "ipc_connect", lambda path: sock)

        cli._cmd_confirmations()
        return sent["request"]

    def test_approve_whole_batch(self, monkeypatch):
        req = self._run(monkeypatch, ["jarvis", "confirmations", "approve", "abc123"])
        assert req == {"type": "approve_confirmation", "id": "abc123"}

    def test_deny_whole_batch(self, monkeypatch):
        req = self._run(monkeypatch, ["jarvis", "confirmations", "deny", "abc123"])
        assert req == {"type": "deny_confirmation", "id": "abc123"}

    def test_approve_all(self, monkeypatch):
        req = self._run(monkeypatch, ["jarvis", "confirmations", "approve-all"])
        assert req == {"type": "approve_all_confirmations"}

    def test_approve_partial_indices(self, monkeypatch):
        req = self._run(
            monkeypatch, ["jarvis", "confirmations", "approve", "abc123", "0,2"]
        )
        assert req == {
            "type": "partial_approve_confirmation",
            "id": "abc123",
            "approved_indices": [0, 2],
        }

    def test_deny_with_indices_rejected(self, monkeypatch):
        monkeypatch.setattr(
            cli.sys, "argv", ["jarvis", "confirmations", "deny", "abc123", "0,2"]
        )
        with pytest.raises(SystemExit):
            cli._cmd_confirmations()

    def test_invalid_index_list_rejected(self, monkeypatch):
        monkeypatch.setattr(
            cli.sys, "argv", ["jarvis", "confirmations", "approve", "abc123", "x,y"]
        )
        with pytest.raises(SystemExit):
            cli._cmd_confirmations()


class TestEnrichPendingWithGoals:
    def test_resolves_goal_description_from_session_id(self):
        app = MagicMock()
        app.confirmation.list_pending.return_value = [
            {"id": "r1", "session_id": "g1", "created_at": 0.0},
        ]
        goal = MagicMock()
        goal.description = "check python and update system"
        app.goals.get_goal.return_value = goal

        result = enrich_pending_with_goals(app)

        assert result[0]["goal_description"] == "check python and update system"
        app.goals.get_goal.assert_called_once_with("g1")

    def test_no_session_id_gives_none_description(self):
        app = MagicMock()
        app.confirmation.list_pending.return_value = [
            {"id": "r1", "session_id": None, "created_at": 0.0},
        ]

        result = enrich_pending_with_goals(app)

        assert result[0]["goal_description"] is None
