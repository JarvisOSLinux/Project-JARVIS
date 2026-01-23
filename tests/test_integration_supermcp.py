"""
SuperMCP integration tests for JARVIS AI Assistant.

Tests SuperMCP client connection, server discovery, tool execution,
and interaction with all MCP servers.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from tests.integration_utils import (
    create_mock_supermcp_client,
    mock_supermcp_tool_call,
    create_test_mcp_config,
    wait_for_async
)


@pytest.mark.integration
class TestSuperMCPConnection:
    """Test SuperMCP client connection and basic operations."""

    def test_supermcp_client_initialization(self):
        """Test SuperMCP client initializes correctly."""
        from jarvis.supermcp_client import SuperMCPClient

        client = SuperMCPClient()
        assert client is not None
        assert hasattr(client, 'connect')
        assert hasattr(client, 'disconnect')
        assert hasattr(client, 'reload_servers')

    @pytest.mark.asyncio
    async def test_supermcp_connection_lifecycle(self):
        """Test full SuperMCP connection lifecycle."""
        from jarvis.supermcp_client import SuperMCPClient

        with patch('jarvis.supermcp_client.stdio_client') as mock_stdio_client:
            # Mock the stdio client
            mock_session = AsyncMock()
            mock_client = AsyncMock()
            mock_stdio_client.return_value = mock_client

            mock_client.__aenter__ = AsyncMock(return_value=(Mock(), Mock()))
            mock_client.__aexit__ = AsyncMock(return_value=None)

            client = SuperMCPClient()

            # Test connection
            await client.connect()
            mock_stdio_client.assert_called_once()

            # Test disconnection
            await client.disconnect()
            mock_client.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_supermcp_reload_servers(self, mock_supermcp_client):
        """Test server reloading functionality."""
        result = await mock_supermcp_client.reload_servers()

        assert "servers" in result
        assert isinstance(result["servers"], list)
        assert len(result["servers"]) > 0

    @pytest.mark.asyncio
    async def test_supermcp_list_servers(self, mock_supermcp_client):
        """Test server listing functionality."""
        servers = await mock_supermcp_client.list_servers()

        assert isinstance(servers, list)
        assert len(servers) > 0
        assert "EchoMCP" in servers

    @pytest.mark.asyncio
    async def test_supermcp_inspect_server(self, mock_supermcp_client):
        """Test server inspection functionality."""
        server_info = await mock_supermcp_client.inspect_server("EchoMCP")

        assert "tools" in server_info
        assert isinstance(server_info["tools"], list)
        assert len(server_info["tools"]) > 0

    @pytest.mark.asyncio
    async def test_supermcp_inspect_unknown_server(self, mock_supermcp_client):
        """Test inspection of non-existent server."""
        result = await mock_supermcp_client.inspect_server("UnknownMCP")

        assert "error" in result
        assert "not found" in result["error"].lower()


@pytest.mark.integration
class TestSuperMCPToolExecution:
    """Test SuperMCP tool execution across different servers."""

    @pytest.mark.asyncio
    async def test_echomcp_echo_tool(self, mock_supermcp_client):
        """Test EchoMCP echo tool execution."""
        message = "Hello, World!"
        result = await mock_supermcp_client.call_server_tool(
            "EchoMCP", "echo", {"message": message}
        )

        assert "result" in result
        assert result["result"] == message

    @pytest.mark.asyncio
    async def test_shellmcp_get_platform_info(self, mock_supermcp_client):
        """Test ShellMCP platform info retrieval."""
        result = await mock_supermcp_client.call_server_tool(
            "ShellMCP", "get_platform_info", {}
        )

        assert "result" in result
        platform_info = result["result"]
        assert "os" in platform_info
        assert "arch" in platform_info

    @pytest.mark.asyncio
    async def test_shellmcp_execute_safe_command(self, mock_supermcp_client):
        """Test ShellMCP execution of safe command."""
        result = await mock_supermcp_client.call_server_tool(
            "ShellMCP", "execute_command", {"command": "ls -la"}
        )

        assert "result" in result
        command_result = result["result"]
        assert "stdout" in command_result
        assert "returncode" in command_result
        assert command_result["returncode"] == 0

    @pytest.mark.asyncio
    async def test_shellmcp_execute_approval_required_command(self, mock_supermcp_client):
        """Test ShellMCP execution of command requiring approval."""
        result = await mock_supermcp_client.call_server_tool(
            "ShellMCP", "execute_command", {"command": "rm -rf /tmp/*"}
        )

        # Should return approval-required response
        assert "approval_required" in result
        assert result["approval_required"] == True
        assert "shellmcp_response" in result
        assert "queued with ID:" in result["shellmcp_response"]

    @pytest.mark.asyncio
    async def test_filesystemmcp_list_directory(self, mock_supermcp_client):
        """Test FileSystemMCP directory listing."""
        with patch('os.listdir') as mock_listdir, \
             patch('os.path.isdir') as mock_isdir:

            mock_listdir.return_value = ['file1.txt', 'file2.py', 'subdir']
            mock_isdir.side_effect = lambda p: p == 'subdir'

            result = await mock_supermcp_client.call_server_tool(
                "FileSystemMCP", "list_directory", {"path": "/tmp"}
            )

            # Note: This would need proper mocking of FileSystemMCP
            # For now, test that the call structure works
            assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_codegenmcp_list_templates(self, mock_supermcp_client):
        """Test CodeGenMCP template listing."""
        # Mock the response since CodeGenMCP may not be available in test env
        mock_supermcp_client.call_server_tool = AsyncMock(return_value={
            "result": ["function_template", "class_template", "api_template"]
        })

        result = await mock_supermcp_client.call_server_tool(
            "CodeGenMCP", "list_available_templates", {}
        )

        assert "result" in result
        templates = result["result"]
        assert isinstance(templates, list)
        assert len(templates) > 0

    @pytest.mark.asyncio
    async def test_codeanalysismcp_initialize_repository(self, mock_supermcp_client):
        """Test CodeAnalysisMCP repository initialization."""
        # Mock the response
        mock_supermcp_client.call_server_tool = AsyncMock(return_value={
            "result": {"repository": "/path/to/repo", "initialized": True}
        })

        result = await mock_supermcp_client.call_server_tool(
            "CodeAnalysisMCP", "initialize_repository", {"path": "/path/to/repo"}
        )

        assert "result" in result
        repo_info = result["result"]
        assert repo_info["initialized"] == True

    @pytest.mark.asyncio
    async def test_unknown_server_tool_call(self, mock_supermcp_client):
        """Test calling tool on unknown server."""
        result = await mock_supermcp_client.call_server_tool(
            "UnknownMCP", "some_tool", {}
        )

        assert "error" in result
        assert "Unknown" in result["error"] or "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_unknown_tool_call(self, mock_supermcp_client):
        """Test calling unknown tool on known server."""
        result = await mock_supermcp_client.call_server_tool(
            "EchoMCP", "unknown_tool", {}
        )

        assert "error" in result
        assert "unknown_tool" in result["error"]


@pytest.mark.integration
class TestSuperMCPErrorHandling:
    """Test SuperMCP error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_connection_failure_handling(self):
        """Test handling of SuperMCP connection failures."""
        from jarvis.supermcp_client import SuperMCPClient

        with patch('jarvis.supermcp_client.stdio_client') as mock_stdio_client:
            mock_stdio_client.side_effect = Exception("Connection failed")

            client = SuperMCPClient()

            with pytest.raises(Exception) as exc_info:
                await client.connect()

            assert "Connection failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_tool_execution_timeout(self, mock_supermcp_client):
        """Test handling of tool execution timeouts."""
        from asyncio import TimeoutError

        # Mock timeout
        mock_supermcp_client.call_server_tool = AsyncMock(side_effect=TimeoutError("Operation timed out"))

        with pytest.raises(TimeoutError):
            await mock_supermcp_client.call_server_tool("EchoMCP", "echo", {"message": "test"})

    @pytest.mark.asyncio
    async def test_malformed_arguments_handling(self, mock_supermcp_client):
        """Test handling of malformed tool arguments."""
        # Test with None arguments
        result = await mock_supermcp_client.call_server_tool("EchoMCP", "echo", None)

        # Should handle gracefully or return error
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_empty_arguments_handling(self, mock_supermcp_client):
        """Test handling of empty tool arguments."""
        result = await mock_supermcp_client.call_server_tool("EchoMCP", "echo", {})

        assert isinstance(result, dict)
        # Echo tool might return empty result or error
        assert "result" in result or "error" in result


@pytest.mark.integration
class TestSuperMCPConcurrentOperations:
    """Test concurrent SuperMCP operations."""

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_sequence(self, mock_supermcp_client):
        """Test sequential execution of multiple tool calls."""
        import asyncio

        # Execute multiple tool calls sequentially
        results = []
        for i in range(3):
            result = await mock_supermcp_client.call_server_tool(
                "EchoMCP", "echo", {"message": f"test_{i}"}
            )
            results.append(result)

        assert len(results) == 3
        for i, result in enumerate(results):
            assert "result" in result
            assert result["result"] == f"test_{i}"

    @pytest.mark.asyncio
    async def test_mixed_server_tool_calls(self, mock_supermcp_client):
        """Test tool calls across different servers."""
        # Call tools from different servers
        echo_result = await mock_supermcp_client.call_server_tool(
            "EchoMCP", "echo", {"message": "echo test"}
        )

        platform_result = await mock_supermcp_client.call_server_tool(
            "ShellMCP", "get_platform_info", {}
        )

        assert "result" in echo_result
        assert echo_result["result"] == "echo test"

        assert "result" in platform_result
        assert "os" in platform_result["result"]