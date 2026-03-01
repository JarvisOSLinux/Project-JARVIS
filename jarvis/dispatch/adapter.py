"""
DispatchAdapter — MCP client that connects to the dispatch binary.

dispatch is a Rust-based concurrent task orchestrator. It exposes itself
as an MCP server via `dispatch serve`. This adapter connects to it over
stdio and provides methods to send task batches, kill tasks, and receive
signals.

The adapter is intentionally thin — it translates Python calls into MCP
tool invocations and surfaces results. All decision-making lives in Jarvis.
"""

import asyncio
from typing import Dict, Any, Optional, List
from ..config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)


class DispatchAdapter:
    """Async MCP client for the dispatch binary."""

    def __init__(self):
        self.timeout = Config.DISPATCH_TIMEOUT
        self.session = None
        self._client = None
        self._connected = False

    async def connect(self):
        """Connect to the dispatch binary via stdio MCP."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=Config.DISPATCH_BINARY,
                args=["serve"],
            )
            self._client = stdio_client(params)
            read, write = await self._client.__aenter__()
            self.session = ClientSession(read, write)
            await self.session.initialize()
            self._connected = True
            logger.info("Dispatch: Connected successfully")
        except Exception as e:
            logger.error(f"Dispatch: Connection failed: {e}")
            raise

    async def disconnect(self):
        """Disconnect from dispatch."""
        try:
            if self.session:
                await self.session.close()
            if self._client:
                await self._client.__aexit__(None, None, None)
            self._connected = False
            logger.info("Dispatch: Disconnected")
        except Exception as e:
            logger.error(f"Dispatch: Disconnect error: {e}")

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def send_tasks(self, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Send a batch of tasks to dispatch for concurrent execution.

        Args:
            tasks: List of task dicts, each with:
                - server: MCP server name
                - tool: tool name on that server
                - params: dict of arguments
                - remind_after: (optional) seconds before a REMIND signal

        Returns:
            Dict with assigned PIDs and status.
        """
        if not self._connected:
            return {"error": "Not connected to dispatch"}

        try:
            result = await asyncio.wait_for(
                self.session.call_tool("dispatch", {"tasks": tasks}),
                timeout=self.timeout,
            )
            return self._extract_content(result)
        except asyncio.TimeoutError:
            return {"error": f"Dispatch timed out after {self.timeout}s"}
        except Exception as e:
            return {"error": f"Failed to dispatch tasks: {e}"}

    async def kill_tasks(self, pids: List[int]) -> Dict[str, Any]:
        """
        Kill running tasks by PID.

        Args:
            pids: List of task PIDs to terminate.

        Returns:
            Dict with kill confirmation.
        """
        if not self._connected:
            return {"error": "Not connected to dispatch"}

        try:
            result = await asyncio.wait_for(
                self.session.call_tool("kill", {"pids": pids}),
                timeout=self.timeout,
            )
            return self._extract_content(result)
        except asyncio.TimeoutError:
            return {"error": f"Kill timed out after {self.timeout}s"}
        except Exception as e:
            return {"error": f"Failed to kill tasks: {e}"}

    async def get_signal_window(self) -> List[Dict[str, Any]]:
        """
        Get the current signal window from dispatch.

        Returns:
            List of signal dicts (up to 20), each with:
                - pid: task PID
                - type: INIT | EXIT | REMIND | WAIT | KILL
                - timestamp: ISO string
                - data: (optional) output or error payload
        """
        if not self._connected:
            return []

        try:
            result = await asyncio.wait_for(
                self.session.call_tool("signals", {}),
                timeout=self.timeout,
            )
            content = self._extract_content(result)
            if isinstance(content, list):
                return content
            return content.get("signals", []) if isinstance(content, dict) else []
        except Exception as e:
            logger.error(f"Dispatch: Failed to get signals: {e}")
            return []

    def _extract_content(self, result) -> Any:
        """Extract content from MCP result."""
        if hasattr(result, 'structuredContent') and result.structuredContent is not None:
            return result.structuredContent
        elif hasattr(result, 'content') and result.content:
            import json
            texts = []
            for block in result.content:
                if hasattr(block, 'text') and block.text:
                    texts.append(block.text)
            combined = "\n".join(texts) if texts else str(result)
            # Try to parse as JSON
            try:
                return json.loads(combined)
            except (json.JSONDecodeError, ValueError):
                return combined
        return str(result)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
