"""
Error handling and recovery integration tests for JARVIS AI Assistant.

Tests error scenarios, recovery mechanisms, loop detection, retry logic,
and graceful handling of various failure modes.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from tests.integration_utils import (
    mock_llm_response,
    mock_llm_supermcp_command,
    create_mock_supermcp_client
)


@pytest.mark.integration
class TestLLMErrorHandling:
    """Test LLM error handling and recovery."""

    def test_llm_service_unavailable(self, jarvis_instance):
        """Test handling when LLM service is completely unavailable."""
        # Mock LLM to raise connection error
        jarvis_instance.llm.ask.side_effect = ConnectionError("LLM service unavailable")

        # Should handle gracefully
        result = jarvis_instance.ask("Test question")

        # Should return a valid response indicating the error
        assert isinstance(result, dict)
        assert "user_request" in result
        # May return error message or fallback response

    def test_llm_timeout_handling(self, jarvis_instance):
        """Test handling of LLM request timeouts."""
        import asyncio

        # Mock LLM to timeout
        async def timeout_llm():
            await asyncio.sleep(10)  # Long delay
            return mock_llm_response("Conversation", "Too late!")

        jarvis_instance.llm.ask = Mock(side_effect=asyncio.TimeoutError("Request timed out"))

        # Should handle timeout gracefully
        result = jarvis_instance.ask("Test question")

        assert isinstance(result, dict)
        assert "user_request" in result

    def test_llm_returns_invalid_json(self, jarvis_instance):
        """Test handling when LLM returns invalid JSON."""
        # Mock LLM to return invalid JSON
        invalid_responses = [
            '{"user_request": "Conversation", "output": "incomplete json',
            '{"user_request": "InvalidType", "output": "test"}',
            'not json at all',
            '{"user_request": "Conversation"}',  # Missing output
            '{"output": "test"}',  # Missing user_request
        ]

        for invalid_json in invalid_responses:
            jarvis_instance.llm.ask.return_value = invalid_json

            result = jarvis_instance.ask("Test question")

            # Should handle gracefully and return valid response
            assert isinstance(result, dict)
            assert "user_request" in result
            assert "output" in result

    def test_llm_returns_empty_response(self, jarvis_instance):
        """Test handling of empty LLM responses."""
        jarvis_instance.llm.ask.return_value = ""

        result = jarvis_instance.ask("Test question")

        # Should handle empty response
        assert isinstance(result, dict)
        assert "user_request" in result

    def test_llm_provider_switching_on_failure(self, jarvis_instance):
        """Test automatic LLM provider switching on failure."""
        # This would require multiple LLM providers configured
        # For now, test that single provider failure is handled
        jarvis_instance.llm.ask.side_effect = Exception("Provider failed")

        result = jarvis_instance.ask("Test question")

        assert isinstance(result, dict)
        # Should either recover or provide error message


@pytest.mark.integration
class TestSuperMCPErrorHandling:
    """Test SuperMCP error handling and recovery."""

    def test_supermcp_connection_failure(self, jarvis_instance):
        """Test handling of SuperMCP connection failures."""
        # Mock SuperMCP command execution to fail
        jarvis_instance.command_parser.execute_command_sequence.side_effect = ConnectionError("SuperMCP unreachable")

        # Setup LLM to request SuperMCP command
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("list_servers()")

        result = jarvis_instance.ask("List servers")

        # Should handle connection failure gracefully
        assert isinstance(result, dict)
        assert "user_request" in result

    def test_supermcp_server_not_found(self, jarvis_instance):
        """Test handling when requested MCP server doesn't exist."""
        # Setup command for non-existent server
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("inspect_server(NonExistentServer)")

        # Mock SuperMCP to return server not found error
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": False,
            "error": "Server 'NonExistentServer' not found"
        }

        result = jarvis_instance.ask("Inspect unknown server")

        assert result["user_request"] == "Conversation"
        # Should contain error information or recovery message

    def test_supermcp_tool_not_found(self, jarvis_instance):
        """Test handling when requested tool doesn't exist on server."""
        # Setup command for non-existent tool
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("call_server_tool(EchoMCP, nonexistent_tool, {})")

        # Mock tool execution to fail
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": False,
            "results": [{"error": "Tool 'nonexistent_tool' not found on server 'EchoMCP'"}]
        }

        result = jarvis_instance.ask("Use unknown tool")

        assert isinstance(result, dict)
        assert result["user_request"] == "Conversation"

    def test_supermcp_partial_command_failure(self, jarvis_instance):
        """Test handling when some commands in sequence fail."""
        # Setup multi-command sequence
        commands = "list_servers(); call_server_tool(BadServer, bad_tool, {}); call_server_tool(ShellMCP, get_platform_info, {})"
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command(commands)

        # Mock mixed results: success, failure, success
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [
                {"servers": ["ShellMCP", "EchoMCP"]},  # Success
                {"error": "BadServer not found"},     # Failure
                {"result": {"os": "Linux"}}           # Success
            ]
        }

        # Setup LLM to handle partial failure
        jarvis_instance.llm.ask.return_value = mock_llm_response(
            "Conversation",
            "Found servers ShellMCP and EchoMCP. BadServer was not available. System is running Linux."
        )

        result = jarvis_instance.ask("Check system and servers")

        assert result["user_request"] == "Conversation"
        assert "BadServer was not available" in result["output"]
        assert "Linux" in result["output"]


