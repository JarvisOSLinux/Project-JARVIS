"""
Command parser integration tests for JARVIS AI Assistant.

Tests command parsing, argument parsing, command sequences, and
integration with SuperMCP wrapper.
"""

import pytest
from unittest.mock import Mock, patch
from tests.integration_utils import (
    mock_supermcp_tool_call,
    assert_supermcp_command_format,
    create_mock_supermcp_client
)


@pytest.mark.integration
class TestCommandParsing:
    """Test SuperMCP command parsing functionality."""

    def test_parse_reload_servers_command(self):
        """Test parsing reload_servers() command."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock reload_servers call
        supermcp.reload_servers = Mock(return_value={"servers": ["EchoMCP"]})

        result = parser._parse_and_execute_command("reload_servers()")

        assert "servers" in result
        assert "EchoMCP" in result["servers"]
        supermcp.reload_servers.assert_called_once()

    def test_parse_list_servers_command(self):
        """Test parsing list_servers() command."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock list_servers call
        supermcp.list_servers = Mock(return_value=["EchoMCP", "ShellMCP"])

        result = parser._parse_and_execute_command("list_servers()")

        assert isinstance(result, list)
        assert "EchoMCP" in result
        assert "ShellMCP" in result
        supermcp.list_servers.assert_called_once()

    def test_parse_inspect_server_command(self):
        """Test parsing inspect_server(server_name) command."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock inspect_server call
        supermcp.inspect_server = Mock(return_value={"tools": ["echo"]})

        result = parser._parse_and_execute_command("inspect_server(EchoMCP)")

        assert "tools" in result
        assert "echo" in result["tools"]
        supermcp.inspect_server.assert_called_once_with("EchoMCP")

    def test_parse_call_server_tool_simple(self):
        """Test parsing simple call_server_tool command."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock tool call
        supermcp.call_server_tool = Mock(return_value={"result": "echoed"})

        result = parser._parse_and_execute_command('call_server_tool(EchoMCP, echo, {"message": "test"})')

        assert "result" in result
        assert result["result"] == "echoed"
        supermcp.call_server_tool.assert_called_once_with("EchoMCP", "echo", {"message": "test"})

    def test_parse_call_server_tool_complex_args(self):
        """Test parsing call_server_tool with complex arguments."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock tool call
        supermcp.call_server_tool = Mock(return_value={"result": "success"})

        command = 'call_server_tool(ShellMCP, execute_command, {"command": "ls -la", "timeout": 30})'
        result = parser._parse_and_execute_command(command)

        assert "result" in result
        supermcp.call_server_tool.assert_called_once_with(
            "ShellMCP",
            "execute_command",
            {"command": "ls -la", "timeout": 30}
        )

    def test_parse_unknown_command(self):
        """Test parsing unknown command."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        result = parser._parse_and_execute_command("unknown_command()")

        assert "error" in result
        assert "Unknown command" in result["error"]

    def test_parse_malformed_command(self):
        """Test parsing malformed commands."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        malformed_commands = [
            "call_server_tool",  # Missing parentheses
            "call_server_tool(EchoMCP)",  # Missing arguments
            "call_server_tool(EchoMCP, echo)",  # Missing arguments
            "reload_servers",  # Missing parentheses
            "",  # Empty command
        ]

        for command in malformed_commands:
            result = parser._parse_and_execute_command(command)
            assert "error" in result or result == {"error": f"Unknown command: {command}"}


@pytest.mark.integration
class TestCommandSequences:
    """Test parsing and execution of command sequences."""

    def test_single_command_sequence(self):
        """Test executing a single command in sequence."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock reload_servers
        supermcp.reload_servers = Mock(return_value={"servers": ["EchoMCP"]})

        result = parser.execute_command_sequence("reload_servers()")

        assert result["success"] == True
        assert len(result["results"]) == 1
        assert "servers" in result["results"][0]
        supermcp.reload_servers.assert_called_once()

    def test_multiple_command_sequence(self):
        """Test executing multiple commands in sequence."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock commands
        supermcp.reload_servers = Mock(return_value={"servers": ["EchoMCP"]})
        supermcp.list_servers = Mock(return_value=["EchoMCP", "ShellMCP"])

        command_sequence = "reload_servers(); list_servers()"
        result = parser.execute_command_sequence(command_sequence)

        assert result["success"] == True
        assert len(result["results"]) == 2

        # Check first command result
        assert "servers" in result["results"][0]

        # Check second command result
        assert isinstance(result["results"][1], list)
        assert "EchoMCP" in result["results"][1]

        supermcp.reload_servers.assert_called_once()
        supermcp.list_servers.assert_called_once()

    def test_command_sequence_with_whitespace(self):
        """Test command sequences with extra whitespace."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock commands
        supermcp.reload_servers = Mock(return_value={"servers": ["EchoMCP"]})
        supermcp.list_servers = Mock(return_value=["EchoMCP"])

        # Command with various whitespace
        command_sequence = "  reload_servers( )  ;   list_servers()  ;  "
        result = parser.execute_command_sequence(command_sequence)

        assert result["success"] == True
        assert len(result["results"]) == 2
        supermcp.reload_servers.assert_called_once()
        supermcp.list_servers.assert_called_once()

    def test_command_sequence_partial_failure(self):
        """Test command sequence where some commands fail."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock commands - first succeeds, second fails
        supermcp.reload_servers = Mock(return_value={"servers": ["EchoMCP"]})

        # Make second command fail by not mocking it properly
        def mock_call_server_tool(*args, **kwargs):
            raise Exception("Tool not found")

        supermcp.call_server_tool = Mock(side_effect=mock_call_server_tool)

        command_sequence = "reload_servers(); call_server_tool(BadMCP, bad_tool, {})"
        result = parser.execute_command_sequence(command_sequence)

        assert result["success"] == False  # Overall failure
        assert len(result["results"]) == 2
        assert "servers" in result["results"][0]  # First command succeeded
        assert "error" in result["results"][1]   # Second command failed

    def test_empty_command_sequence(self):
        """Test empty command sequence."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        result = parser.execute_command_sequence("")

        assert result["success"] == True
        assert result["results"] == []

    def test_command_sequence_with_only_semicolons(self):
        """Test command sequence with only semicolons."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        result = parser.execute_command_sequence(";;;")

        assert result["success"] == True
        assert result["results"] == []


@pytest.mark.integration
class TestArgumentParsing:
    """Test JSON-like argument parsing in commands."""

    def test_parse_simple_json_args(self):
        """Test parsing simple JSON arguments."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Test parsing arguments from command string
        content = 'EchoMCP, echo, {"message": "hello"}'
        parts = parser._parse_command_arguments(content)

        assert len(parts) == 3
        assert parts[0] == "EchoMCP"
        assert parts[1] == "echo"
        assert parts[2] == '{"message": "hello"}'

        # Test that args get parsed as JSON
        args_str = parts[2]
        args = parser._parse_json_like_args(args_str)
        assert args == {"message": "hello"}

    def test_parse_complex_json_args(self):
        """Test parsing complex JSON arguments."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        args_str = '{"command": "ls -la", "timeout": 30, "working_dir": "/tmp"}'
        args = parser._parse_json_like_args(args_str)

        expected = {
            "command": "ls -la",
            "timeout": 30,
            "working_dir": "/tmp"
        }
        assert args == expected

    def test_parse_nested_json_args(self):
        """Test parsing nested JSON arguments."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        args_str = '{"config": {"host": "localhost", "port": 8080}, "enabled": true}'
        args = parser._parse_json_like_args(args_str)

        expected = {
            "config": {"host": "localhost", "port": 8080},
            "enabled": True
        }
        assert args == expected

    def test_parse_empty_args(self):
        """Test parsing empty arguments."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        args = parser._parse_json_like_args("{}")
        assert args == {}

        args = parser._parse_json_like_args("")
        assert args == {}

    def test_parse_malformed_json_args(self):
        """Test parsing malformed JSON arguments."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        malformed_args = [
            '{"missing": "closing"',
            '{invalid: "json"}',
            'not json at all',
            '{"trailing": "comma",}',
        ]

        for malformed in malformed_args:
            # Should handle gracefully, possibly returning empty dict or raising exception
            try:
                args = parser._parse_json_like_args(malformed)
                # If it succeeds, should be a dict
                assert isinstance(args, dict)
            except (ValueError, json.JSONDecodeError):
                # Expected for malformed JSON
                pass


