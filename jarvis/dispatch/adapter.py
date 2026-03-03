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
import time
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

            logger.info(f"Dispatch: Spawning '{Config.DISPATCH_BINARY} serve'")
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
            logger.error(f"Dispatch: Connection failed (binary='{Config.DISPATCH_BINARY}'): {e}")
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
            logger.warning("Dispatch: send_tasks called but not connected")
            return {"error": "Not connected to dispatch"}

        logger.info(f"Dispatch: Sending {len(tasks)} task(s)")
        for i, task in enumerate(tasks):
            logger.info(f"Dispatch:   task[{i}]: server={task.get('server')}, tool={task.get('tool')}, params={task.get('params')}")

        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self.session.call_tool("dispatch", {"tasks": tasks}),
                timeout=self.timeout,
            )
            elapsed = time.perf_counter() - t0
            content = self._extract_content(result)
            logger.info(f"Dispatch: send_tasks completed in {elapsed:.2f}s — result: {content}")
            return content
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - t0
            logger.error(f"Dispatch: send_tasks timed out after {elapsed:.2f}s (limit={self.timeout}s)")
            return {"error": f"Dispatch timed out after {self.timeout}s"}
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.error(f"Dispatch: send_tasks failed after {elapsed:.2f}s — {e}")
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
            logger.warning("Dispatch: kill_tasks called but not connected")
            return {"error": "Not connected to dispatch"}

        logger.info(f"Dispatch: Killing PIDs {pids}")
        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self.session.call_tool("kill", {"pids": pids}),
                timeout=self.timeout,
            )
            elapsed = time.perf_counter() - t0
            content = self._extract_content(result)
            logger.info(f"Dispatch: kill_tasks completed in {elapsed:.2f}s — result: {content}")
            return content
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - t0
            logger.error(f"Dispatch: kill_tasks timed out after {elapsed:.2f}s (limit={self.timeout}s)")
            return {"error": f"Kill timed out after {self.timeout}s"}
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.error(f"Dispatch: kill_tasks failed after {elapsed:.2f}s — {e}")
            return {"error": f"Failed to kill tasks: {e}"}

    async def set_timer(
        self, label: str, duration: int, metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Set a one-shot timer in dispatch.

        Args:
            label: Human-readable label for the signal window.
            duration: Seconds until REMIND fires.
            metadata: Opaque key-value data passed through in signals.

        Returns:
            Dict with assigned PID and status, or error.
        """
        if not self._connected:
            logger.warning("Dispatch: set_timer called but not connected")
            return {"error": "Not connected to dispatch"}

        logger.info(f"Dispatch: Setting timer label='{label}', duration={duration}s, metadata={metadata}")
        params: Dict[str, Any] = {"label": label, "duration": duration}
        if metadata is not None:
            params["metadata"] = metadata

        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self.session.call_tool("timer", params),
                timeout=self.timeout,
            )
            elapsed = time.perf_counter() - t0
            content = self._extract_content(result)
            logger.info(f"Dispatch: set_timer completed in {elapsed:.2f}s — result: {content}")
            return content
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - t0
            logger.error(f"Dispatch: set_timer timed out after {elapsed:.2f}s (limit={self.timeout}s)")
            return {"error": f"Timer call timed out after {self.timeout}s"}
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.error(f"Dispatch: set_timer failed after {elapsed:.2f}s — {e}")
            return {"error": f"Failed to set timer: {e}"}

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
                signals = content
            elif isinstance(content, dict):
                signals = content.get("signals", [])
            else:
                signals = []

            if signals:
                logger.debug(f"Dispatch: Signal window returned {len(signals)} signal(s)")
                for sig in signals:
                    logger.debug(f"Dispatch:   signal: type={sig.get('type')}, pid={sig.get('pid')}, data={sig.get('data', '')}")
            return signals
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
            try:
                return json.loads(combined)
            except (json.JSONDecodeError, ValueError):
                logger.debug(f"Dispatch: MCP result was not JSON, using raw text ({len(combined)} chars)")
                return combined
        logger.debug(f"Dispatch: MCP result had no content, returning str representation")
        return str(result)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
