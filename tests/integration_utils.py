"""
Integration test utilities for JARVIS AI Assistant.

Provides helper functions and utilities for integration testing across all subsystems.
"""

import json
import tempfile
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from unittest.mock import Mock, AsyncMock, MagicMock
import pytest

# JARVIS imports for testing
from jarvis.config import Config
from jarvis.core.logger import get_logger

logger = get_logger(__name__)


def create_test_mcp_config(servers: Optional[Dict[str, Any]] = None) -> str:
    """
    Create a temporary mcp.json config file for testing.

    Args:
        servers: Dictionary of MCP servers to include. If None, creates minimal test config.

    Returns:
        Path to the temporary config file
    """
    if servers is None:
        servers = {
            "EchoMCP": {
                "command": "python",
                "args": ["tests/mock_servers/echo_server.py"],
                "type": "stdio",
                "description": "Test echo server",
                "enabled": True
            }
        }

    config = {"mcpServers": servers}

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config, f, indent=2)
        return f.name


def mock_llm_response(user_request: str, output: str, **kwargs) -> Dict[str, Any]:
    """
    Create a mock LLM response dictionary.

    Args:
        user_request: Either "Conversation" or "SuperMCP"
        output: The response content
        **kwargs: Additional fields to include

    Returns:
        Properly formatted LLM response dictionary
    """
    response = {
        "user_request": user_request,
        "output": output
    }
    response.update(kwargs)
    return response


def mock_llm_conversation(question: str, answer: str) -> Dict[str, Any]:
    """
    Create a mock LLM conversation response.

    Args:
        question: User question (ignored, for documentation)
        answer: LLM's conversational response

    Returns:
        Conversation-type LLM response
    """
    return mock_llm_response("Conversation", answer)


def mock_llm_supermcp_command(commands: str) -> Dict[str, Any]:
    """
    Create a mock LLM SuperMCP command response.

    Args:
        commands: SuperMCP command sequence (e.g., "list_servers(); reload_servers()")

    Returns:
        SuperMCP-type LLM response
    """
    return mock_llm_response("SuperMCP", commands)


def mock_supermcp_tool_call(server: str, tool: str, args: Optional[Dict[str, Any]] = None,
                           result: Any = None, error: Optional[str] = None) -> Dict[str, Any]:
    """
    Create a mock SuperMCP tool call response.

    Args:
        server: MCP server name
        tool: Tool name
        args: Tool arguments
        result: Tool execution result
        error: Error message if tool failed

    Returns:
        Mock tool execution result
    """
    if error:
        return {"error": error}
    return {
        "server": server,
        "tool": tool,
        "arguments": args or {},
        "result": result,
        "success": True
    }


def mock_shellmcp_approval_required(command: str, command_id: str = "test-123") -> Dict[str, Any]:
    """
    Create a mock ShellMCP response requiring approval.

    Args:
        command: The command that requires approval
        command_id: Command ID for approval/denial

    Returns:
        Mock approval-required response
    """
    return {
        "approval_required": True,
        "server": "ShellMCP",
        "tool": "execute_command",
        "arguments": {"command": command},
        "shellmcp_response": f"This command requires approval. It has been queued with ID: {command_id}"
    }


def mock_shellmcp_approved_execution(command: str, output: str) -> Dict[str, Any]:
    """
    Create a mock ShellMCP successful execution response.

    Args:
        command: The executed command
        output: Command output

    Returns:
        Mock successful execution response
    """
    return {
        "server": "ShellMCP",
        "tool": "execute_command",
        "arguments": {"command": command},
        "result": {
            "success": True,
            "stdout": output,
            "stderr": "",
            "returncode": 0
        },
        "success": True
    }


def create_mock_llm_provider(responses: List[Dict[str, Any]]) -> Mock:
    """
    Create a mock LLM provider that returns predefined responses.

    Args:
        responses: List of responses to return in sequence

    Returns:
        Mock LLM provider
    """
    provider = Mock()
    provider.chat = Mock(side_effect=responses)
    provider.is_available.return_value = True
    return provider


def create_mock_supermcp_client() -> Mock:
    """
    Create a mock SuperMCP client for testing.

    Returns:
        Mock SuperMCP client
    """
    client = Mock()

    # Mock server operations
    client.reload_servers = AsyncMock(return_value={"servers": ["EchoMCP", "ShellMCP"]})
    client.list_servers = AsyncMock(return_value=["EchoMCP", "ShellMCP"])
    client.inspect_server = AsyncMock(return_value={
        "tools": [{"name": "echo", "description": "Echo back input"}]
    })

    # Mock tool calls
    def mock_call_server_tool(server, tool, args=None):
        if server == "EchoMCP" and tool == "echo":
            return {"result": args.get("message", "")}
        elif server == "ShellMCP" and tool == "get_platform_info":
            return {"result": {"os": "Linux", "arch": "x86_64"}}
        elif server == "ShellMCP" and tool == "execute_command":
            command = args.get("command", "")
            if "ls" in command:
                return {"result": {"stdout": "file1.txt\nfile2.py", "stderr": "", "returncode": 0}}
            else:
                return mock_shellmcp_approval_required(command)
        return {"error": f"Unknown tool {server}.{tool}"}

    client.call_server_tool = AsyncMock(side_effect=mock_call_server_tool)

    return client


