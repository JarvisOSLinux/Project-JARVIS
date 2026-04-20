"""
Integration test utilities for JARVIS AI Assistant.

Provides helper functions and utilities for integration testing across all subsystems.
Updated for the event-driven dispatch architecture.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock

# ---------------------------------------------------------------------------
# Dispatch-format response builders
# ---------------------------------------------------------------------------


def make_respond_action(
    output: str, goal_updates: Optional[List] = None
) -> Dict[str, Any]:
    """Create a respond action (LLM tells the user something)."""
    resp = {"action": "respond", "output": output}
    if goal_updates:
        resp["goal_updates"] = goal_updates
    return resp


def make_dispatch_action(
    tasks: List[Dict[str, Any]], goal_updates: Optional[List] = None
) -> Dict[str, Any]:
    """Create a dispatch action (LLM sends tasks to dispatch)."""
    resp = {"action": "dispatch", "tasks": tasks}
    if goal_updates:
        resp["goal_updates"] = goal_updates
    return resp


def make_wait_action() -> Dict[str, Any]:
    """Create a wait action."""
    return {"action": "wait"}


def make_kill_action(pids: List[int]) -> Dict[str, Any]:
    """Create a kill action."""
    return {"action": "kill", "pids": pids}


def make_defer_action(goal_id: str, duration: int, reason: str = "") -> Dict[str, Any]:
    """Create a defer action."""
    return {
        "action": "defer",
        "goal_id": goal_id,
        "duration": duration,
        "reason": reason,
    }


def make_task(
    server: str,
    tool: str,
    params: Optional[Dict] = None,
    remind_after: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a single task dict for dispatch actions."""
    task = {"server": server, "tool": tool, "params": params or {}}
    if remind_after is not None:
        task["remind_after"] = remind_after
    return task


# ---------------------------------------------------------------------------
# Signal builders (dispatch signals coming back from dispatch binary)
# ---------------------------------------------------------------------------