@pytest.mark.integration
class TestApprovalDetection:
    """Test detection and handling of approval-required commands."""

    def test_detect_shellmcp_approval_required(self):
        """Test detection of ShellMCP approval-required response."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock ShellMCP tool call returning approval-required response
        approval_response = {
            "server": "ShellMCP",
            "tool": "execute_command",
            "arguments": {"command": "rm -rf /"},
            "result": {
                "success": False,
                "message": "This command requires approval. It has been queued with ID: cmd-123"
            }
        }

        supermcp.call_server_tool = Mock(return_value=approval_response)

        command = 'call_server_tool(ShellMCP, execute_command, {"command": "rm -rf /"})'
        result = parser._parse_and_execute_command(command)

        # Should detect approval requirement
        approval_detected = parser._is_approval_required(result)
        assert approval_detected == True

    def test_detect_no_approval_required(self):
        """Test detection when no approval is required."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock successful tool call
        success_response = {
            "server": "EchoMCP",
            "tool": "echo",
            "arguments": {"message": "test"},
            "result": "test"
        }

        supermcp.call_server_tool = Mock(return_value=success_response)

        command = 'call_server_tool(EchoMCP, echo, {"message": "test"})'
        result = parser._parse_and_execute_command(command)

        # Should not detect approval requirement
        approval_detected = parser._is_approval_required(result)
        assert approval_detected == False

    def test_extract_command_id_from_response(self):
        """Test extracting command ID from approval response."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        response_text = "This command requires approval. It has been queued with ID: abc-123-def"
        command_id = parser._extract_command_id(response_text)

        assert command_id == "abc-123-def"

    def test_extract_command_id_various_formats(self):
        """Test extracting command ID from various response formats."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        test_cases = [
            ("Command queued with ID: simple-id", "simple-id"),
            ("ID: complex-uuid-123", "complex-uuid-123"),
            ("queued with ID: 12345", "12345"),
            ("no id mentioned", None),
            ("", None),
        ]

        for response_text, expected_id in test_cases:
            command_id = parser._extract_command_id(response_text)
            assert command_id == expected_id