def create_mock_mcp_server_response(success: bool = True, result: Any = None,
                                   error: Optional[str] = None) -> Dict[str, Any]:
    """
    Create a mock MCP server response.

    Args:
        success: Whether the operation succeeded
        result: The operation result
        error: Error message if operation failed

    Returns:
        Mock MCP server response
    """
    if not success:
        return {"success": False, "error": error}
    return {"success": True, "result": result}


def assert_valid_json_response(response: Dict[str, Any]) -> None:
    """
    Assert that a response is a valid JARVIS JSON response.

    Args:
        response: Response to validate

    Raises:
        AssertionError: If response is not valid
    """
    assert isinstance(response, dict), "Response must be a dictionary"
    assert "user_request" in response, "Response must have 'user_request' field"
    assert "output" in response, "Response must have 'output' field"

    user_request = response["user_request"]
    assert user_request in ["Conversation", "SuperMCP"], \
        f"user_request must be 'Conversation' or 'SuperMCP', got: {user_request}"


def assert_supermcp_command_format(command: str) -> None:
    """
    Assert that a SuperMCP command string is properly formatted.

    Args:
        command: Command string to validate

    Raises:
        AssertionError: If command format is invalid
    """
    assert isinstance(command, str), "SuperMCP command must be a string"
    assert command.strip() != "", "SuperMCP command cannot be empty"

    # Basic format check - should contain valid command patterns
    valid_patterns = [
        "reload_servers()",
        "list_servers()",
        "inspect_server(",
        "call_server_tool("
    ]

    has_valid_command = any(pattern in command for pattern in valid_patterns)
    assert has_valid_command, f"Command must contain valid SuperMCP pattern: {command}"


def create_temp_directory_with_files(files: Dict[str, str]) -> str:
    """
    Create a temporary directory with test files.

    Args:
        files: Dictionary mapping file paths to content

    Returns:
        Path to the temporary directory
    """
    temp_dir = tempfile.mkdtemp()

    for file_path, content in files.items():
        full_path = Path(temp_dir) / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)

    return temp_dir


def wait_for_async(coro, timeout: float = 5.0):
    """
    Helper to run async coroutines in tests.

    Args:
        coro: Coroutine to run
        timeout: Timeout in seconds

    Returns:
        Coroutine result
    """
    import asyncio

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


def create_test_jarvis_config(**overrides) -> Dict[str, Any]:
    """
    Create a test configuration for JARVIS components.

    Args:
        **overrides: Configuration overrides

    Returns:
        Test configuration dictionary
    """
    config = {
        'LLM_MODEL': 'test-model',
        'SUPERMCP_SERVER_PATH': 'SuperMCP/SuperMCP.py',
        'SUPERMCP_TIMEOUT': 30,
        'OUTPUT_MODE': 'text',
        'LOG_LEVEL': 'INFO',
        'TTS_MODEL_ONNX': 'test.onnx',
        'TTS_MODEL_JSON': 'test.json',
        'VOSK_MODEL_PATH': 'models/vosk-model-small-en-us-0.15'
    }
    config.update(overrides)
    return config


class MockApprovalHandler:
    """
    Mock approval handler for testing approval workflows.
    """

    def __init__(self, approve_commands: bool = True):
        """
        Initialize mock approval handler.

        Args:
            approve_commands: Whether to approve or deny commands by default
        """
        self.approve_commands = approve_commands
        self.requests = []

    def request_approval(self, command: str, security_level: str) -> bool:
        """
        Mock approval request.

        Args:
            command: Command requiring approval
            security_level: Security level of the command

        Returns:
            True if approved, False if denied
        """
        self.requests.append({
            'command': command,
            'security_level': security_level,
            'approved': self.approve_commands
        })

        return self.approve_commands

    def get_requests(self) -> List[Dict[str, Any]]:
        """Get all approval requests made."""
        return self.requests.copy()

    def clear_requests(self) -> None:
        """Clear the request history."""
        self.requests.clear()


def setup_test_environment(config_overrides: Optional[Dict[str, str]] = None):
    """
    Set up test environment with configuration overrides.

    Args:
        config_overrides: Environment variable overrides

    Returns:
        Context manager that cleans up environment on exit
    """
    original_env = os.environ.copy()

    if config_overrides:
        os.environ.update(config_overrides)

    class TestEnvironment:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            # Restore original environment
            os.environ.clear()
            os.environ.update(original_env)

    return TestEnvironment()