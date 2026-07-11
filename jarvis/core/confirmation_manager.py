"""
Confirmation Manager — Threat Level Access (TLA) confirmation gate.

MCP server developers declare ``confirmation_required: true`` on tools that
need user approval before execution.  The ConfirmationManager reads that
metadata at dispatch time and gates execution accordingly.

**Non-blocking / event-driven design:**

The manager never ``await``s a user response.  Instead it:

1. Stashes pending tasks with their confirmation id.
2. Sends a notification (desktop / socket / CLI prompt) — fire-and-forget.
3. Returns immediately so the event loop stays free for signals, reminders,
   and new user input.
4. When the user responds, the response arrives as a
   ``CONFIRMATION_RESPONSE`` event through the EventMerger.
5. Jarvis calls ``resolve()`` which returns the stashed tasks and approval
   status, and Jarvis resumes the dispatch.

Three delivery channels, chosen automatically by availability:

1. **Desktop notification** — ``notify-send`` (Linux) with Allow / Deny
   actions.  Skipped when ``notification_silent`` is True.
2. **Socket** — structured JSON on the JARVIS output socket so external
   apps can render their own confirmation UI.
3. **CLI / TTY** — ``[y/N]`` stdin prompt (runs in executor, response
   injected back via EventMerger).

The global ``CONFIRMATION_MODE`` (config) controls overall behaviour:

* ``allow_all``  — skip confirmation, always approve
* ``smart``      — only ask when the tool sets ``confirmation_required``
* ``ask_all``    — ask for every tool call regardless of metadata
"""

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..config import Config
from .logger import get_logger
from .threat_level import ThreatLevel, classify

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 30


@dataclass
class PendingConfirmation:
    """A set of tasks awaiting user approval."""

    request_id: str
    tasks: List[Dict[str, Any]]
    denied_tools: List[str] = field(default_factory=list)
    approved_tasks: List[Dict[str, Any]] = field(default_factory=list)
    # Dispatch sub-chain context to resume after confirmation.
    dispatch_context: Optional[Dict[str, Any]] = None
    # Retained so a later list_pending() query can describe what's waiting --
    # the original request only computes these locally otherwise.
    tool_names: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


