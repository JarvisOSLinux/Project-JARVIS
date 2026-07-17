"""
Tests for the read-only status affordance (#191): DispatchAdapter.get_task_status /
get_task_output, and the ROOT "status" action handler in root_actions.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.core.command_parser import TaskParser
from jarvis.dispatch.adapter import DispatchAdapter
from jarvis.runtime.root_actions import _handle_status


@pytest.mark.integration
class TestAdapterStatusNotConnected:
    @pytest.mark.asyncio
    async def test_get_task_status_returns_empty(self):
        adapter = DispatchAdapter()
        assert await adapter.get_task_status() == []

    @pytest.mark.asyncio
    async def test_get_task_output_returns_empty(self):
        adapter = DispatchAdapter()
        assert await adapter.get_task_output([1]) == ""


@pytest.mark.integration
class TestAdapterStatusConnected:
    """dispatch's status/get_output tools return their JSON via content[0].text,
    not structuredContent, so _extract_content's json.loads fallback wraps it
    under "output" — get_task_status/get_task_output must unwrap that shape.
    """

    def _connected_adapter(self, tool_text: str):
        adapter = DispatchAdapter()
        adapter._connected = True
        adapter.session = MagicMock()
        result = MagicMock()
        result.structuredContent = None
        block = MagicMock()
        block.text = tool_text
        result.content = [block]
        adapter.session.call_tool = AsyncMock(return_value=result)
        return adapter

    @pytest.mark.asyncio
    async def test_get_task_status_unwraps_output_list(self):
        adapter = self._connected_adapter(
            '[{"pid": 1, "type": "mcp", "server": "s", "tool": "t", "state": "running"}]'
        )
        tasks = await adapter.get_task_status()
        assert tasks == [
            {"pid": 1, "type": "mcp", "server": "s", "tool": "t", "state": "running"}
        ]
        adapter.session.call_tool.assert_awaited_once_with("status", {})

    @pytest.mark.asyncio
    async def test_get_task_status_non_json_returns_empty(self):
        adapter = self._connected_adapter("not json")
        assert await adapter.get_task_status() == []

    @pytest.mark.asyncio
    async def test_get_task_output_returns_text(self):
        adapter = self._connected_adapter("PID 1 [hash=abc]\nsome output")
        output = await adapter.get_task_output([1])
        assert output == "PID 1 [hash=abc]\nsome output"
        adapter.session.call_tool.assert_awaited_once_with("get_output", {"pids": [1]})


@pytest.mark.integration
class TestStatusActionHandler:
    """_handle_status (root_actions.py): read-only, no dispatch side effects."""

    def _make_app(self, tasks, held_output="", goal=None):
        app = MagicMock()
        app.dispatch.get_task_status = AsyncMock(return_value=tasks)
        app.dispatch.get_task_output = AsyncMock(return_value=held_output)
        app.goals.get_goal = MagicMock(return_value=goal)
        app.goals.get_context = MagicMock(return_value=[])
        app.sessions.load_summary = MagicMock(return_value=None)
        app.contextor = None
        app._act_on_root_response = AsyncMock()
        return app

    @pytest.mark.asyncio
    async def test_status_no_goal_scope_reports_all_tasks(self, monkeypatch):
        tasks = [{"pid": 1, "state": "running"}, {"pid": 2, "state": "done"}]
        app = self._make_app(tasks, held_output="PID 2\ndone output")

        captured = {}

        async def fake_ask_llm(app, logger, context, tag=None, mode=None):
            captured["context"] = context
            return {"action": "respond", "output": "ok"}

        monkeypatch.setattr("jarvis.runtime.root_actions.ask_llm", fake_ask_llm)

        await _handle_status(app, MagicMock(), {"goal_id": None}, 0, 5)

        app.dispatch.get_task_status.assert_awaited_once()
        app.dispatch.get_task_output.assert_awaited_once_with([2])
        assert "STATUS_RESULT (all active tasks)" in captured["context"]
        assert "HELD_OUTPUT" in captured["context"]
        app._act_on_root_response.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_scoped_to_goal_filters_by_task_pids(self, monkeypatch):
        goal = MagicMock()
        goal.id = "g1"
        goal.description = "check things"
        goal.task_pids = [2]
        tasks = [
            {"pid": 1, "state": "running"},
            {"pid": 2, "state": "running"},
        ]
        app = self._make_app(tasks, held_output="", goal=goal)

        async def fake_ask_llm(app, logger, context, tag=None, mode=None):
            return {"action": "respond", "output": "ok"}

        monkeypatch.setattr("jarvis.runtime.root_actions.ask_llm", fake_ask_llm)

        await _handle_status(app, MagicMock(), {"goal_id": "g1"}, 0, 5)

        app.goals.get_goal.assert_called_once_with("g1")
        # Only pid 2 belongs to the goal; pid 1 must be filtered out, and both
        # are "running" so get_task_output should not be called at all.
        app.dispatch.get_task_output.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_status_no_tasks_reports_none(self, monkeypatch):
        app = self._make_app([], held_output="")
        captured = {}

        async def fake_ask_llm(app, logger, context, tag=None, mode=None):
            captured["context"] = context
            return {"action": "respond", "output": "ok"}

        monkeypatch.setattr("jarvis.runtime.root_actions.ask_llm", fake_ask_llm)

        await _handle_status(app, MagicMock(), {"goal_id": None}, 0, 5)

        assert "no running or held tasks" in captured["context"]


@pytest.mark.integration
class TestStatusActionParserRoundTrip:
    def test_status_action_is_valid(self):
        result = TaskParser.parse({"action": "status", "goal_id": "g1"})
        assert result == {
            "action": "status",
            "goal_id": "g1",
            "goal_updates": [],
        }
