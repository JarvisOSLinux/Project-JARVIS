"""
JARVIS — the brain.

Hierarchical orchestrator:

  ROOT  →  responds directly, runs memory ops (store/recall/search),
           or routes to DISPATCH subsystem
  DISPATCH  →  tool discovery and execution sub-chain
               (plan → search → install → dispatch → done)

Memory operations (store, recall, search_memory, list_memory) are
ROOT-level actions — no separate LLM sub-chain.  The Rust contextor
binary handles storage and vector search; JARVIS calls it directly.

Dual input: voice (wake word) and socket/CLI ("jarvis send") feed the same
event queue. Both can be active simultaneously.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional

from .config import Config
from .core import ComponentFactory
from .core.logger import JarvisLogger, get_logger
from .dispatch.event_merger import Event
from .runtime import events as runtime_events
from .runtime import io as runtime_io
from .runtime import root_handlers
from .runtime.dispatch_flow import (
    dispatch_execute_tasks as runtime_dispatch_execute_tasks,
)
from .runtime.dispatch_flow import dispatch_send as runtime_dispatch_send
from .runtime.dispatch_flow import do_defer as runtime_do_defer
from .runtime.dispatch_flow import do_kill as runtime_do_kill
from .runtime.dispatch_flow import get_tool_metadata as runtime_get_tool_metadata
from .runtime.dispatch_flow import (
    run_dispatch_subchain as runtime_run_dispatch_subchain,
)
from .runtime.goal_updates import apply_goal_updates as runtime_apply_goal_updates
from .runtime.lifecycle import (
    bootstrap_tool_index_nonfatal,
    cancel_task_if_running,
    connect_dispatch_nonfatal,
    install_signal_handlers,
    start_runtime_services,
    stdin_is_tty,
)
from .runtime.root_actions import act_on_root_response as runtime_act_on_root_response
from .runtime.root_actions import feed_root_summary as runtime_feed_root_summary
from .runtime.root_context import build_root_context as runtime_build_root_context
from .runtime.root_context import (
    compact_payload_for_llm as runtime_compact_payload_for_llm,
)
from .runtime.session_commands import (
    handle_slash_command as runtime_handle_slash_command,
)
from .runtime.session_commands import session_reply as runtime_session_reply
from .runtime.voice_activation_thread import (
    process_voice_command_inject as runtime_process_voice_command_inject,
)
from .runtime.voice_activation_thread import (
    run_voice_activation as runtime_run_voice_activation,
)
from .sessions import SessionManager

logger = get_logger(__name__)

MAX_CHAIN_DEPTH = 15


class Jarvis:
    def __init__(self, text_mode=False, tui_mode=False):
        # TUI owns the terminal, so it always disables voice and stdin.
        self.text_mode = text_mode or tui_mode
        self.tui_mode = tui_mode
        self._running = False

        # Textual owns the terminal surface; suppress plain stdout logging
        # and stdout response printing while the TUI is active.
        JarvisLogger.set_console_enabled(not self.tui_mode)
        if self.tui_mode:
            JarvisLogger.apply_tui_root_mitigation()

        self.components = ComponentFactory.create_all_components(
            text_mode=self.text_mode,
            on_voice_command=self._handle_voice_command,
            suppress_stdout_output=self.tui_mode,
        )

        self.llm = self.components["llm"]
        self.dispatch = self.components["dispatch_adapter"]
        self.contextor = self.components["contextor"]
        self.goals = self.components["goal_manager"]
        self.events = self.components["event_merger"]
        self._embeddings = self.components.get("embeddings")
        self.task_parser = self.components["task_parser"]
        self.output_manager = self.components["output_manager"]
        self.confirmation = self.components["confirmation_manager"]
        self.voice_manager = self.components.get("voice_manager")
        self._output_clients: List[asyncio.StreamWriter] = []

        # Chat sessions — scopes conversation_log + memory to the active chat.
        # Session metadata lives in the contextor binary; SessionManager
        # just tracks the current-session pointer on the Python side.
        self.sessions = SessionManager(self.contextor)

    # ------------------------------------------------------------------
    # Event-driven main loop
    # ------------------------------------------------------------------

    async def run(self):
        self._running = True

        loop = asyncio.get_running_loop()
        install_signal_handlers(loop, self.stop)
        await connect_dispatch_nonfatal(self.dispatch, logger)
        await bootstrap_tool_index_nonfatal(self.dispatch, self._embeddings, logger)

        runtime_tasks = await start_runtime_services(self, logger)
        socket_task = runtime_tasks["input_socket"]
        output_task = runtime_tasks["output_socket"]

        logger.info("JARVIS: Event loop started")

        try:
            async for event in self.events:
                await self._handle_event(event)
        except KeyboardInterrupt:
            logger.info("JARVIS: Interrupted")
        finally:
            cancel_task_if_running(socket_task)
            cancel_task_if_running(output_task)
            await self._shutdown()

    async def _handle_event(self, event: Event):
        await runtime_events.handle_event(self, event)

    # ------------------------------------------------------------------
    # ROOT-level handlers
    # ------------------------------------------------------------------

    async def _on_user_input(self, text: str):
        await root_handlers.on_user_input(self, logger, text)

    async def _on_dispatch_signal(self, signal: Dict[str, Any]):
        await root_handlers.on_dispatch_signal(self, logger, signal)

    async def _on_confirmation_response(self, data: Dict[str, Any]):
        """Handle a CONFIRMATION_RESPONSE event from the event loop.

        Resolves the pending confirmation, then either dispatches the
        approved tasks or feeds USER_DENIAL back to ROOT so the LLM
        keeps communicating with the user.
        """
        await root_handlers.on_confirmation_response(self, logger, data)

    async def _act_on_root_response(self, response: Dict[str, Any], depth: int = 0):
        await runtime_act_on_root_response(
            app=self,
            logger=logger,
            response=response,
            depth=depth,
            max_chain_depth=MAX_CHAIN_DEPTH,
        )

    async def _feed_root_summary(self, label: str, summary: str, depth: int):
        await runtime_feed_root_summary(self, logger, label, summary, depth)

    # ------------------------------------------------------------------
    # DISPATCH sub-chain
    # ------------------------------------------------------------------

    async def _run_dispatch_subchain(self, intent: str) -> str:
        return await runtime_run_dispatch_subchain(
            app=self,
            logger=logger,
            intent=intent,
            max_chain_depth=MAX_CHAIN_DEPTH,
        )

    async def _dispatch_send(self, tasks, dispatch_context=None) -> Dict[str, Any]:
        return await runtime_dispatch_send(
            app=self,
            logger=logger,
            tasks=tasks,
            dispatch_context=dispatch_context,
        )

    async def _get_tool_metadata(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return await runtime_get_tool_metadata(self, logger, task)

    async def _dispatch_execute_tasks(self, tasks, depth: int):
        await runtime_dispatch_execute_tasks(
            app=self,
            logger=logger,
            tasks=tasks,
            depth=depth,
        )

    # ------------------------------------------------------------------
    # Session slash-commands
    # ------------------------------------------------------------------

    def _handle_slash_command(self, text: str) -> bool:
        """Handle /new, /sessions, /switch, /rename, /delete.

        Returns True if the input was a slash-command (handled), else
        False so it falls through to normal LLM routing.
        """
        return runtime_handle_slash_command(self, text)

    def _session_reply(self, message: str) -> None:
        """Emit a local reply for slash-commands (no LLM roundtrip)."""
        runtime_session_reply(self, message)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _get_embeddings(self):
        """Return the OllamaEmbeddings instance, or None if unavailable."""
        return self._embeddings

    def _activity(self, text: str, kind: str = "activity") -> None:
        """Emit a concise, user-facing runtime status line."""
        self.output_manager.emit_activity(text=text, kind=kind)

    def _persist_assistant_turn(self, text: str) -> None:
        """Append assistant-visible text to the session transcript in contextor."""
        if not self.contextor or not text or not str(text).strip():
            return
        sid = self.sessions.current_id
        if not sid:
            return
        self.contextor.auto_store_assistant_reply(
            str(text).strip(),
            session_id=sid,
        )

    def _ask_llm_sync(self, context: str, tag: str = "") -> Dict[str, Any]:
        """Single LLM call with timing logs (synchronous)."""
        logger.info(f"JARVIS [{tag}]: Calling LLM (mode={self.llm.mode})...")
        logger.debug(f"JARVIS [{tag}]: LLM context:\n{context}")

        t0 = time.perf_counter()
        response = self.llm.ask(context)
        elapsed = time.perf_counter() - t0

        logger.info(f"JARVIS [{tag}]: LLM responded in {elapsed:.2f}s")
        logger.debug(f"JARVIS [{tag}]: LLM raw response: {response}")
        return response

    async def _ask_llm(self, context: str, tag: str = "") -> Dict[str, Any]:
        """Single LLM call with timing logs (non-blocking for UI)."""
        self._activity(f"LLM ({self.llm.mode}) is thinking…", kind="llm")
        t0 = time.perf_counter()
        response = await asyncio.to_thread(self._ask_llm_sync, context, tag)
        elapsed = time.perf_counter() - t0
        self._activity(f"LLM responded in {elapsed:.1f}s.", kind="llm")
        return response

    def _compact_payload_for_llm(
        self,
        payload: Any,
        *,
        max_chars: int = 3000,
    ) -> str:
        """Compact large payloads before injecting them into root context.

        Keeps logs verbose but prevents giant vectors / stack traces from
        bloating the active chat context.
        """
        return runtime_compact_payload_for_llm(payload, max_chars=max_chars)

    def _build_root_context(
        self,
        new_input: Optional[str] = None,
        signal: Optional[Dict[str, Any]] = None,
    ) -> str:
        return runtime_build_root_context(
            self, logger, new_input=new_input, signal=signal
        )

    def _apply_goal_updates(self, updates):
        runtime_apply_goal_updates(self, updates)

    async def _do_kill(self, pids):
        await runtime_do_kill(self, logger, pids)

    async def _do_defer(self, goal_id: str, duration: int, reason: str = ""):
        await runtime_do_defer(self, logger, goal_id, duration, reason)

    # ------------------------------------------------------------------
    # Input sources (fed to EventMerger)
    # ------------------------------------------------------------------

    def _has_stdin(self) -> bool:
        """True if stdin is a TTY (interactive chat mode)."""
        return stdin_is_tty()

    def _run_voice_activation(self) -> None:
        """Run voice activation in a thread; commands are injected into the event loop."""
        runtime_run_voice_activation(self, logger)

    def _process_voice_command_inject(self) -> None:
        """Process voice command and inject into event loop (no direct ask)."""
        runtime_process_voice_command_inject(self, logger)

    async def _run_socket_listener(self) -> None:
        await runtime_io.run_socket_listener(self, logger)

    async def _handle_socket_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await runtime_io.handle_socket_connection(self, logger, reader, writer)

    def _on_output_for_broadcast(self, response: Dict[str, Any]) -> None:
        runtime_io.on_output_for_broadcast(self, response)

    async def _broadcast_to_output_clients(self, response: Dict[str, Any]) -> None:
        await runtime_io.broadcast_to_output_clients(self, response)

    async def _run_output_socket_listener(self) -> None:
        await runtime_io.run_output_socket_listener(self, logger)

    async def _handle_output_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await runtime_io.handle_output_connection(self, logger, reader, writer)

    async def _await_user_input(self) -> str:
        return await runtime_events.await_user_input()

    async def _await_dispatch_signal(self) -> Optional[Dict[str, Any]]:
        return await runtime_events.await_dispatch_signal(self, logger)

    # ------------------------------------------------------------------
    # Synchronous / legacy interface
    # ------------------------------------------------------------------

    def ask(self, prompt: str) -> Dict[str, Any]:
        """Synchronous single-prompt interface for one-shot CLI usage."""
        logger.info(f"JARVIS: Processing: '{prompt}'")

        self.sessions.ensure_session()
        if self.contextor:
            self.contextor.auto_store_prompt(
                prompt,
                session_id=self.sessions.current_id,
            )

        self.goals.add_goal(prompt)
        self.llm.switch_mode("root")
        context = self._build_root_context(new_input=prompt)

        response = self._ask_llm_sync(context, tag="ask")
        parsed = self.task_parser.parse(response)

        if "error" in parsed:
            result = {"output": "I had trouble processing that. Could you try again?"}
        elif parsed["action"] == "respond":
            result = {"output": parsed["output"]}
        else:
            result = {"output": f"Action: {parsed.get('action', 'unknown')}"}

        self.output_manager.handle_response(result)
        if "error" not in parsed and parsed.get("action") == "respond":
            self._persist_assistant_turn(result.get("output", ""))

        if Config.RESET_HISTORY_AFTER_RESPONSE:
            self.llm.reset_history()

        return result

    def _handle_voice_command(self, text: str) -> dict:
        """Voice callback: inject into event loop when running, else call ask() directly."""
        if self._running and self.events._running:
            self.events.inject_user_input(text)
            return {}
        return self.ask(prompt=text)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Request graceful shutdown (e.g. from signal handler)."""
        self._running = False
        if self.voice_manager and hasattr(self.voice_manager, "activation"):
            try:
                self.voice_manager.activation.stop_listening()
            except Exception:
                pass
        self.events.request_shutdown()

    async def _shutdown(self):
        self._running = False
        self.output_manager.remove_output_callback(self._on_output_for_broadcast)
        await self.events.stop()
        await self.dispatch.disconnect()
        if self.contextor:
            self.contextor.disconnect()
        logger.info("JARVIS: Shutdown complete")

    def listen_with_activation(self):
        if not self.voice_manager:
            logger.error("Voice manager not available in text mode")
            return
        self.voice_manager.start_voice_activation_mode()

    def listen(self):
        if not self.voice_manager:
            logger.error("Voice manager not available in text mode")
            return
        self.voice_manager.start_continuous_listening_mode()


def main():
    """Main entry point for JARVIS - delegates to CLI handler"""
    from .cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
