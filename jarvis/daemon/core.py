"""
JARVIS Core - Business logic for the daemon

This module contains the core JARVIS logic, decoupled from any specific
frontend (CLI, Voice, KDE). It processes queries and returns responses
through the message protocol.
"""

import re
from json import dumps
from typing import Dict, Any, Optional, Callable
from queue import Queue
from threading import Lock

from ..config import Config
from ..core import ComponentFactory
from ..core.logger import get_logger
from .protocol import (
    Message, MessageType, ClientSource,
    create_response, create_error, create_approval_request
)

logger = get_logger(__name__)


class JarvisCore:
    """
    Core JARVIS business logic.

    This class is frontend-agnostic and handles:
    - LLM interaction
    - SuperMCP tool orchestration
    - Command approval workflows
    - Session management
    """

    def __init__(self):
        """Initialize JARVIS core components"""
        self._lock = Lock()
        self._pending_approvals: Dict[str, Dict[str, Any]] = {}
        self._approval_callbacks: Dict[str, Callable] = {}

        # Initialize components
        logger.info("Initializing JARVIS Core...")

        # Create core components (no voice - that's handled by voice service)
        self.components = ComponentFactory.create_all_components(
            text_mode=True,  # Core doesn't handle voice directly
            on_voice_command=None
        )

        self.llm = self.components['llm']
        self.command_parser = self.components['command_parser']

        # Statistics
        self._query_count = 0
        self._connected_clients: Dict[str, ClientSource] = {}

        logger.info("JARVIS Core initialized")

    def process_query(self, message: Message,
                      on_approval_needed: Optional[Callable[[Message], None]] = None) -> Message:
        """
        Process a query message and return a response.

        Args:
            message: Query message from client
            on_approval_needed: Callback when approval is needed (for async approval)

        Returns:
            Response message
        """
        with self._lock:
            self._query_count += 1
            query_id = message.id

        prompt = message.text
        if not prompt:
            return create_error("Empty query", "EMPTY_QUERY", query_id)

        logger.info(f"Processing query [{query_id[:8]}]: '{prompt[:50]}...'")

        try:
            response = self._process_llm_interaction(
                prompt, query_id, on_approval_needed
            )

            return create_response(
                text=response['output'],
                reply_to=query_id,
                tools_used=response.get('tools_used', [])
            )

        except Exception as e:
            logger.error(f"Error processing query: {e}", exc_info=True)
            return create_error(str(e), "PROCESSING_ERROR", query_id)

    def _process_llm_interaction(self, prompt: str, query_id: str,
                                  on_approval_needed: Optional[Callable] = None) -> Dict[str, Any]:
        """
        Process LLM interaction with SuperMCP tool execution.

        This is the main processing loop, extracted from the original Jarvis class.
        """
        response = self.llm.ask(prompt)
        tools_used = []

        iteration = 0
        MAX_ITERATIONS = 5
        last_command = None
        repeat_count = 0

        while response['user_request'] != "Conversation" and iteration < MAX_ITERATIONS:
            iteration += 1
            logger.debug(f"Iteration {iteration}, type: '{response['user_request']}'")

            if response['user_request'] == "SuperMCP":
                current_command = response['output']
                logger.info(f"[{iteration}] SuperMCP: {current_command}")

                # Detect repeated commands
                if current_command == last_command:
                    repeat_count += 1
                    logger.warning(f"LLM repeated same command (count: {repeat_count})")
                    if repeat_count >= 2:
                        logger.error("LLM stuck repeating - breaking loop")
                        response = {
                            "user_request": "Conversation",
                            "output": "I apologize, but I'm having difficulty processing this request. Please try rephrasing."
                        }
                        break
                else:
                    repeat_count = 0
                    last_command = current_command

                # Execute SuperMCP commands
                supermcp_output = self.command_parser.execute_command_sequence(response['output'])
                logger.debug(f"SuperMCP output:\n{dumps(supermcp_output, indent=2)}")

                # Track tools used
                for result in supermcp_output.get('results', []):
                    if isinstance(result, dict) and 'server' in result:
                        tools_used.append(f"{result['server']}.{result.get('tool', 'unknown')}")

                # Check for approval requirement
                results = supermcp_output.get('results', [])
                approval_required = False
                approval_info = None

                for result in results:
                    if isinstance(result, dict) and result.get('approval_required'):
                        approval_required = True
                        approval_info = result
                        break

                if approval_required:
                    # Handle approval workflow
                    feedback_text = self._handle_approval(
                        approval_info, query_id, on_approval_needed
                    )
                    response = self.llm.ask(feedback_text)
                else:
                    # Normal flow
                    feedback_text = self._format_feedback_for_llm(supermcp_output)
                    logger.info(f"Feedback ({len(feedback_text)} chars): {feedback_text[:150]}...")
                    response = self.llm.ask(feedback_text)
            else:
                logger.warning(f"Unknown user_request type: '{response['user_request']}'")
                break

        if iteration >= MAX_ITERATIONS:
            logger.warning(f"Hit maximum iterations ({MAX_ITERATIONS})")
            response = {
                "user_request": "Conversation",
                "output": "I've tried multiple approaches but encountered issues. Please try a simpler request."
            }

        # Reset history if configured
        if Config.RESET_HISTORY_AFTER_RESPONSE:
            logger.debug("Resetting LLM history")
            self.llm.reset_history()

        response['tools_used'] = tools_used
        return response

    def _handle_approval(self, approval_info: Dict[str, Any], query_id: str,
                         on_approval_needed: Optional[Callable] = None) -> str:
        """
        Handle command approval workflow.

        For daemon mode, this sends an approval request to the client
        and waits for a response (or uses callback for async).
        """
        command = approval_info.get('command', '')
        shellmcp_response = approval_info.get('shellmcp_response', '')

        # Extract command ID
        command_id_match = re.search(r'ID:\s*([a-f0-9-]+)', shellmcp_response, re.IGNORECASE)
        command_id = command_id_match.group(1) if command_id_match else None

        security_level = "requires_approval"

        logger.info(f"Approval required for command: {command}")

        # For now, we'll use synchronous approval via callback
        # In full implementation, this would be async via WebSocket
        if on_approval_needed:
            approval_msg = create_approval_request(command, security_level, query_id)

            # Store pending approval
            self._pending_approvals[query_id] = {
                'command': command,
                'command_id': command_id,
                'approval_info': approval_info
            }

            # Notify client (they should respond with approval_response)
            on_approval_needed(approval_msg)

            # For synchronous operation, we need to wait for response
            # This is a simplified version - full async would use await
            # For now, default to denied for safety
            logger.warning("Async approval not yet implemented, defaulting to denied")
            approved = False
        else:
            # No callback - auto-deny for safety
            approved = False

        if approved and command_id:
            logger.info(f"Command approved, executing with ID: {command_id}")
            execution_result = self.command_parser.supermcp.call_server_tool(
                "ShellMCP",
                "approve_command",
                {"commandId": command_id}
            )

            if execution_result.get('error'):
                return f"Error executing command: {execution_result['error']}"
            else:
                result_str = str(execution_result.get('result', execution_result))
                return f"[SUCCESS] Command executed: {command}\nOutput: {result_str[:500]}\n\nTASK COMPLETE! Return a Conversation response."
        else:
            if command_id:
                self.command_parser.supermcp.call_server_tool(
                    "ShellMCP",
                    "deny_command",
                    {"commandId": command_id, "reason": "User denied the command"}
                )

            logger.info(f"Command denied: {command}")
            return f"[DENIED] Command was not approved: {command}\n\nTASK COMPLETE! Return a Conversation response."

    def handle_approval_response(self, message: Message) -> Optional[str]:
        """
        Handle approval response from client.

        Returns the query_id if approval was for a pending query.
        """
        reply_to = message.reply_to
        if not reply_to or reply_to not in self._pending_approvals:
            logger.warning(f"Received approval for unknown query: {reply_to}")
            return None

        approved = message.data.get('approved', False) if message.data else False
        pending = self._pending_approvals.pop(reply_to)

        logger.info(f"Approval response for {reply_to}: {'approved' if approved else 'denied'}")

        # Execute or deny the command
        command_id = pending.get('command_id')
        if approved and command_id:
            self.command_parser.supermcp.call_server_tool(
                "ShellMCP",
                "approve_command",
                {"commandId": command_id}
            )
        elif command_id:
            self.command_parser.supermcp.call_server_tool(
                "ShellMCP",
                "deny_command",
                {"commandId": command_id, "reason": "User denied"}
            )

        return reply_to

    def _format_feedback_for_llm(self, output: Dict[str, Any]) -> str:
        """Convert SuperMCP output to clear, readable text for the LLM"""
        if not isinstance(output, dict) or not output.get('success'):
            return f"Error: {output.get('error', 'Unknown error occurred')}"

        feedback_lines = []
        results = output.get('results', [])

        for i, result in enumerate(results, 1):
            if not isinstance(result, dict):
                feedback_lines.append(f"[SUCCESS] Command executed successfully")
                feedback_lines.append(f"  Output: {str(result)}")
                feedback_lines.append(f"TASK COMPLETE! Return a Conversation response.")
                continue

            if 'error' in result:
                feedback_lines.append(f"Error in command {i}: {result['error']}")
                continue

            if result.get('ok') is True and 'count' in result:
                feedback_lines.append(f"[OK] Reloaded {result['count']} MCP servers")
                continue

            if 'result' in result:
                inner_result = result['result']
                if isinstance(inner_result, dict) and 'files' in inner_result:
                    files = inner_result.get('files', [])
                    dirs = inner_result.get('directories', [])
                    path = inner_result.get('path', '.')

                    feedback_lines.append(f"[SUCCESS] Listed directory '{path}':")
                    file_names = ', '.join([f['name'] for f in files[:10]])
                    feedback_lines.append(f"  Files ({len(files)}): {file_names}")
                    if len(files) > 10:
                        feedback_lines.append(f"    ... and {len(files) - 10} more")
                    dir_names = ', '.join([d['name'] for d in dirs[:10]]) if dirs else "none"
                    feedback_lines.append(f"  Directories ({len(dirs)}): {dir_names}")
                    feedback_lines.append(f"\nTASK COMPLETE! Return a Conversation response.")
                    continue

                if isinstance(inner_result, list) and inner_result and 'name' in inner_result[0]:
                    server_names = [s['name'] for s in inner_result]
                    feedback_lines.append(f"[OK] Available servers: {', '.join(server_names)}")
                    continue

                feedback_lines.append(f"[OK] Command executed successfully")
                feedback_lines.append(f"  Output: {str(inner_result)[:300]}")
                feedback_lines.append(f"TASK COMPLETE! Return a Conversation response.")
                continue

            if 'name' in result and 'tools' in result:
                server_name = result['name']
                tools = result.get('tools', [])
                feedback_lines.append(f"[OK] Inspected {server_name}:")
                feedback_lines.append(f"  Available tools: {', '.join(tools)}")
                continue

            feedback_lines.append(f"Result {i}: {dumps(result)[:300]}")

        return "\n".join(feedback_lines)

    def get_status(self) -> Dict[str, Any]:
        """Get daemon status information"""
        return {
            'status': 'running',
            'query_count': self._query_count,
            'connected_clients': len(self._connected_clients),
            'llm_provider': Config.LLM_PROVIDER,
            'llm_model': Config.LLM_MODEL,
            'pending_approvals': len(self._pending_approvals)
        }

    def register_client(self, client_id: str, source: ClientSource) -> None:
        """Register a connected client"""
        self._connected_clients[client_id] = source
        logger.info(f"Client registered: {client_id} ({source.value})")

    def unregister_client(self, client_id: str) -> None:
        """Unregister a disconnected client"""
        if client_id in self._connected_clients:
            del self._connected_clients[client_id]
            logger.info(f"Client unregistered: {client_id}")

    def shutdown(self) -> None:
        """Shutdown the core gracefully"""
        logger.info("Shutting down JARVIS Core...")
        # Cleanup resources
        self._pending_approvals.clear()
        self._connected_clients.clear()