def make_signal(
    pid: int,
    signal_type: str,
    data: Optional[Dict] = None,
    metadata: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Create a dispatch signal dict."""
    sig = {"pid": pid, "type": signal_type}
    if data is not None:
        sig["data"] = data
    if metadata is not None:
        sig["metadata"] = metadata
    return sig


def make_exit_signal(pid: int, output: str = "", error: str = "") -> Dict[str, Any]:
    """Create an EXIT signal."""
    data = {}
    if output:
        data["output"] = output
    if error:
        data["error"] = error
    return make_signal(pid, "EXIT", data=data)


def make_remind_signal(pid: int, goal_id: Optional[str] = None) -> Dict[str, Any]:
    """Create a REMIND signal, optionally linked to a goal."""
    metadata = {"goal_id": goal_id} if goal_id else None
    return make_signal(pid, "REMIND", metadata=metadata)


# ---------------------------------------------------------------------------
# Mock factories
# ---------------------------------------------------------------------------


def create_mock_llm(responses: Optional[List[Dict[str, Any]]] = None) -> Mock:
    """Create a mock LLM that returns predefined dispatch-format responses."""
    llm = Mock()
    if responses:
        llm.ask = Mock(side_effect=responses)
    else:
        llm.ask = Mock(return_value=make_respond_action("Hello!"))
    llm.reset_history = Mock()
    llm.provider = Mock()
    llm.provider.model = "test-model"
    return llm


def create_mock_dispatch_adapter(connected: bool = False) -> Mock:
    """Create a mock DispatchAdapter."""
    adapter = Mock()
    adapter.is_connected = connected
    adapter.connect = AsyncMock()
    adapter.disconnect = AsyncMock()
    adapter.send_tasks = AsyncMock(return_value={"pids": [1]})
    adapter.kill_tasks = AsyncMock(return_value={"killed": [1]})
    adapter.set_timer = AsyncMock(return_value={"pid": 99})
    adapter.get_signal_window = AsyncMock(return_value=[])
    return adapter


# ---------------------------------------------------------------------------
# File system helpers
# ---------------------------------------------------------------------------


def create_temp_directory_with_files(files: Dict[str, str]) -> str:
    """Create a temporary directory with test files."""
    temp_dir = tempfile.mkdtemp()
    for file_path, content in files.items():
        full_path = Path(temp_dir) / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
    return temp_dir


def create_test_mcp_config(servers: Optional[Dict[str, Any]] = None) -> str:
    """Create a temporary mcp.json config file for testing."""
    if servers is None:
        servers = {
            "EchoMCP": {
                "command": "python",
                "args": ["tests/mock_servers/echo_server.py"],
                "type": "stdio",
                "description": "Test echo server",
                "enabled": True,
            }
        }
    config = {"mcpServers": servers}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config, f, indent=2)
        return f.name


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def setup_test_environment(config_overrides: Optional[Dict[str, str]] = None):
    """Set up test environment with configuration overrides."""
    original_env = os.environ.copy()

    if config_overrides:
        os.environ.update(config_overrides)

    class TestEnvironment:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            os.environ.clear()
            os.environ.update(original_env)

    return TestEnvironment()


# ---------------------------------------------------------------------------
# Async test helpers
# ---------------------------------------------------------------------------


def wait_for_async(coro, timeout: float = 5.0):
    """Helper to run async coroutines in tests."""
    import asyncio

    import pytest

    async def run_with_timeout():
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            pytest.fail(f"Async operation timed out after {timeout} seconds")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(run_with_timeout())
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Legacy LLM response helpers (for LLM-level tests that care about raw JSON)
# ---------------------------------------------------------------------------


def mock_llm_response(
    user_request: str = "Conversation", output: str = "Hello!"
) -> Dict[str, Any]:
    """Create a mock LLM response in the old JSON format (user_request/output).

    The LLM actually returns free-form JSON; the *new* dispatch format uses
    ``action`` as the key. These helpers are kept for tests that exercise
    the raw LLM response parsing layer.
    """
    return {"user_request": user_request, "output": output}


def mock_llm_supermcp_command(command: str) -> Dict[str, Any]:
    """Create a mock SuperMCP command response from the LLM."""
    return {"user_request": "SuperMCP", "output": command}


def assert_valid_json_response(response: Dict[str, Any]) -> None:
    """Assert that a response dict is valid per the old JSON format."""
    assert isinstance(response, dict), f"Expected dict, got {type(response)}"
    assert "user_request" in response, f"Missing 'user_request' key: {response}"
    assert "output" in response, f"Missing 'output' key: {response}"
    valid_types = {"Conversation", "SuperMCP"}
    assert (
        response["user_request"] in valid_types
    ), f"Invalid user_request type '{response['user_request']}'"


def create_mock_llm_provider(responses: Optional[List] = None) -> Mock:
    """Create a mock LLM provider that returns pre-serialized JSON strings.

    NOTE: The LLM constructor calls ``provider.chat()`` once during preload.
    This helper automatically prepends a preload response so the test
    responses are consumed during actual ``llm.ask()`` calls.
    """
    provider = Mock()
    provider.model = "mock-model"

    # Preload response (consumed by LLM.__init__)
    preload_response = json.dumps({"status": "ready"})

    if responses:
        json_responses = [preload_response]
        for r in responses:
            if isinstance(r, dict):
                json_responses.append(json.dumps(r))
            else:
                json_responses.append(r)  # already a string
        provider.chat = Mock(side_effect=json_responses)
    else:
        provider.chat = Mock(return_value=json.dumps(mock_llm_response()))

    return provider


# Keep old names available for backward compat in conftest fixtures
mock_shellmcp_approval_required = None  # removed
mock_shellmcp_approved_execution = None  # removed
MockApprovalHandler = None  # removed
create_mock_supermcp_client = None  # removed
mock_supermcp_tool_call = None  # removed
assert_supermcp_command_format = None  # removed
