"""
End-to-end integration tests for JARVIS AI Assistant.

Tests complete user request → response workflows including LLM processing,
SuperMCP orchestration, and approval workflows.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from tests.integration_utils import (
    mock_llm_conversation,
    mock_llm_supermcp_command,
    mock_shellmcp_approved_execution,
    mock_shellmcp_approval_required,
    assert_valid_json_response,
    wait_for_async
)


@pytest.mark.integration
class TestEndToEndWorkflows:
    """Test complete end-to-end workflows from user input to response."""

    def test_simple_conversation_workflow(self, jarvis_instance, mock_llm_conversation):
        """Test simple conversation: user asks question, gets conversational response."""
        # Setup mock LLM response
        conversation_response = mock_llm_conversation("What is Python?", "Python is a programming language.")
        jarvis_instance.llm.ask.return_value = conversation_response

        # Process user request
        result = jarvis_instance.ask("What is Python?")

        # Verify response
        assert_valid_json_response(result)
        assert result["user_request"] == "Conversation"
        assert "programming language" in result["output"]

        # Verify LLM was called
        jarvis_instance.llm.ask.assert_called_once_with("What is Python?")

    def test_supermcp_command_workflow(self, jarvis_instance, mock_llm_supermcp_command, mock_supermcp_client):
        """Test SuperMCP command execution workflow."""
        # Setup mock LLM response with SuperMCP command
        supermcp_response = mock_llm_supermcp_command("list_servers()")
        jarvis_instance.llm.ask.return_value = supermcp_response

        # Mock SuperMCP command execution
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [{"servers": ["EchoMCP", "ShellMCP"]}]
        }

        # Setup second LLM call for final conversation response
        final_response = mock_llm_conversation("", "Available servers: EchoMCP, ShellMCP")
        jarvis_instance.llm.ask.return_value = final_response

        # Process user request
        result = jarvis_instance.ask("What servers are available?")

        # Verify response flow
        assert_valid_json_response(result)
        assert result["user_request"] == "Conversation"
        assert "EchoMCP" in result["output"]

    def test_multi_step_workflow(self, jarvis_instance, mock_llm_supermcp_command, mock_supermcp_client):
        """Test multi-step workflow requiring multiple SuperMCP commands."""
        # First LLM response: list servers and get platform info
        first_response = mock_llm_supermcp_command("list_servers(); call_server_tool(ShellMCP, get_platform_info, {})")
        jarvis_instance.llm.ask.return_value = first_response

        # Mock SuperMCP command execution
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [
                {"servers": ["EchoMCP", "ShellMCP"]},
                {"result": {"os": "Linux", "arch": "x86_64"}}
            ]
        }

        # Second LLM call for final response
        final_response = mock_llm_conversation("", "Found 2 servers on Linux x86_64 system.")
        jarvis_instance.llm.ask.return_value = final_response

        # Process user request
        result = jarvis_instance.ask("Tell me about the system and servers")

        # Verify multi-step execution
        assert_valid_json_response(result)
        assert result["user_request"] == "Conversation"
        assert "Linux" in result["output"] and "servers" in result["output"]

    def test_approval_workflow_approved(self, jarvis_instance, mock_llm_supermcp_command,
                                      mock_approval_handler, mock_supermcp_client):
        """Test complete approval workflow where user approves the command."""
        # First LLM response: execute a command that requires approval
        first_response = mock_llm_supermcp_command("call_server_tool(ShellMCP, execute_command, {command: 'rm -rf /tmp/*'})")
        jarvis_instance.llm.ask.return_value = first_response

        # Mock SuperMCP command execution - returns approval required
        approval_result = mock_shellmcp_approval_required("rm -rf /tmp/*", "cmd-123")
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [approval_result]
        }

        # Mock approval request
        jarvis_instance.request_approval = mock_approval_handler.request_approval
        mock_approval_handler.approve_commands = True

        # Mock command execution after approval
        execution_result = mock_shellmcp_approved_execution("rm -rf /tmp/*", "Removed 5 files")
        jarvis_instance.command_parser.supermcp.call_server_tool.return_value = execution_result

        # Second LLM call for final response
        final_response = mock_llm_conversation("", "Successfully removed 5 files from /tmp/")
        jarvis_instance.llm.ask.return_value = final_response

        # Process user request
        result = jarvis_instance.ask("Clean up temp files")

        # Verify approval workflow completed
        assert_valid_json_response(result)
        assert result["user_request"] == "Conversation"
        assert "removed 5 files" in result["output"]

        # Verify approval was requested
        requests = mock_approval_handler.get_requests()
        assert len(requests) == 1
        assert "rm -rf /tmp/*" in requests[0]["command"]

    def test_approval_workflow_denied(self, jarvis_instance, mock_llm_supermcp_command,
                                    mock_approval_handler_deny, mock_supermcp_client):
        """Test approval workflow where user denies the command."""
        # First LLM response: execute dangerous command
        first_response = mock_llm_supermcp_command("call_server_tool(ShellMCP, execute_command, {command: 'rm -rf /'})")
        jarvis_instance.llm.ask.return_value = first_response

        # Mock SuperMCP command execution - returns approval required
        approval_result = mock_shellmcp_approval_required("rm -rf /", "cmd-456")
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [approval_result]
        }

        # Mock approval denial
        jarvis_instance.request_approval = mock_approval_handler_deny.request_approval

        # Mock deny command call
        jarvis_instance.command_parser.supermcp.call_server_tool.return_value = {"success": True}

        # Second LLM call for final response
        final_response = mock_llm_conversation("", "Command was denied by user for security reasons.")
        jarvis_instance.llm.ask.return_value = final_response

        # Process user request
        result = jarvis_instance.ask("Delete everything")

        # Verify denial workflow
        assert_valid_json_response(result)
        assert result["user_request"] == "Conversation"
        assert "denied" in result["output"] or "security" in result["output"]

        # Verify denial was recorded
        requests = mock_approval_handler_deny.get_requests()
        assert len(requests) == 1
        assert requests[0]["approved"] == False

    def test_error_recovery_workflow(self, jarvis_instance, mock_llm_supermcp_command, mock_supermcp_client):
        """Test error recovery when SuperMCP command fails."""
        # First LLM response: execute failing command
        first_response = mock_llm_supermcp_command("call_server_tool(NonExistentMCP, bad_tool, {})")
        jarvis_instance.llm.ask.return_value = first_response

        # Mock SuperMCP command execution - returns error
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": False,
            "error": "Server 'NonExistentMCP' not found"
        }

        # Second LLM call for error recovery
        recovery_response = mock_llm_conversation("", "I apologize, but I couldn't access that server. Please try a different request.")
        jarvis_instance.llm.ask.return_value = recovery_response

        # Process user request
        result = jarvis_instance.ask("Do something with a non-existent server")

        # Verify error recovery
        assert_valid_json_response(result)
        assert result["user_request"] == "Conversation"
        assert "apologize" in result["output"] or "couldn't" in result["output"]

    def test_loop_detection_workflow(self, jarvis_instance, mock_llm_supermcp_command, mock_supermcp_client):
        """Test loop detection when LLM repeats the same command."""
        # LLM keeps returning the same failing command
        failing_command = mock_llm_supermcp_command("call_server_tool(BrokenMCP, broken_tool, {})")

        # Mock SuperMCP always fails with same error
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": False,
            "error": "Tool 'broken_tool' not found"
        }

        # Process request - should detect loop and break after max iterations
        result = jarvis_instance.ask("Use broken tool")

        # Should eventually break the loop and return error message
        assert_valid_json_response(result)
        assert result["user_request"] == "Conversation"
        assert "difficulty" in result["output"] or "try again" in result["output"]

    def test_partial_success_workflow(self, jarvis_instance, mock_llm_supermcp_command, mock_supermcp_client):
        """Test workflow with partial success/failure of multiple commands."""
        # LLM response: execute multiple commands
        multi_command = mock_llm_supermcp_command("list_servers(); call_server_tool(BadMCP, bad_tool, {}); call_server_tool(ShellMCP, get_platform_info, {})")
        jarvis_instance.llm.ask.return_value = multi_command

        # Mock mixed results: success, failure, success
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [
                {"servers": ["ShellMCP", "EchoMCP"]},  # Success
                {"error": "BadMCP not found"},        # Failure
                {"result": {"os": "Linux"}}           # Success
            ]
        }

        # Final LLM response acknowledging partial success
        final_response = mock_llm_conversation("", "Found servers ShellMCP and EchoMCP. System is running Linux. Note: BadMCP was not available.")
        jarvis_instance.llm.ask.return_value = final_response

        # Process request
        result = jarvis_instance.ask("Check system and servers")

        # Verify partial success handling
        assert_valid_json_response(result)
        assert result["user_request"] == "Conversation"
        assert "ShellMCP" in result["output"] and "Linux" in result["output"]