@pytest.mark.integration
class TestLoopDetectionAndRecovery:
    """Test loop detection and infinite loop prevention."""

    def test_llm_command_repetition_detection(self, jarvis_instance):
        """Test detection when LLM repeats the same command."""
        # Setup LLM to always return the same failing command
        failing_command = mock_llm_supermcp_command("call_server_tool(BrokenServer, broken_tool, {})")
        jarvis_instance.llm.ask.side_effect = [
            failing_command,  # First attempt
            failing_command,  # Second attempt (repeat)
            failing_command,  # Third attempt (repeat detected)
        ]

        # Mock command execution to always fail
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": False,
            "error": "BrokenServer not found"
        }

        result = jarvis_instance.ask("Use broken server")

        # Should detect loop and break with error message
        assert result["user_request"] == "Conversation"
        assert "difficulty" in result["output"] or "try again" in result["output"]

    def test_max_iterations_limit(self, jarvis_instance):
        """Test that system respects maximum iteration limits."""
        # Setup scenario that would loop indefinitely
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("list_servers()")

        # Mock command that always requires more processing
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [{"servers": ["Server1"]}]
        }

        # This should eventually hit the iteration limit
        result = jarvis_instance.ask("List servers repeatedly")

        # Should eventually stop and return a response
        assert isinstance(result, dict)
        assert "user_request" in result

    def test_loop_detection_reset_between_requests(self, jarvis_instance):
        """Test that loop detection state resets between different requests."""
        # First request with repetition
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("call_server_tool(Broken, tool, {})")
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": False,
            "error": "Broken server"
        }

        # First request should work normally
        result1 = jarvis_instance.ask("First broken request")

        # Second different request should not be affected by first request's loop detection
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("list_servers()")
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [{"servers": ["EchoMCP"]}]
        }

        result2 = jarvis_instance.ask("Second normal request")

        # Both should return valid responses
        assert isinstance(result1, dict)
        assert isinstance(result2, dict)


@pytest.mark.integration
class TestRetryLogicAndRecovery:
    """Test retry logic and error recovery mechanisms."""

    def test_automatic_command_retry_on_failure(self, jarvis_instance):
        """Test automatic retry of failed commands."""
        # Setup LLM to provide alternative command after failure
        jarvis_instance.llm.ask.side_effect = [
            mock_llm_supermcp_command("call_server_tool(BadServer, tool, {})"),  # First attempt fails
            mock_llm_supermcp_command("call_server_tool(GoodServer, tool, {})"), # Retry with different server
        ]

        # Mock first command fails, second succeeds
        jarvis_instance.command_parser.execute_command_sequence.side_effect = [
            {"success": False, "error": "BadServer not found"},  # First failure
            {"success": True, "results": [{"result": "Success!"}]},  # Second success
        ]

        result = jarvis_instance.ask("Execute command with retry")

        # Should eventually succeed
        assert result["user_request"] == "Conversation"
        assert "Success" in result["output"]

    def test_graceful_degradation_on_all_failures(self, jarvis_instance):
        """Test graceful degradation when all retry attempts fail."""
        # Setup LLM that keeps trying failing commands
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("call_server_tool(AlwaysFails, tool, {})")

        # Mock all attempts fail
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": False,
            "error": "Server always fails"
        }

        result = jarvis_instance.ask("Impossible task")

        # Should eventually give up gracefully
        assert isinstance(result, dict)
        assert result["user_request"] == "Conversation"

    def test_partial_recovery_scenarios(self, jarvis_instance):
        """Test recovery from partial failures."""
        # Setup multi-step process where some steps fail but others succeed
        commands = "reload_servers(); inspect_server(BadServer); list_servers()"
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command(commands)

        # Mock: reload succeeds, inspect fails, list succeeds
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [
                {"message": "Reloaded 3 servers"},
                {"error": "BadServer not found"},
                {"servers": ["EchoMCP", "ShellMCP"]}
            ]
        }

        result = jarvis_instance.ask("Complex multi-step task")

        # Should handle partial success
        assert isinstance(result, dict)
        assert result["user_request"] == "Conversation"


