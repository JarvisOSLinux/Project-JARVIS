"""
SuperMCP Client - Persistent subprocess-based communication

This maintains a persistent connection to SuperMCP for efficient
repeated calls. The subprocess is started once and reused for all
requests until explicitly disconnected or the process dies.
"""

import asyncio
import atexit
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List
from .config import Config
from .core.logger import get_logger

logger = get_logger(__name__)


class SuperMCPClient:
    """
    Persistent SuperMCP client using subprocess.

    Maintains a long-running connection to SuperMCP for efficient
    repeated tool calls. Automatically reconnects if the process dies.
    """

    def __init__(self):
        # Use config path or fallback to default
        config_path = Config.SUPERMCP_SERVER_PATH
        if not Path(config_path).is_absolute():
            self.supermcp_path = Path(__file__).parent / config_path
        else:
            self.supermcp_path = Path(config_path)

        self.timeout = Config.SUPERMCP_TIMEOUT
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._connected = False

    def _next_id(self) -> int:
        """Get next request ID (thread-safe)"""
        with self._lock:
            self._request_id += 1
            return self._request_id

    def is_connected(self) -> bool:
        """Check if SuperMCP process is running"""
        return (
            self._connected and
            self._process is not None and
            self._process.poll() is None
        )

    def connect(self) -> bool:
        """
        Connect to SuperMCP server (synchronous).

        Returns True if connected successfully, False otherwise.
        """
        if self.is_connected():
            logger.debug("SuperMCP: Already connected")
            return True

        try:
            logger.debug(f"SuperMCP: Starting process: {self.supermcp_path}")

            # Start SuperMCP as subprocess
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW

            self._process = subprocess.Popen(
                [sys.executable, str(self.supermcp_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                creationflags=creationflags
            )

            # Send initialize request
            init_response = self._send_request_sync(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "jarvis", "version": "1.0"}
                }
            )

            if isinstance(init_response, dict) and "error" in init_response:
                raise RuntimeError(f"Initialize failed: {init_response['error']}")

            # Send initialized notification
            self._send_notification_sync("notifications/initialized")

            self._connected = True
            logger.info("SuperMCP: Connected successfully!")
            return True

        except Exception as e:
            logger.error(f"SuperMCP: Connection failed: {e}")
            self._cleanup_process()
            return False

    def disconnect(self):
        """Disconnect from SuperMCP server"""
        if self._process:
            logger.debug("SuperMCP: Disconnecting...")
            self._cleanup_process()
            logger.info("SuperMCP: Disconnected")

    def _cleanup_process(self):
        """Clean up the subprocess"""
        self._connected = False
        if self._process:
            try:
                self._process.stdin.close()
            except:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except:
                try:
                    self._process.kill()
                except:
                    pass
            self._process = None

    def _ensure_connected(self) -> bool:
        """Ensure we're connected, reconnecting if necessary"""
        if not self.is_connected():
            logger.debug("SuperMCP: Not connected, attempting to connect...")
            return self.connect()
        return True

    def _send_notification_sync(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC notification (no response expected)"""
        if not self._process or self._process.poll() is not None:
            return

        notification = {"jsonrpc": "2.0", "method": method}
        if params:
            notification["params"] = params

        try:
            notification_json = json.dumps(notification) + "\n"
            self._process.stdin.write(notification_json.encode())
            self._process.stdin.flush()
            logger.debug(f"SuperMCP: Sent notification: {method}")
        except Exception as e:
            logger.error(f"SuperMCP: Failed to send notification: {e}")

    def _send_request_sync(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a JSON-RPC request and wait for response (synchronous)"""
        if not self._process or self._process.poll() is not None:
            return {"error": "SuperMCP process not running"}

        request_id = self._next_id()
        request = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params:
            request["params"] = params

        try:
            request_json = json.dumps(request) + "\n"
            logger.debug(f"SuperMCP: Sending: {method}")

            self._process.stdin.write(request_json.encode())
            self._process.stdin.flush()

            # Read response line (blocking)
            response_line = self._process.stdout.readline()
            response_str = response_line.decode().strip()

            if not response_str:
                return {"error": "Empty response from SuperMCP"}

            response = json.loads(response_str)

            if "error" in response:
                return {"error": response["error"]}

            return response.get("result", response)

        except Exception as e:
            logger.error(f"SuperMCP: Request failed: {e}")
            # Mark as disconnected so next call will reconnect
            self._connected = False
            return {"error": str(e)}

    def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a SuperMCP tool"""
        if not self._ensure_connected():
            return {"error": "Failed to connect to SuperMCP"}

        return self._send_request_sync("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })

    def reload_servers(self) -> Dict[str, Any]:
        """Reload available MCP servers"""
        result = self._call_tool("reload_servers", {})
        return self._extract_content(result)

    def list_servers(self) -> List[Dict[str, Any]]:
        """List all available MCP servers"""
        result = self._call_tool("list_servers", {})
        return self._extract_content(result)

    def inspect_server(self, server_name: str) -> Dict[str, Any]:
        """Inspect a specific MCP server's capabilities"""
        result = self._call_tool("inspect_server", {"name": server_name})
        return self._extract_content(result)

    def call_server_tool(self, server_name: str, tool_name: str,
                         arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call a tool from a specific MCP server"""
        result = self._call_tool("call_server_tool", {
            "name": server_name,
            "tool_name": tool_name,
            "arguments": arguments or {}
        })
        return self._extract_content(result)

    def _extract_content(self, result: Any) -> Any:
        """Extract content from MCP result"""
        if isinstance(result, dict):
            if "error" in result:
                return result

            # Handle MCP content array format
            if "content" in result:
                content = result["content"]
                if isinstance(content, list):
                    texts = []
                    for item in content:
                        if isinstance(item, dict) and "text" in item:
                            texts.append(item["text"])
                    if texts:
                        combined = "\n".join(texts)
                        try:
                            return json.loads(combined)
                        except:
                            return combined
                return content

            return result

        return result


# Global singleton instance for persistent connection
_global_client: Optional[SuperMCPClient] = None
_global_lock = threading.Lock()


def get_supermcp_client() -> SuperMCPClient:
    """Get the global SuperMCP client instance (singleton)"""
    global _global_client
    with _global_lock:
        if _global_client is None:
            _global_client = SuperMCPClient()
            # Register cleanup on exit
            atexit.register(_cleanup_global_client)
        return _global_client


def _cleanup_global_client():
    """Clean up the global client on exit"""
    global _global_client
    if _global_client:
        logger.debug("SuperMCP: Cleaning up global client on exit")
        _global_client.disconnect()
        _global_client = None


class SuperMCPWrapper:
    """
    Synchronous wrapper around SuperMCPClient.

    Uses a persistent connection to SuperMCP for efficient repeated calls.
    The connection is automatically established on first use and maintained
    until the process exits.
    """

    def __init__(self):
        # Use the global singleton client
        self.client = get_supermcp_client()

    def reload_servers(self) -> Dict[str, Any]:
        """Reload available MCP servers"""
        logger.debug("SuperMCPWrapper: reload_servers called")
        return self.client.reload_servers()

    def list_servers(self) -> List[Dict[str, Any]]:
        """List all available MCP servers"""
        logger.debug("SuperMCPWrapper: list_servers called")
        return self.client.list_servers()

    def inspect_server(self, server_name: str) -> Dict[str, Any]:
        """Inspect a specific MCP server's capabilities"""
        logger.debug(f"SuperMCPWrapper: inspect_server({server_name}) called")
        return self.client.inspect_server(server_name)

    def call_server_tool(self, server_name: str, tool_name: str,
                         arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call a tool from a specific MCP server"""
        logger.debug(f"SuperMCPWrapper: call_server_tool({server_name}, {tool_name}) called")
        return self.client.call_server_tool(server_name, tool_name, arguments)

    def disconnect(self):
        """Explicitly disconnect (usually not needed)"""
        self.client.disconnect()

    def is_connected(self) -> bool:
        """Check if connected to SuperMCP"""
        return self.client.is_connected()
