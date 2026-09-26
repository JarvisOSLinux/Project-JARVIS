"""
Tests for the read-only status affordance (#191): DispatchAdapter.get_task_status /
get_task_output, and the ROOT "status" action handler in root_actions.py.

Also covers the live-output tail the status read asks for (#212): without it
"Still running?" can only ever be answered "Yes", never "Yes, and it is waiting
for you to answer a prompt".
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.config import Config
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
    not structured content, so _extract_content's json.loads fallback wraps it
    under "output" — get_task_status/get_task_output must unwrap that shape.
    """

    def _connected_adapter(self, tool_text: str):
        adapter = DispatchAdapter()
        adapter._connected = True
        adapter.session = MagicMock()
        result = MagicMock()
        # Both spellings, and both must be None. The MCP SDK's attribute is
        # `structured_content`; `structuredContent` is only the wire spelling.
        # A MagicMock auto-creates any attribute that is read, so leaving the
        # snake_case one unset would hand _extract_content a truthy mock and
        # this double would stop modelling the real CallToolResult (#240).
        result.structured_content = None
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
        adapter.session.call_tool.assert_awaited_once_with(
            "status", {"tail": Config.DISPATCH_STATUS_TAIL_CHARS}
        )

    @pytest.mark.asyncio
    async def test_get_task_status_non_json_returns_empty(self):
        adapter = self._connected_adapter("not json")
        assert await adapter.get_task_status() == []

    @pytest.mark.asyncio
    async def test_get_task_status_requests_the_configured_tail(self, monkeypatch):
        monkeypatch.setattr(Config, "DISPATCH_STATUS_TAIL_CHARS", 512)
        adapter = self._connected_adapter("[]")
        await adapter.get_task_status()
        adapter.session.call_tool.assert_awaited_once_with("status", {"tail": 512})

    @pytest.mark.asyncio
    async def test_explicit_zero_tail_sends_the_plain_status_request(self):
        adapter = self._connected_adapter("[]")
        await adapter.get_task_status(tail=0)
        adapter.session.call_tool.assert_awaited_once_with("status", {})

    @pytest.mark.asyncio
    async def test_tail_bearing_rows_survive_the_content_text_shape(self):
        """The tail rides in content[0].text like the rest of the status JSON."""
        rows = [
            {
                "pid": 1,
                "type": "mcp",
                "state": "running",
                "tail": "[hash=ab] <ab>Proceed with installation? [Y/n] </ab>",
                "tail_hash": "ab",
            }
        ]
        adapter = self._connected_adapter(json.dumps(rows))
        assert await adapter.get_task_status() == rows

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
    async def test_live_tail_reaches_the_model(self, monkeypatch):
        prompt = "Proceed with installation? [Y/n] "
        tasks = [
            {
                "pid": 1,
                "state": "running",
                "tail": f"[hash=ab] <ab>{prompt}</ab>",
                "tail_hash": "ab",
            }
        ]
        app = self._make_app(tasks)
        captured = {}

        async def fake_ask_llm(app, logger, context, tag=None, mode=None):
            captured["context"] = context
            return {"action": "respond", "output": "ok"}

        monkeypatch.setattr("jarvis.runtime.root_actions.ask_llm", fake_ask_llm)

        await _handle_status(app, MagicMock(), {"goal_id": None}, 0, 5)

        context = captured["context"]
        assert "LIVE_OUTPUT (pid 1" in context
        # Verbatim, provenance wrapper included — the model must read it as data.
        assert f"[hash=ab] <ab>{prompt}</ab>" in context
        # ...and lifted out of the row JSON, whose char budget a long tail would
        # otherwise consume before the pids and states are even printed.
        rows_line = context.split("STATUS_RESULT (all active tasks):\n")[1].split("\n")[
            0
        ]
        assert "tail_hash" not in rows_line
        assert '"pid": 1' in rows_line or '"pid":1' in rows_line

    @pytest.mark.asyncio
    async def test_tailless_tasks_add_no_live_output_block(self, monkeypatch):
        app = self._make_app([{"pid": 1, "state": "running"}])
        captured = {}

        async def fake_ask_llm(app, logger, context, tag=None, mode=None):
            captured["context"] = context
            return {"action": "respond", "output": "ok"}

        monkeypatch.setattr("jarvis.runtime.root_actions.ask_llm", fake_ask_llm)

        await _handle_status(app, MagicMock(), {"goal_id": None}, 0, 5)

        assert "LIVE_OUTPUT" not in captured["context"]

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


@pytest.mark.integration
class TestStatusPromptBlocks:
    """A block ROOT is never told about is a block it will not look for."""

    @pytest.mark.parametrize(
        "name",
        (
            "LLM_ROOT_PROMPT",
            "LLM_ROOT_PROMPT_UNIFIED",
            "LLM_ROOT_PROMPT_NO_CONTEXTOR",
            "LLM_ROOT_PROMPT_UNIFIED_NO_CONTEXTOR",
        ),
    )
    def test_live_output_is_documented_beside_held_output(self, name):
        prompt = getattr(Config, name)

        assert "LIVE_OUTPUT" in prompt
        assert "HELD_OUTPUT" in prompt
