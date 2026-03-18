"""
Confirmation Manager — Tool-Level Action (TLA) confirmation gate.

MCP server developers declare ``confirmation_required: true`` on tools that
need user approval before execution.  The ConfirmationManager reads that
metadata at dispatch time and gates execution accordingly.

Three delivery channels, chosen automatically by availability:

1. **Desktop notification** — ``notify-send`` (Linux) with Allow / Deny
   actions.  Skipped when ``notification_silent`` is True.
2. **Socket** — structured JSON on the JARVIS output socket so external
   apps can render their own confirmation UI.
3. **CLI / TTY** — ``[y/N]`` stdin prompt as a last-resort fallback.

The global ``CONFIRMATION_MODE`` (config) controls overall behaviour:

* ``allow_all``  — skip confirmation, always approve
* ``smart``      — only ask when the tool sets ``confirmation_required``
* ``ask_all``    — ask for every tool call regardless of metadata
"""

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional

from ..config import Config
from .logger import get_logger

logger = get_logger(__name__)

# How long to wait for a user response before denying (seconds).
DEFAULT_TIMEOUT = 30


class ConfirmationManager:
    """Gate tool execution behind user confirmation."""

    def __init__(self) -> None:
        # Pending confirmation futures keyed by request id.
        self._pending: Dict[str, asyncio.Future] = {}
        # External output callback (set by Jarvis to broadcast via socket).
        self._output_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        # External output clients list for socket-based confirmation
        self._has_socket_clients: Optional[Callable[[], bool]] = None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def set_output_callback(
        self,
        callback: Callable[[Dict[str, Any]], None],
        has_clients: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Register the output socket broadcast callback.

        ``has_clients`` should return True when at least one socket
        subscriber is connected (so we know whether to use the socket
        channel).
        """
        self._output_callback = callback
        self._has_socket_clients = has_clients

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request_confirmation(
        self,
        tool: str,
        summary: str,
        params: Dict[str, Any],
        notification_silent: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> bool:
        """Ask the user to approve a tool invocation.

        Returns ``True`` if approved, ``False`` if denied or timed-out.
        """
        request_id = str(uuid.uuid4())[:8]

        logger.info(
            f"Confirmation requested: tool={tool}, id={request_id}"
        )

        # Try channels in priority order.
        result = await self._try_channels(
            request_id=request_id,
            tool=tool,
            summary=summary,
            params=params,
            notification_silent=notification_silent,
            timeout=timeout,
        )

        logger.info(
            f"Confirmation result: id={request_id}, approved={result}"
        )
        return result

    def should_confirm(self, tool_metadata: Dict[str, Any]) -> bool:
        """Decide whether a tool invocation needs confirmation.

        Uses ``CONFIRMATION_MODE`` and the tool's ``confirmation_required``
        field.
        """
        mode = Config.CONFIRMATION_MODE

        if mode == "allow_all":
            return False
        if mode == "ask_all":
            return True

        # "smart" — respect the tool's own declaration.
        return bool(tool_metadata.get("confirmation_required", False))

    def handle_confirmation_response(self, response: Dict[str, Any]) -> None:
        """Process an incoming confirmation response (from socket / IPC).

        Expected format::

            {"type": "confirmation_response",
             "id": "abc123",
             "approved": true}
        """
        req_id = response.get("id")
        approved = response.get("approved", False)

        if req_id and req_id in self._pending:
            future = self._pending.pop(req_id)
            if not future.done():
                future.set_result(bool(approved))
            logger.debug(f"Confirmation response: id={req_id}, approved={approved}")
        else:
            logger.warning(
                f"Received confirmation response for unknown id: {req_id}"
            )

    # ------------------------------------------------------------------
    # Channel implementations
    # ------------------------------------------------------------------

    async def _try_channels(
        self,
        request_id: str,
        tool: str,
        summary: str,
        params: Dict[str, Any],
        notification_silent: bool,
        timeout: float,
    ) -> bool:
        """Try each confirmation channel in priority order."""

        # 1. Desktop notification (unless silent).
        if not notification_silent and self._has_desktop_notifications():
            return await self._confirm_desktop(
                request_id, tool, summary, timeout,
            )

        # 2. Socket — if clients are connected.
        if self._output_callback and self._has_socket_clients and self._has_socket_clients():
            return await self._confirm_socket(
                request_id, tool, summary, params, timeout,
            )

        # 3. CLI / TTY fallback.
        if self._has_tty():
            return await self._confirm_cli(tool, summary, timeout)

        # No channel available — deny by default (safe).
        logger.warning("No confirmation channel available, denying by default")
        return False

    # -- Desktop (notify-send) -----------------------------------------

    @staticmethod
    def _has_desktop_notifications() -> bool:
        """Check whether desktop notifications are available."""
        return shutil.which("notify-send") is not None

    async def _confirm_desktop(
        self,
        request_id: str,
        tool: str,
        summary: str,
        timeout: float,
    ) -> bool:
        """Use ``notify-send`` with actions for confirmation.

        ``notify-send`` (libnotify) supports ``--action`` flags.  The
        process blocks until the user clicks an action or the notification
        expires, printing the chosen action key to stdout.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "notify-send",
                "--app-name=JARVIS",
                f"--expire-time={int(timeout * 1000)}",
                "--action=allow=Allow",
                "--action=deny=Deny",
                "JARVIS — Confirmation Required",
                f"Tool: {tool}\n{summary}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout + 2,
                )
                action = stdout.decode().strip()
                return action == "allow"
            except asyncio.TimeoutError:
                proc.kill()
                return False

        except FileNotFoundError:
            logger.debug("notify-send not found, falling back")
            return False
        except Exception as e:
            logger.warning(f"Desktop notification failed: {e}")
            return False

    # -- Socket --------------------------------------------------------

    async def _confirm_socket(
        self,
        request_id: str,
        tool: str,
        summary: str,
        params: Dict[str, Any],
        timeout: float,
    ) -> bool:
        """Send confirmation request over the output socket and wait."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[request_id] = future

        # Broadcast the request to connected clients.
        message = {
            "type": "confirmation_request",
            "id": request_id,
            "tool": tool,
            "summary": summary,
            "params": params,
            "timeout": timeout,
        }

        try:
            self._output_callback(message)
        except Exception as e:
            logger.warning(f"Failed to send confirmation via socket: {e}")
            self._pending.pop(request_id, None)
            return False

        # Wait for the response (or timeout).
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            logger.info(f"Confirmation timed out: id={request_id}")
            return False

    # -- CLI / TTY -----------------------------------------------------

    @staticmethod
    def _has_tty() -> bool:
        return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()

    async def _confirm_cli(
        self,
        tool: str,
        summary: str,
        timeout: float,
    ) -> bool:
        """Prompt the user on stdin with ``[y/N]``."""
        prompt = (
            f"\n[JARVIS] Confirmation required\n"
            f"  Tool:   {tool}\n"
            f"  Action: {summary}\n"
            f"  Approve? [y/N]: "
        )
        try:
            answer = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, input, prompt),
                timeout=timeout,
            )
            return answer.strip().lower() in ("y", "yes")
        except (asyncio.TimeoutError, EOFError):
            return False
