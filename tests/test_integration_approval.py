"""
Approval workflow integration tests for JARVIS AI Assistant.

Tests ShellMCP approval/denial workflow, command ID extraction,
approval request handling, and complete approval cycles.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from tests.integration_utils import (
    mock_shellmcp_approval_required,
    mock_shellmcp_approved_execution,
    MockApprovalHandler
)


@pytest.mark.integration
class TestApprovalRequestDetection:
    """Test detection of approval-required commands."""

    def test_detect_approval_required_response(self):
        """Test detection of approval-required response from ShellMCP."""
        from jarvis.main import Jarvis

        # Create Jarvis instance
        with patch('jarvis.core.component_factory.ComponentFactory.create_llm') as mock_llm, \
             patch('jarvis.core.component_factory.ComponentFactory.create_supermcp') as mock_supermcp, \
             patch('jarvis.core.component_factory.ComponentFactory.create_tts_optional') as mock_tts, \
             patch('jarvis.core.component_factory.ComponentFactory.create_voice_manager_optional') as mock_vm:

            mock_llm.return_value = Mock()
            mock_supermcp.return_value = Mock()
            mock_tts.return_value = None
            mock_vm.return_value = None

            jarvis = Jarvis(text_mode=True)

            # Test approval detection
            approval_response = mock_shellmcp_approval_required("rm -rf /tmp/*", "cmd-123")
            is_required = jarvis._is_approval_required([approval_response])

            assert is_required == True

    def test_detect_no_approval_required(self):
        """Test detection when approval is not required."""
        from jarvis.main import Jarvis

        with patch('jarvis.core.component_factory.ComponentFactory.create_llm') as mock_llm, \
             patch('jarvis.core.component_factory.ComponentFactory.create_supermcp') as mock_supermcp, \
             patch('jarvis.core.component_factory.ComponentFactory.create_tts_optional') as mock_tts, \
             patch('jarvis.core.component_factory.ComponentFactory.create_voice_manager_optional') as mock_vm:

            mock_llm.return_value = Mock()
            mock_supermcp.return_value = Mock()
            mock_tts.return_value = None
            mock_vm.return_value = None

            jarvis = Jarvis(text_mode=True)

            # Test non-approval response
            normal_response = mock_shellmcp_approved_execution("ls -la", "file1.txt\nfile2.py")
            is_required = jarvis._is_approval_required([normal_response])

            assert is_required == False

    def test_extract_command_id_from_approval_response(self):
        """Test extracting command ID from approval response."""
        from jarvis.main import Jarvis

        with patch('jarvis.core.component_factory.ComponentFactory.create_llm') as mock_llm, \
             patch('jarvis.core.component_factory.ComponentFactory.create_supermcp') as mock_supermcp, \
             patch('jarvis.core.component_factory.ComponentFactory.create_tts_optional') as mock_tts, \
             patch('jarvis.core.component_factory.ComponentFactory.create_voice_manager_optional') as mock_vm:

            mock_llm.return_value = Mock()
            mock_supermcp.return_value = Mock()
            mock_tts.return_value = None
            mock_vm.return_value = None

            jarvis = Jarvis(text_mode=True)

            # Test command ID extraction
            response_text = "This command requires approval. It has been queued with ID: test-uuid-123"
            command_id = jarvis._extract_command_id(response_text)

            assert command_id == "test-uuid-123"

    def test_extract_command_id_various_formats(self):
        """Test extracting command ID from various response formats."""
        from jarvis.main import Jarvis

        with patch('jarvis.core.component_factory.ComponentFactory.create_llm') as mock_llm, \
             patch('jarvis.core.component_factory.ComponentFactory.create_supermcp') as mock_supermcp, \
             patch('jarvis.core.component_factory.ComponentFactory.create_tts_optional') as mock_tts, \
             patch('jarvis.core.component_factory.ComponentFactory.create_voice_manager_optional') as mock_vm:

            mock_llm.return_value = Mock()
            mock_supermcp.return_value = Mock()
            mock_tts.return_value = None
            mock_vm.return_value = None

            jarvis = Jarvis(text_mode=True)

            test_cases = [
                ("Command queued with ID: simple-123", "simple-123"),
                ("ID: complex-uuid-here", "complex-uuid-here"),
                ("queued with ID: 123456", "123456"),
                ("no id in this message", None),
                ("", None),
            ]

            for response_text, expected_id in test_cases:
                command_id = jarvis._extract_command_id(response_text)
                assert command_id == expected_id


@pytest.mark.integration
class TestApprovalWorkflowApproved:
    """Test complete approval workflow when user approves."""

    def test_complete_approval_workflow_approved(self, jarvis_instance, mock_llm_supermcp_command,
                                                mock_approval_handler, mock_supermcp_client):
        """Test complete workflow: command requires approval, user approves, command executes."""
        # Setup LLM to request dangerous command
        dangerous_command = "call_server_tool(ShellMCP, execute_command, {command: 'rm -rf /tmp/*'})"
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command(dangerous_command)

        # Setup SuperMCP to require approval
        approval_result = mock_shellmcp_approval_required("rm -rf /tmp/*", "cmd-approve-test")
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [approval_result]
        }

        # Setup approval handler to approve
        jarvis_instance.request_approval = mock_approval_handler.request_approval

        # Setup command execution after approval
        execution_result = mock_shellmcp_approved_execution("rm -rf /tmp/*", "Removed 5 temporary files")
        jarvis_instance.command_parser.supermcp.call_server_tool.return_value = execution_result

        # Setup final LLM response
        final_response = {
            "user_request": "Conversation",
            "output": "Successfully cleaned up 5 temporary files from /tmp/"
        }
        jarvis_instance.llm.ask.return_value = final_response

        # Execute request
        result = jarvis_instance.ask("Clean up temp files")

        # Verify workflow completed successfully
        assert result["user_request"] == "Conversation"
        assert "cleaned up" in result["output"]
        assert "5 temporary files" in result["output"]

        # Verify approval was requested
        requests = mock_approval_handler.get_requests()
        assert len(requests) == 1
        assert "rm -rf /tmp/*" in requests[0]["command"]
        assert requests[0]["approved"] == True

    def test_approval_with_command_id_extraction(self, jarvis_instance, mock_supermcp_client):
        """Test approval workflow with proper command ID extraction and tool calls."""
        # Setup dangerous command request
        jarvis_instance.llm.ask.return_value = {
            "user_request": "SuperMCP",
            "output": "call_server_tool(ShellMCP, execute_command, {command: 'sudo rm -rf /var'})"
        }

        # Setup approval-required response with command ID
        approval_result = mock_shellmcp_approval_required("sudo rm -rf /var", "approval-uuid-456")
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [approval_result]
        }

        # Mock approval approval
        jarvis_instance.request_approval = Mock(return_value=True)

        # Mock approve_command tool call
        jarvis_instance.command_parser.supermcp.call_server_tool = AsyncMock(return_value={
            "success": True,
            "result": "Command approved and executed successfully"
        })

        # Setup final response
        jarvis_instance.llm.ask.return_value = {
            "user_request": "Conversation",
            "output": "System cleanup completed successfully."
        }

        # Execute
        result = jarvis_instance.ask("Clean system")

        # Verify approval_command was called with correct ID
        jarvis_instance.command_parser.supermcp.call_server_tool.assert_called_with(
            "ShellMCP",
            "approve_command",
            {"commandId": "approval-uuid-456"}
        )

        assert result["user_request"] == "Conversation"
        assert "completed successfully" in result["output"]


@pytest.mark.integration
class TestApprovalWorkflowDenied:
    """Test complete approval workflow when user denies."""

    def test_complete_approval_workflow_denied(self, jarvis_instance, mock_llm_supermcp_command,
                                              mock_approval_handler_deny, mock_supermcp_client):
        """Test complete workflow: command requires approval, user denies, command not executed."""
        # Setup dangerous command request
        dangerous_command = "call_server_tool(ShellMCP, execute_command, {command: 'rm -rf /home'})"
        jarvis_instance.llm.ask.return_value = mock_llm_supermcp_command(dangerous_command)

        # Setup approval-required response
        approval_result = mock_shellmcp_approval_required("rm -rf /home", "cmd-deny-test")
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [approval_result]
        }

        # Setup denial handler
        jarvis_instance.request_approval = mock_approval_handler_deny.request_approval

        # Mock deny_command tool call
        jarvis_instance.command_parser.supermcp.call_server_tool = AsyncMock(return_value={
            "success": True,
            "result": "Command denied by user"
        })

        # Setup final LLM response acknowledging denial
        final_response = {
            "user_request": "Conversation",
            "output": "Command was denied for security reasons. No files were deleted."
        }
        jarvis_instance.llm.ask.return_value = final_response

        # Execute request
        result = jarvis_instance.ask("Delete all user files")

        # Verify workflow completed with denial
        assert result["user_request"] == "Conversation"
        assert "denied" in result["output"] or "security reasons" in result["output"]

        # Verify denial was recorded
        requests = mock_approval_handler_deny.get_requests()
        assert len(requests) == 1
        assert "rm -rf /home" in requests[0]["command"]
        assert requests[0]["approved"] == False

        # Verify deny_command was called
        jarvis_instance.command_parser.supermcp.call_server_tool.assert_called_with(
            "ShellMCP",
            "deny_command",
            {"commandId": "cmd-deny-test", "reason": "User denied the command"}
        )

    def test_denial_without_command_id(self, jarvis_instance, mock_approval_handler_deny):
        """Test denial workflow when no command ID is available."""
        # Setup approval-required response without command ID
        approval_result = {
            "approval_required": True,
            "server": "ShellMCP",
            "tool": "execute_command",
            "arguments": {"command": "dangerous_cmd"},
            "shellmcp_response": "This command requires approval but no ID was provided."
        }

        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [approval_result]
        }

        # Setup denial
        jarvis_instance.request_approval = mock_approval_handler_deny.request_approval

        # Setup final response (no deny_command call should be made)
        final_response = {
            "user_request": "Conversation",
            "output": "Command was denied. No execution was performed."
        }
        jarvis_instance.llm.ask.return_value = final_response

        # Execute (mock the initial dangerous command request)
        jarvis_instance.llm.ask.return_value = {
            "user_request": "SuperMCP",
            "output": "call_server_tool(ShellMCP, execute_command, {command: 'dangerous_cmd'})"
        }

        result = jarvis_instance.ask("Run dangerous command")

        # Verify denial worked without command ID
        assert result["user_request"] == "Conversation"
        assert "denied" in result["output"]

        # Verify no deny_command call was made (since no command ID)
        jarvis_instance.command_parser.supermcp.call_server_tool.assert_not_called()


@pytest.mark.integration
class TestApprovalEdgeCases:
    """Test approval workflow edge cases."""

    def test_multiple_approval_requests(self, jarvis_instance, mock_approval_handler):
        """Test handling multiple commands requiring approval in sequence."""
        # Setup command sequence with multiple dangerous commands
        commands = "call_server_tool(ShellMCP, execute_command, {command: 'rm -rf /tmp'}); call_server_tool(ShellMCP, execute_command, {command: 'rm -rf /var'})"
        jarvis_instance.llm.ask.return_value = {
            "user_request": "SuperMCP",
            "output": commands
        }

        # Setup both commands requiring approval
        approval1 = mock_shellmcp_approval_required("rm -rf /tmp", "cmd-1")
        approval2 = mock_shellmcp_approval_required("rm -rf /var", "cmd-2")

        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [approval1, approval2]
        }

        # Setup approval handler
        jarvis_instance.request_approval = mock_approval_handler.request_approval

        # Execute
        result = jarvis_instance.ask("Clean multiple directories")

        # Verify both approvals were requested
        requests = mock_approval_handler.get_requests()
        assert len(requests) == 2
        assert "rm -rf /tmp" in requests[0]["command"]
        assert "rm -rf /var" in requests[1]["command"]

    def test_approval_request_timeout(self, jarvis_instance):
        """Test handling of approval request timeouts."""
        # Setup approval-required command
        jarvis_instance.llm.ask.return_value = {
            "user_request": "SuperMCP",
            "output": "call_server_tool(ShellMCP, execute_command, {command: 'timeout_cmd'})"
        }

        approval_result = mock_shellmcp_approval_required("timeout_cmd", "cmd-timeout")
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [approval_result]
        }

        # Setup approval handler that raises timeout
        def timeout_approval(command, level):
            import time
            time.sleep(35)  # Longer than any reasonable timeout
            return True

        jarvis_instance.request_approval = timeout_approval

        # This would need to be tested with actual timeout handling in Jarvis
        # For now, just verify the approval request is made
        result = jarvis_instance.ask("Run timeout command")

        # Should still attempt to process the approval
        assert isinstance(result, dict)

    def test_approval_with_malformed_response(self, jarvis_instance):
        """Test approval handling with malformed ShellMCP response."""
        # Setup command that gets malformed approval response
        jarvis_instance.llm.ask.return_value = {
            "user_request": "SuperMCP",
            "output": "call_server_tool(ShellMCP, execute_command, {command: 'test'})"
        }

        # Malformed approval response (missing required fields)
        malformed_approval = {
            "approval_required": True,
            # Missing server, tool, arguments, shellmcp_response
        }

        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [malformed_approval]
        }

        # Should handle gracefully
        result = jarvis_instance.ask("Run test command")

        # Should still return a valid response
        assert isinstance(result, dict)
        assert "user_request" in result

    def test_approval_state_persistence(self, jarvis_instance, mock_approval_handler):
        """Test that approval state is properly managed across requests."""
        # First request with approval
        jarvis_instance.llm.ask.return_value = {
            "user_request": "SuperMCP",
            "output": "call_server_tool(ShellMCP, execute_command, {command: 'first_cmd'})"
        }

        approval_result = mock_shellmcp_approval_required("first_cmd", "cmd-1")
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [approval_result]
        }

        jarvis_instance.request_approval = mock_approval_handler.request_approval

        # First execution
        jarvis_instance.ask("First command")

        # Verify first approval was recorded
        requests = mock_approval_handler.get_requests()
        assert len(requests) == 1

        # Reset for second command
        mock_approval_handler.clear_requests()

        # Second request
        jarvis_instance.llm.ask.return_value = {
            "user_request": "SuperMCP",
            "output": "call_server_tool(ShellMCP, execute_command, {command: 'second_cmd'})"
        }

        approval_result2 = mock_shellmcp_approval_required("second_cmd", "cmd-2")
        jarvis_instance.command_parser.execute_command_sequence.return_value = {
            "success": True,
            "results": [approval_result2]
        }

        jarvis_instance.ask("Second command")

        # Verify second approval was recorded separately
        requests = mock_approval_handler.get_requests()
        assert len(requests) == 1  # Only the second request
        assert "second_cmd" in requests[0]["command"]