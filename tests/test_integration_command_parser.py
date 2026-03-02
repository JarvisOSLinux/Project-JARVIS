"""
TaskParser integration tests for JARVIS AI Assistant.

Tests the TaskParser which validates and parses LLM responses
into structured dispatch actions (dispatch, respond, wait, kill, defer).
"""

import pytest
from jarvis.core.command_parser import TaskParser


@pytest.mark.integration
class TestRespondActionParsing:
    """Test parsing of respond actions."""

    def test_parse_respond_action(self):
        parser = TaskParser()
        response = {"action": "respond", "output": "Hello!"}
        result = parser.parse(response)

        assert result["action"] == "respond"
        assert result["output"] == "Hello!"
        assert result["goal_updates"] == []

    def test_parse_respond_with_goal_updates(self):
        parser = TaskParser()
        response = {
            "action": "respond",
            "output": "Task done.",
            "goal_updates": [{"id": "g1", "status": "completed", "result": "done"}],
        }
        result = parser.parse(response)

        assert result["action"] == "respond"
        assert result["output"] == "Task done."
        assert len(result["goal_updates"]) == 1
        assert result["goal_updates"][0]["id"] == "g1"

    def test_parse_respond_empty_output(self):
        parser = TaskParser()
        response = {"action": "respond"}
        result = parser.parse(response)

        assert result["action"] == "respond"
        assert result["output"] == ""


@pytest.mark.integration
class TestDispatchActionParsing:
    """Test parsing of dispatch actions."""

    def test_parse_dispatch_single_task(self):
        parser = TaskParser()
        response = {
            "action": "dispatch",
            "tasks": [
                {"server": "ShellMCP", "tool": "execute_command", "params": {"command": "ls"}},
            ],
        }
        result = parser.parse(response)

        assert result["action"] == "dispatch"
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["server"] == "ShellMCP"
        assert result["tasks"][0]["tool"] == "execute_command"
        assert result["tasks"][0]["params"] == {"command": "ls"}

    def test_parse_dispatch_multiple_tasks(self):
        parser = TaskParser()
        response = {
            "action": "dispatch",
            "tasks": [
                {"server": "ShellMCP", "tool": "run", "params": {"cmd": "ls"}},
                {"server": "EchoMCP", "tool": "echo", "params": {"msg": "hi"}},
            ],
        }
        result = parser.parse(response)

        assert result["action"] == "dispatch"
        assert len(result["tasks"]) == 2

    def test_parse_dispatch_with_remind_after(self):
        parser = TaskParser()
        response = {
            "action": "dispatch",
            "tasks": [
                {"server": "ShellMCP", "tool": "run", "params": {}, "remind_after": 60},
            ],
        }
        result = parser.parse(response)

        assert result["tasks"][0]["remind_after"] == 60

    def test_parse_dispatch_empty_tasks_returns_error(self):
        parser = TaskParser()
        response = {"action": "dispatch", "tasks": []}
        result = parser.parse(response)

        assert "error" in result

    def test_parse_dispatch_missing_tasks_returns_error(self):
        parser = TaskParser()
        response = {"action": "dispatch"}
        result = parser.parse(response)

        assert "error" in result

    def test_parse_dispatch_skips_invalid_tasks(self):
        parser = TaskParser()
        response = {
            "action": "dispatch",
            "tasks": [
                "not a dict",
                {"server": "ShellMCP"},  # missing tool
                {"server": "ShellMCP", "tool": "run", "params": {}},
            ],
        }
        result = parser.parse(response)

        assert result["action"] == "dispatch"
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["server"] == "ShellMCP"

    def test_parse_dispatch_all_invalid_tasks_returns_error(self):
        parser = TaskParser()
        response = {
            "action": "dispatch",
            "tasks": [
                "not a dict",
                {"server": "X"},  # missing tool
            ],
        }
        result = parser.parse(response)

        assert "error" in result