@pytest.mark.integration
class TestCommandParserErrorHandling:
    """Test command parser error handling."""

    def test_command_execution_error_handling(self):
        """Test handling of command execution errors."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Mock command that raises exception
        supermcp.reload_servers = Mock(side_effect=Exception("Network error"))

        result = parser._parse_and_execute_command("reload_servers()")

        assert "error" in result
        assert "Network error" in result["error"]

    def test_sequence_execution_error_handling(self):
        """Test handling of errors in command sequences."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # First command succeeds, second fails
        supermcp.reload_servers = Mock(return_value={"servers": ["EchoMCP"]})
        supermcp.list_servers = Mock(side_effect=Exception("Connection lost"))

        result = parser.execute_command_sequence("reload_servers(); list_servers()")

        assert result["success"] == False  # Overall failure due to second command
        assert len(result["results"]) == 2
        assert "servers" in result["results"][0]  # First succeeded
        assert "error" in result["results"][1]   # Second failed

    def test_invalid_json_argument_error(self):
        """Test handling of invalid JSON in arguments."""
        from jarvis.core.command_parser import SuperMCPCommandParser

        supermcp = Mock()
        parser = SuperMCPCommandParser(supermcp)

        # Command with invalid JSON
        command = 'call_server_tool(EchoMCP, echo, {invalid: json})'
        result = parser._parse_and_execute_command(command)

        # Should handle the error gracefully
        assert isinstance(result, dict)
        # May return error or succeed depending on implementation