class ConfirmationManager:
    """Non-blocking, event-driven tool confirmation gate."""

    def __init__(self) -> None:
        # Pending confirmations keyed by request id.
        self._pending: Dict[str, PendingConfirmation] = {}
        # External output callback (set by Jarvis to broadcast via socket).
        self._output_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._has_socket_clients: Optional[Callable[[], bool]] = None
        # Injector for confirmation responses into the event loop.
        self._inject_confirmation: Optional[Callable[[Dict[str, Any]], None]] = None
        # Timeout tasks keyed by request id (so we can cancel on response).
        self._timeout_tasks: Dict[str, asyncio.Task] = {}
        # TUI callback — async callable that pushes ConfirmModal and returns bool.
        self._tui_callback: Optional[Callable[[str, List[str]], Any]] = None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def set_output_callback(
        self,
        callback: Callable[[Dict[str, Any]], None],
        has_clients: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Register the output socket broadcast callback."""
        self._output_callback = callback
        self._has_socket_clients = has_clients

    def set_tui_callback(
        self,
        callback: Callable[[str, List[str]], Any],
    ) -> None:
        """Register the TUI confirmation callback.

        The callback receives (request_id, tool_names) and must return a
        bool (True = allow, False = deny).  It is awaited, so it can use
        Textual's ``push_screen_wait``.
        """
        self._tui_callback = callback

    def set_event_injector(
        self,
        injector: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Register the EventMerger's inject_confirmation_response callable.

        This is how desktop notification and CLI channels push their
        responses back into the event loop without blocking.
        """
        self._inject_confirmation = injector

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_confirm(
        self, tool_metadata: Dict[str, Any], tool_name: Optional[str] = None
    ) -> bool:
        """Decide whether a tool invocation needs confirmation.

        In ``smart`` mode the *host* sets a minimum threat level per tool (see
        ``threat_level.classify``): host-classified dangerous tools (e.g.
        command execution, which can escalate via sudo) are always confirmed
        regardless of their manifest, and a manifest may only raise the level,
        never lower it below the host floor. ``allow_all`` / ``ask_all``
        override as before.
        """
        mode = Config.CONFIRMATION_MODE

        if mode == "allow_all":
            return False
        if mode == "ask_all":
            return True

        # "smart" — confirm at or above ELEVATED. classify() folds in the host
        # floor AND the tool's own confirmation_required / threat_level.
        return classify(tool_name, tool_metadata) >= ThreatLevel.ELEVATED

    async def request_confirmation(
        self,
        request_id: str,
        tasks: List[Dict[str, Any]],
        tools_needing_confirmation: List[Dict[str, Any]],
        approved_tasks: List[Dict[str, Any]],
        denied_tools: List[str],
        dispatch_context: Optional[Dict[str, Any]] = None,
        notification_silent: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Send confirmation notification and return immediately.

        Does NOT block.  The response will arrive later as a
        ``CONFIRMATION_RESPONSE`` event through the EventMerger.

        Args:
            request_id: Unique id for this confirmation batch.
            tasks: The original full task list.
            tools_needing_confirmation: List of ``{tool_name, tool, params, meta}``
                dicts for tools that need confirmation.
            approved_tasks: Tasks already approved (no confirmation needed).
            denied_tools: Tools already denied (for partial batches).
            dispatch_context: Opaque context to resume dispatch after response.
            notification_silent: Suppress desktop notification.
            timeout: Seconds before auto-deny.
        """
        # Build a human-readable summary of tools needing confirmation.
        tool_names = [t["tool_name"] for t in tools_needing_confirmation]
        tool_summaries = ", ".join(tool_names)

        pending = PendingConfirmation(
            request_id=request_id,
            tasks=tasks,
            approved_tasks=list(approved_tasks),
            denied_tools=list(denied_tools),
            dispatch_context=dispatch_context,
            tool_names=tool_names,
        )
        self._pending[request_id] = pending

        logger.info(
            f"Confirmation requested (non-blocking): id={request_id}, "
            f"tools={tool_summaries}"
        )

        # Fire notification (non-blocking) on the best available channel.
        await self._send_notification(
            request_id=request_id,
            tool_names=tool_names,
            tools_detail=tools_needing_confirmation,
            notification_silent=notification_silent,
            timeout=timeout,
        )

        # Start timeout only if one is configured. The default (0) leaves
        # the confirmation pending indefinitely -- it's tracked in
        # list_pending() and reviewable via CLI/socket at any time, so
        # nothing needs to expire on a clock. A positive value restores the
        # old auto-deny behavior for unattended/headless setups.
        if timeout and timeout > 0:
            self._timeout_tasks[request_id] = asyncio.create_task(
                self._auto_deny_after(request_id, timeout)
            )

    def list_pending(self) -> List[Dict[str, Any]]:
        """Summaries of currently pending confirmations, for CLI/socket review."""
        return [
            {
                "id": p.request_id,
                "tool_names": p.tool_names,
                "created_at": p.created_at,
            }
            for p in self._pending.values()
        ]

    def resolve(self, response: Dict[str, Any]) -> Optional[PendingConfirmation]:
        """Process a confirmation response and return the pending data.

        Called by Jarvis when a ``CONFIRMATION_RESPONSE`` event arrives.

        Returns the ``PendingConfirmation`` with approval status applied,
        or None if the request id is unknown (expired / already resolved).
        """
        req_id = response.get("id")
        approved = response.get("approved", False)

        if not req_id or req_id not in self._pending:
            logger.warning(f"Confirmation response for unknown id: {req_id}")
            return None

        pending = self._pending.pop(req_id)

        # Cancel the timeout task.
        timeout_task = self._timeout_tasks.pop(req_id, None)
        if timeout_task and not timeout_task.done():
            timeout_task.cancel()

        if approved:
            # Move all tools_needing_confirmation into approved.
            # (The tasks that needed confirmation are everything in
            # pending.tasks minus what was already in approved_tasks.)
            pending.approved_tasks = list(pending.tasks)
            pending.denied_tools = []
            logger.info(f"Confirmation approved: id={req_id}")
        else:
            # All tools that needed confirmation are denied.
            # approved_tasks stays as-is (tools that didn't need confirmation).
            for task in pending.tasks:
                tool_name = f"{task.get('server', '?')}.{task.get('tool', '?')}"
                if task not in pending.approved_tasks:
                    if tool_name not in pending.denied_tools:
                        pending.denied_tools.append(tool_name)
            logger.info(f"Confirmation denied: id={req_id}")

        return pending

    def has_pending(self, request_id: str) -> bool:
        """Check if a confirmation request is still pending."""
        return request_id in self._pending

    # ------------------------------------------------------------------
    # Notification channels (all non-blocking)
    # ------------------------------------------------------------------

    async def _send_notification(
        self,
        request_id: str,
        tool_names: List[str],
        tools_detail: List[Dict[str, Any]],
        notification_silent: bool,
        timeout: float,
    ) -> None:
        """Fire notification on the best available channel."""

        tool_summary = ", ".join(tool_names)

        # 1. TUI modal — highest priority when Textual is running.
        if self._tui_callback is not None:
            asyncio.create_task(self._notify_tui(request_id, tool_names))
            return

        # 2. Desktop notification (unless silent).
        if not notification_silent and self._has_desktop_notifications():
            asyncio.create_task(self._notify_desktop(request_id, tool_summary, timeout))
            return

        # 2. Socket — if clients are connected.
        if (
            self._output_callback
            and self._has_socket_clients
            and self._has_socket_clients()
        ):
            self._notify_socket(request_id, tool_names, tools_detail, timeout)
            return

        # 3. CLI / TTY fallback.
        if self._has_tty():
            asyncio.create_task(self._notify_cli(request_id, tool_summary, timeout))
            return

        # No live channel available right now -- that's fine. The request
        # stays in _pending, reviewable anytime via list_pending() (CLI or
        # socket), rather than being auto-denied just because nobody
        # happened to be watching at this exact moment.
        logger.info(
            f"No live confirmation channel available for id={request_id}; "
            "left pending for CLI/socket review"
        )

    # -- TUI (ConfirmModal) --------------------------------------------

    async def _notify_tui(self, request_id: str, tool_names: List[str]) -> None:
        try:
            approved = await self._tui_callback(request_id, tool_names)
        except Exception as e:
            logger.warning(f"TUI confirmation callback failed: {e}")
            approved = False

        if self._inject_confirmation:
            self._inject_confirmation(
                {
                    "type": "confirmation_response",
                    "id": request_id,
                    "approved": bool(approved),
                }
            )

    # -- Desktop (notify-send) -----------------------------------------

    @staticmethod
    def _has_desktop_notifications() -> bool:
        from ..platform import current as platform

        return platform.has_desktop_notifications()

    async def _notify_desktop(
        self,
        request_id: str,
        tool_summary: str,
        timeout: float,
    ) -> None:
        """Show a desktop notification via the platform layer."""
        from ..platform import current as platform

        try:
            action = await platform.send_desktop_notification(
                title="JARVIS — Confirmation Required",
                body=f"Tool: {tool_summary}",
                timeout_ms=int(timeout * 1000),
            )
            approved = action == "allow"

            if self._inject_confirmation:
                self._inject_confirmation(
                    {
                        "type": "confirmation_response",
                        "id": request_id,
                        "approved": approved,
                    }
                )

        except Exception as e:
            logger.warning(f"Desktop notification failed: {e}")
            if self._inject_confirmation:
                self._inject_confirmation(
                    {
                        "type": "confirmation_response",
                        "id": request_id,
                        "approved": False,
                    }
                )

    # -- Socket --------------------------------------------------------

    def _notify_socket(
        self,
        request_id: str,
        tool_names: List[str],
        tools_detail: List[Dict[str, Any]],
        timeout: float,
    ) -> None:
        """Send confirmation request over the output socket.  Non-blocking.
        The response will come back on the input socket as a
        ``confirmation_response`` JSON line.
        """
        message = {
            "type": "confirmation_request",
            "id": request_id,
            "tools": tool_names,
            "details": [
                {"tool": t["tool_name"], "params": t.get("params", {})}
                for t in tools_detail
            ],
            "timeout": timeout,
        }
        try:
            self._output_callback(message)
        except Exception as e:
            logger.warning(f"Failed to send confirmation via socket: {e}")

    # -- CLI / TTY -----------------------------------------------------

    @staticmethod
    def _has_tty() -> bool:
        return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()

    async def _notify_cli(
        self,
        request_id: str,
        tool_summary: str,
        timeout: float,
    ) -> None:
        """Prompt the user on stdin.  Runs in an executor so it doesn't
        block the event loop.  Injects the response when done.
        """
        prompt = (
            f"\n[JARVIS] Confirmation required\n"
            f"  Tools: {tool_summary}\n"
            f"  Approve? [y/N]: "
        )
        try:
            answer = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, input, prompt),
                timeout=timeout,
            )
            approved = answer.strip().lower() in ("y", "yes")
        except (asyncio.TimeoutError, EOFError):
            approved = False

        if self._inject_confirmation:
            self._inject_confirmation(
                {
                    "type": "confirmation_response",
                    "id": request_id,
                    "approved": approved,
                }
            )

    # -- Timeout -------------------------------------------------------

    async def _auto_deny_after(self, request_id: str, timeout: float) -> None:
        """Auto-deny a pending confirmation after timeout seconds."""
        await asyncio.sleep(timeout)
        if request_id in self._pending:
            logger.info(f"Confirmation timed out, auto-denying: id={request_id}")
            if self._inject_confirmation:
                self._inject_confirmation(
                    {
                        "type": "confirmation_response",
                        "id": request_id,
                        "approved": False,
                    }
                )