@pytest.mark.integration
class TestWaitActionParsing:
    """Test parsing of wait actions."""

    def test_parse_wait_action(self):
        parser = TaskParser()
        response = {"action": "wait"}
        result = parser.parse(response)

        assert result["action"] == "wait"
        assert result["goal_updates"] == []


@pytest.mark.integration
class TestKillActionParsing:
    """Test parsing of kill actions."""

    def test_parse_kill_action(self):
        parser = TaskParser()
        response = {"action": "kill", "pids": [1, 2, 3]}
        result = parser.parse(response)

        assert result["action"] == "kill"
        assert result["pids"] == [1, 2, 3]

    def test_parse_kill_empty_pids_returns_error(self):
        parser = TaskParser()
        response = {"action": "kill", "pids": []}
        result = parser.parse(response)

        assert "error" in result

    def test_parse_kill_missing_pids_returns_error(self):
        parser = TaskParser()
        response = {"action": "kill"}
        result = parser.parse(response)

        assert "error" in result


@pytest.mark.integration
class TestDeferActionParsing:
    """Test parsing of defer actions."""

    def test_parse_defer_action(self):
        parser = TaskParser()
        response = {
            "action": "defer",
            "goal_id": "g1",
            "duration": 1800,
            "reason": "waiting for data",
        }
        result = parser.parse(response)

        assert result["action"] == "defer"
        assert result["goal_id"] == "g1"
        assert result["duration"] == 1800
        assert result["reason"] == "waiting for data"

    def test_parse_defer_missing_goal_id_returns_error(self):
        parser = TaskParser()
        response = {"action": "defer", "duration": 60}
        result = parser.parse(response)

        assert "error" in result

    def test_parse_defer_missing_duration_returns_error(self):
        parser = TaskParser()
        response = {"action": "defer", "goal_id": "g1"}
        result = parser.parse(response)

        assert "error" in result

    def test_parse_defer_invalid_duration_returns_error(self):
        parser = TaskParser()
        response = {"action": "defer", "goal_id": "g1", "duration": -10}
        result = parser.parse(response)

        assert "error" in result

    def test_parse_defer_optional_reason(self):
        parser = TaskParser()
        response = {"action": "defer", "goal_id": "g1", "duration": 60}
        result = parser.parse(response)

        assert result["action"] == "defer"
        assert result["reason"] == ""


@pytest.mark.integration
class TestUnknownActionHandling:
    """Test handling of unknown and invalid actions."""

    def test_unknown_action_returns_error(self):
        parser = TaskParser()
        response = {"action": "unknown_action", "output": "test"}
        result = parser.parse(response)

        assert "error" in result
        assert "unknown_action" in result["error"].lower() or "Unknown" in result["error"]

    def test_missing_action_returns_error(self):
        parser = TaskParser()
        response = {"output": "test"}
        result = parser.parse(response)

        assert "error" in result

    def test_none_action_returns_error(self):
        parser = TaskParser()
        response = {"action": None, "output": "test"}
        result = parser.parse(response)

        assert "error" in result


@pytest.mark.integration
class TestGoalUpdatesInActions:
    """Test that goal_updates are preserved across all action types."""

    def test_dispatch_with_goal_updates(self):
        parser = TaskParser()
        response = {
            "action": "dispatch",
            "tasks": [{"server": "X", "tool": "y", "params": {}}],
            "goal_updates": [{"id": "g1", "status": "active"}],
        }
        result = parser.parse(response)
        assert len(result["goal_updates"]) == 1

    def test_wait_with_goal_updates(self):
        parser = TaskParser()
        response = {
            "action": "wait",
            "goal_updates": [{"id": "g1", "status": "active"}],
        }
        result = parser.parse(response)
        assert len(result["goal_updates"]) == 1

    def test_kill_with_goal_updates(self):
        parser = TaskParser()
        response = {
            "action": "kill",
            "pids": [1],
            "goal_updates": [{"id": "g1", "status": "failed"}],
        }
        result = parser.parse(response)
        assert len(result["goal_updates"]) == 1