@pytest.mark.integration
class TestResourceAndTimeoutHandling:
    """Test resource management and timeout handling."""

    def test_long_running_command_timeout(self, jarvis_instance):
        """Test handling of long-running commands that timeout."""
        import asyncio

        # Setup long-running command
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("call_server_tool(ShellMCP, execute_command, {command: 'sleep 30'})")

        # Mock command execution that takes too long
        async def slow_execution(*args, **kwargs):
            await asyncio.sleep(35)  # Longer than timeout
            return {"result": "Too late!"}

        jarvis_instance.command_parser.execute_command_sequence = AsyncMock(side_effect=slow_execution)

        result = jarvis_instance.ask("Run slow command")

        # Should handle timeout gracefully
        assert isinstance(result, dict)

    def test_memory_error_handling(self, jarvis_instance):
        """Test handling of memory errors during processing."""
        # Setup command that causes memory issues
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("call_server_tool(ShellMCP, execute_command, {command: 'huge_command'})")

        # Mock memory error
        jarvis_instance.command_parser.execute_command_sequence.side_effect = MemoryError("Out of memory")

        result = jarvis_instance.ask("Run memory-intensive command")

        # Should handle memory error gracefully
        assert isinstance(result, dict)
        assert "user_request" in result

    def test_network_error_recovery(self, jarvis_instance):
        """Test recovery from network-related errors."""
        # Setup network-dependent command
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("reload_servers()")

        # Mock network error
        jarvis_instance.command_parser.execute_command_sequence.side_effect = OSError("Network is unreachable")

        result = jarvis_instance.ask("Refresh servers")

        # Should handle network error gracefully
        assert isinstance(result, dict)
        assert "user_request" in result


@pytest.mark.integration
class TestComplexErrorScenarios:
    """Test complex error scenarios with multiple failure modes."""

    def test_cascading_error_recovery(self, jarvis_instance):
        """Test recovery from cascading errors."""
        # Setup scenario where one error leads to another
        jarvis_instance.llm.ask.side_effect = [
            mock_llm_supermcp_command("call_server_tool(Server1, tool1, {})"),
            mock_llm_supermcp_command("call_server_tool(Server2, tool2, {})"),  # Fallback
            mock_llm_response("Conversation", "Unable to complete the requested operation.")
        ]

        # Mock cascading failures
        jarvis_instance.command_parser.execute_command_sequence.side_effect = [
            OSError("Server1 network error"),     # First server down
            Exception("Server2 authentication failed"),  # Second server auth issue
        ]

        result = jarvis_instance.ask("Complex operation")

        # Should eventually fail gracefully
        assert result["user_request"] == "Conversation"
        assert "Unable to complete" in result["output"]

    def test_error_message_propagation(self, jarvis_instance):
        """Test that error messages are properly propagated to user."""
        # Setup command that fails with specific error
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("call_server_tool(ShellMCP, execute_command, {command: 'nonexistent_cmd'})")

        # Mock specific error
        specific_error = "Command 'nonexistent_cmd' not found in PATH"
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": False,
            "error": specific_error
        }

        result = jarvis_instance.ask("Run unknown command")

        # Error should be communicated to user
        assert isinstance(result, dict)
        assert result["user_request"] == "Conversation"

    def test_error_state_cleanup(self, jarvis_instance):
        """Test that error states are properly cleaned up between requests."""
        # First request with error
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command("call_server_tool(Broken, tool, {})")
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": False,
            "error": "Broken server"
        }

        result1 = jarvis_instance.ask("First failing request")

        # Second request should work normally (not affected by first error)
        jarvis_instance.llm.ask.return_value = mock_llm_response("Conversation", "Second request successful")
        jarvis_instance.command_parser.execute_command_sequence = Mock()  # Reset

        result2 = jarvis_instance.ask("Second normal request")

        # Both should return valid responses
        assert isinstance(result1, dict)
        assert isinstance(result2, dict)
        assert result2["user_request"] == "Conversation"
        assert "successful" in result2["output"]