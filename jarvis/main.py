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
import json
import sys
import time
from typing import Any, Dict, List, Optional

from .config import Config
from .core import ComponentFactory
from .core.logger import JarvisLogger, get_logger
from .dispatch.event_merger import Event
from .runtime import events as runtime_events
from .runtime import io as runtime_io
from .runtime.lifecycle import (
    bootstrap_tool_index_nonfatal,
    cancel_task_if_running,
    connect_dispatch_nonfatal,
    install_signal_handlers,
    start_runtime_services,
)
from .runtime.root_actions import act_on_root_response as runtime_act_on_root_response
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
        logger.info(f"JARVIS: User input: '{text}'")

        # Slash-commands are session-control shortcuts, not LLM input.
        if text.startswith("/"):
            handled = self._handle_slash_command(text)
            if handled:
                return

        # Ensure we have a session to log against.  First-ever input
        # lazily creates one so history is always session-scoped.
        self.sessions.ensure_session()

        self.goals.add_goal(text)

        # Auto-store every user prompt for long-term recall.
        # No LLM decision — every prompt gets persisted + embedded.
        if self.contextor:
            self.contextor.auto_store_prompt(
                text,
                session_id=self.sessions.current_id,
            )

        self.llm.switch_mode("root")
        context = self._build_root_context(new_input=text)
        self._activity("Thinking about your request…", kind="llm")

        response = await self._ask_llm(context, tag="root")
        await self._act_on_root_response(response)

    async def _on_dispatch_signal(self, signal: Dict[str, Any]):
        sig_type = signal.get("type")
        sig_pid = signal.get("pid")
        logger.info(f"JARVIS: Dispatch signal: type={sig_type}, pid={sig_pid}")
        if sig_type:
            self._activity(
                f"Dispatch signal: {sig_type} (pid {sig_pid})", kind="dispatch"
            )

        self.goals.update_from_signal(signal)

        self.llm.switch_mode("root")
        context = self._build_root_context(signal=signal)

        response = await self._ask_llm(context, tag="root")
        await self._act_on_root_response(response)

    async def _on_confirmation_response(self, data: Dict[str, Any]):
        """Handle a CONFIRMATION_RESPONSE event from the event loop.

        Resolves the pending confirmation, then either dispatches the
        approved tasks or feeds USER_DENIAL back to ROOT so the LLM
        keeps communicating with the user.
        """
        pending = self.confirmation.resolve(data)
        if pending is None:
            # Already expired / resolved — ignore.
            return

        logger.info(
            f"JARVIS: Confirmation resolved: id={pending.request_id}, "
            f"approved={len(pending.approved_tasks)}, "
            f"denied={len(pending.denied_tools)}"
        )

        # All denied — feed USER_DENIAL to ROOT.
        if pending.denied_tools and not pending.approved_tasks:
            denied_list = ", ".join(pending.denied_tools)
            self.llm.switch_mode("root")
            context = self._build_root_context()
            context += f"\nUSER_DENIAL: Action {denied_list} was denied by the user"
            response = await self._ask_llm(context, tag="root-confirmation-denied")
            await self._act_on_root_response(response)
            return

        # Some or all approved — dispatch the approved tasks.
        if pending.approved_tasks:
            result = await self.dispatch.send_tasks(pending.approved_tasks)

            self.llm.switch_mode("root")
            context = self._build_root_context()

            if isinstance(result, dict) and "error" in result:
                context += f"\nDISPATCH_ERROR: {self._compact_payload_for_llm(result)}"
            else:
                context += f"\nDISPATCH_RESULT: {self._compact_payload_for_llm(result)}"

            # Include partial denial if some tools were denied.
            if pending.denied_tools:
                denied_list = ", ".join(pending.denied_tools)
                context += f"\nUSER_DENIAL: Action {denied_list} was denied by the user"

            response = await self._ask_llm(context, tag="root-confirmation-result")
            await self._act_on_root_response(response)

    async def _act_on_root_response(self, response: Dict[str, Any], depth: int = 0):
        await runtime_act_on_root_response(
            app=self,
            logger=logger,
            response=response,
            depth=depth,
            max_chain_depth=MAX_CHAIN_DEPTH,
        )

    async def _feed_root_summary(self, label: str, summary: str, depth: int):
        """Feed a subsystem summary back into ROOT for the next decision."""
        self.llm.switch_mode("root")
        context = self._build_root_context()
        context += f"\n{label}: {summary}"

        response = await self._ask_llm(context, tag="root-chain")
        await self._act_on_root_response(response, depth + 1)

    # ------------------------------------------------------------------
    # DISPATCH sub-chain
    # ------------------------------------------------------------------

    async def _run_dispatch_subchain(self, intent: str) -> str:
        """
        Enter dispatch mode, give it the intent, and loop until it
        returns "done" with a summary.

        Before entering dispatch, JARVIS picks the active discovery
        backend (embedding or keyword) and installs the matching
        system prompt — the LLM never sees which backend is active.

        The LLM starts with a "plan" action to split the intent into
        sub-tasks. JARVIS runs the selected discovery backend and
        injects MATCHED_TOOLS / CANDIDATE_SERVERS into the next prompt.
        """
        # Pick the discovery backend before switching modes so the
        # dispatch system prompt matches the runtime behavior.
        mode = await self.dispatch.select_discovery_mode(self._get_embeddings())
        dispatch_prompt = (
            Config.LLM_DISPATCH_PROMPT_EMBEDDING
            if mode == "embedding"
            else Config.LLM_DISPATCH_PROMPT_KEYWORD
        )
        self.llm.set_prompt("dispatch", dispatch_prompt)

        self.llm.switch_mode("dispatch")

        context_parts = [f"INTENT: {intent}"]
        goals = self.goals.get_context()
        if goals:
            context_parts.append(f"GOALS: {json.dumps(goals)}")
        context = "\n".join(context_parts)

        for step in range(MAX_CHAIN_DEPTH):
            logger.info(
                "JARVIS: Dispatch iteration start "
                f"(step={step}, context_chars={len(context)})"
            )
            self._activity(f"Dispatch step {step + 1}: reasoning…", kind="dispatch")
            response = await self._ask_llm(context, tag=f"dispatch-step-{step}")
            parsed = self.task_parser.parse(response)

            if "error" in parsed:
                logger.warning(
                    f"JARVIS: Dispatch sub-chain parse error: {parsed['error']}"
                )
                return f"Error: {parsed['error']}"

            action = parsed["action"]
            logger.info(
                f"JARVIS: Dispatch iteration action (step={step}, action={action})"
            )
            self._apply_goal_updates(parsed.get("goal_updates", []))

            if action == "done":
                logger.info(
                    f"JARVIS: Dispatch sub-chain completed: {parsed['summary']}"
                )
                self._activity("Dispatch completed.", kind="dispatch")
                return parsed["summary"]

            if action == "plan":
                # LLM split the intent into sub-tasks — search for tools
                sub_tasks = parsed.get("tasks", [])
                logger.info(f"JARVIS: Plan has {len(sub_tasks)} sub-task(s)")
                self._activity(
                    f"Planned {len(sub_tasks)} sub-task(s); finding tools…",
                    kind="dispatch",
                )

                available_tools = await self.dispatch.discover_tools(
                    tasks=sub_tasks,
                    embeddings=self._get_embeddings(),
                )

                if available_tools:
                    context = f"{available_tools}\n\nINTENT: {intent}"
                else:
                    context = (
                        "NO_TOOLS_FOUND: No matching tools were found. "
                        "Re-plan with different sub-task intents, or use 'done' "
                        "if the request cannot be fulfilled.\n"
                        f"INTENT: {intent}"
                    )

            elif action == "search":
                self._activity("Searching MCP servers…", kind="dispatch")
                result = await self.dispatch.search_servers(parsed["keywords"])
                context = f"SEARCH_RESULTS: {self._compact_payload_for_llm(result)}"

            elif action == "list_tools":
                self._activity(
                    f"Listing tools for {parsed['server_id']}…", kind="dispatch"
                )
                result = await self.dispatch.list_server_tools(parsed["server_id"])
                context = f"TOOLS: {self._compact_payload_for_llm(result)}"

            elif action == "install":
                self._activity(
                    f"Installing server {parsed['server_id']}…", kind="dispatch"
                )
                result = await self.dispatch.install_server(parsed["server_id"])
                context = f"INSTALL_RESULT: {self._compact_payload_for_llm(result)}"

                # Auto-index non-approved servers after successful install
                server_id = parsed.get("server_id", "")
                if "error" not in result and server_id:
                    await self.dispatch.auto_index_server(
                        server_id=server_id,
                        embeddings=self._get_embeddings(),
                    )

            elif action == "dispatch" and "tasks" in parsed:
                self._activity(
                    f"Dispatching {len(parsed['tasks'])} task(s)…", kind="dispatch"
                )
                result = await self._dispatch_send(parsed["tasks"])
                if isinstance(result, dict) and result.get("awaiting_confirmation"):
                    # Non-blocking: confirmation sent, return to event loop.
                    # Dispatch will resume when CONFIRMATION_RESPONSE arrives.
                    logger.info(
                        f"JARVIS: Dispatch sub-chain paused for confirmation "
                        f"id={result['confirmation_id']}"
                    )
                    self._activity(
                        "Waiting for your confirmation before running tools.",
                        kind="dispatch",
                    )
                    return "Waiting for user confirmation."
                elif isinstance(result, dict) and "error" in result:
                    context = f"DISPATCH_ERROR: {self._compact_payload_for_llm(result)}"
                else:
                    self._activity("Tool results received.", kind="dispatch")
                    context = (
                        f"DISPATCH_RESULT: {self._compact_payload_for_llm(result)}"
                    )

            elif action == "wait":
                logger.info("JARVIS: Dispatch sub-chain waiting")
                self._activity("Waiting for tasks to complete…", kind="dispatch")
                return "Waiting for tasks to complete."

            elif action == "kill":
                self._activity("Stopping selected task(s)…", kind="dispatch")
                await self._do_kill(parsed["pids"])
                context = f"KILL_RESULT: Killed PID(s) {parsed['pids']}"

            elif action == "defer":
                self._activity(
                    f"Setting reminder for {parsed['duration']}s (goal {parsed['goal_id']})…",
                    kind="dispatch",
                )
                await self._do_defer(
                    parsed["goal_id"],
                    parsed["duration"],
                    parsed.get("reason", ""),
                )
                return f"Deferred goal {parsed['goal_id']} for {parsed['duration']}s."

            elif action == "respond":
                self._activity("Composing response…", kind="llm")
                out = parsed["output"]
                self.output_manager.handle_response({"output": out})
                self._persist_assistant_turn(out)
                return out

            else:
                logger.warning(f"JARVIS: Unexpected dispatch action '{action}'")
                return f"Unexpected action: {action}"

        logger.error("JARVIS: Dispatch sub-chain hit max steps")
        return "Tool execution timed out (too many steps)."

    async def _dispatch_send(self, tasks, dispatch_context=None) -> Dict[str, Any]:
        """Low-level send to dispatch adapter, gated by TLA confirmation.

        **Non-blocking**: if any tool requires confirmation, the tasks are
        stashed and a notification is sent.  The method returns immediately
        with ``{"awaiting_confirmation": True, ...}``.  When the user
        responds, the ``CONFIRMATION_RESPONSE`` event triggers
        ``_on_confirmation_response()`` which resumes the dispatch.

        If no tools need confirmation, tasks are dispatched immediately.
        """
        if not self.dispatch.is_connected:
            self._activity("Dispatch is unavailable right now.", kind="dispatch")
            return {"error": "Dispatch not connected"}

        approved_tasks = []
        tools_needing_confirmation = []

        for task in tasks:
            tool_name = f"{task.get('server', '?')}.{task.get('tool', '?')}"
            tool_meta = await self._get_tool_metadata(task)

            if self.confirmation.should_confirm(tool_meta):
                notification_silent = tool_meta.get(
                    "notification_silent",
                    Config.NOTIFICATION_SILENT,
                )
                tools_needing_confirmation.append(
                    {
                        "tool_name": tool_name,
                        "task": task,
                        "params": task.get("params", {}),
                        "notification_silent": notification_silent,
                    }
                )
            else:
                approved_tasks.append(task)

        # No confirmation needed — dispatch everything now.
        if not tools_needing_confirmation:
            return await self.dispatch.send_tasks(approved_tasks)

        # Some tools need confirmation — stash and notify, return immediately.
        import uuid

        request_id = str(uuid.uuid4())[:8]

        # Use the first tool's notification_silent preference for the batch.
        notification_silent = tools_needing_confirmation[0].get(
            "notification_silent",
            Config.NOTIFICATION_SILENT,
        )

        await self.confirmation.request_confirmation(
            request_id=request_id,
            tasks=tasks,
            tools_needing_confirmation=tools_needing_confirmation,
            approved_tasks=approved_tasks,
            denied_tools=[],
            dispatch_context=dispatch_context,
            notification_silent=notification_silent,
            timeout=Config.CONFIRMATION_TIMEOUT,
        )

        tool_names = [t["tool_name"] for t in tools_needing_confirmation]
        self._activity(
            "Awaiting confirmation for: "
            + ", ".join(tool_names[:3])
            + ("…" if len(tool_names) > 3 else ""),
            kind="dispatch",
        )
        return {
            "awaiting_confirmation": True,
            "confirmation_id": request_id,
            "tools_pending": tool_names,
        }

    async def _get_tool_metadata(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Retrieve tool metadata (including confirmation_required) from the
        MCP server registry.

        Falls back to an empty dict if metadata is unavailable so that
        unconfigured tools default to no confirmation (safe for the
        ``smart`` mode).
        """
        server_id = task.get("server")
        tool_name = task.get("tool")
        if not server_id or not tool_name:
            return {}

        try:
            tools = await self.dispatch.list_server_tools(server_id)
            if isinstance(tools, dict) and "tools" in tools:
                for t in tools["tools"]:
                    if t.get("name") == tool_name:
                        return t
        except Exception as e:
            logger.debug(f"Could not fetch metadata for {server_id}.{tool_name}: {e}")

        return {}

    async def _dispatch_execute_tasks(self, tasks, depth: int):
        """Handle a dispatch action that already has concrete tasks (from root)."""
        if not self.dispatch.is_connected:
            self.output_manager.handle_response(
                {
                    "output": "I can't execute tools right now — dispatch is not connected.",
                }
            )
            return

        result = await self._dispatch_send(tasks)

        # If awaiting confirmation, return to event loop — the
        # CONFIRMATION_RESPONSE event will resume this flow.
        if isinstance(result, dict) and result.get("awaiting_confirmation"):
            logger.info(
                f"JARVIS: Root dispatch paused for confirmation "
                f"id={result['confirmation_id']}"
            )
            return

        self.llm.switch_mode("root")
        context = self._build_root_context()
        if isinstance(result, dict) and "error" in result:
            context += f"\nDISPATCH_ERROR: {self._compact_payload_for_llm(result)}"
        else:
            context += f"\nDISPATCH_RESULT: {self._compact_payload_for_llm(result)}"

        response = await self._ask_llm(context, tag="root-dispatch-result")
        await self._act_on_root_response(response, depth + 1)

    # ------------------------------------------------------------------
    # Session slash-commands
    # ------------------------------------------------------------------

    def _handle_slash_command(self, text: str) -> bool:
        """Handle /new, /sessions, /switch, /rename, /delete.

        Returns True if the input was a slash-command (handled), else
        False so it falls through to normal LLM routing.
        """
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/new":
            if not self.sessions.available:
                self._session_reply("Memory is disabled — sessions unavailable.")
                return True
            session = self.sessions.new_session(title=arg or None)
            if session:
                self._session_reply(
                    f"Started new session {session.short_id()}"
                    + (f" ('{session.title}')" if session.title else ""),
                )
            else:
                self._session_reply("Could not create a new session.")
            return True

        if cmd == "/sessions":
            if not self.sessions.available:
                self._session_reply("Memory is disabled — sessions unavailable.")
                return True
            sessions = self.sessions.list(limit=50)
            if not sessions:
                self._session_reply("No sessions yet.")
                return True
            current_id = self.sessions.current_id
            lines = ["Sessions (most recent first):"]
            for s in sessions:
                marker = "* " if s.id == current_id else "  "
                lines.append(f"{marker}{s.short_id()}  {s.display_label()}")
            self._session_reply("\n".join(lines))
            return True

        if cmd == "/switch":
            if not arg:
                self._session_reply("Usage: /switch <session_id_prefix>")
                return True
            session = self.sessions.switch(arg)
            if session:
                self._session_reply(
                    f"Switched to {session.short_id()} ('{session.title}')"
                )
            else:
                self._session_reply(f"No session matches '{arg}'.")
            return True

        if cmd == "/rename":
            if not arg:
                self._session_reply("Usage: /rename <new title>")
                return True
            if not self.sessions.current:
                self._session_reply("No active session to rename.")
                return True
            if self.sessions.rename(arg):
                self._session_reply(f"Renamed to '{arg}'.")
            else:
                self._session_reply("Rename failed.")
            return True

        if cmd == "/delete":
            if not arg:
                self._session_reply("Usage: /delete <session_id_prefix>")
                return True
            sessions = self.sessions.list(limit=500)
            matches = [s for s in sessions if s.id.startswith(arg)]
            if len(matches) != 1:
                self._session_reply(
                    f"Need a unique id prefix; got {len(matches)} match(es)."
                )
                return True
            if self.sessions.delete(matches[0].id):
                self._session_reply(f"Deleted session {matches[0].short_id()}.")
            else:
                self._session_reply("Delete failed.")
            return True

        # Unknown slash-command — let it through to the LLM so the user
        # can still type "/foo" as literal input if they insist.
        return False

    def _session_reply(self, message: str) -> None:
        """Emit a local reply for slash-commands (no LLM roundtrip)."""
        self.output_manager.handle_response({"output": message})

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
        try:
            if isinstance(payload, (dict, list)):
                text = json.dumps(payload, ensure_ascii=False)
            else:
                text = str(payload)
        except Exception:
            text = str(payload)

        # Trim huge vector dumps while preserving diagnostic intent.
        text = text.replace("vector", "vec")
        text = text.replace("vectors", "vecs")

        if len(text) <= max_chars:
            return text
        omitted = len(text) - max_chars
        return f"{text[:max_chars]} ... [truncated {omitted} chars]"

    def _build_root_context(
        self,
        new_input: Optional[str] = None,
        signal: Optional[Dict[str, Any]] = None,
    ) -> str:
        parts = []

        active_goals = self.goals.get_context()
        if active_goals:
            parts.append(f"GOALS: {json.dumps(active_goals)}")

        if signal:
            parts.append(f"SIGNAL: {json.dumps(signal)}")

        # RAG retrieval — inject relevant memories from the contextor
        # based on the current user input.  Scoped to the active session
        # (plus global entries) so sibling chats don't bleed through.
        if new_input and self.contextor:
            rag_context = self.contextor.retrieve_context(
                query=new_input,
                top_k=getattr(Config, "RAG_TOP_K", 5),
                min_score=getattr(Config, "RAG_MIN_SCORE", 0.3),
                session_id=self.sessions.current_id,
                include_global=True,
            )
            if rag_context:
                logger.info(
                    "JARVIS: RAG context injected for root "
                    f"(chars={len(rag_context)}, session_id={self.sessions.current_id})"
                )
                parts.append(rag_context)
            else:
                logger.debug("JARVIS: No RAG context injected for root")

        # Tier-2 rolling summary — gives the LLM a compressed view of
        # older turns without blowing the context window.
        summary = self.sessions.load_summary()
        if summary:
            parts.append(f"CONVERSATION_SUMMARY: {summary}")

        if new_input:
            parts.append(f"NEW INPUT: {new_input}")

        logger.debug(
            "JARVIS: Built root context "
            f"(parts={len(parts)}, chars={sum(len(p) for p in parts)})"
        )

        return "\n".join(parts) if parts else "No active context."

    def _apply_goal_updates(self, updates):
        for update in updates:
            goal_id = update.get("id")
            status = update.get("status")
            if not goal_id or not status:
                continue

            if status == "completed":
                self.goals.complete_goal(goal_id, update.get("result"))
            elif status == "failed":
                self.goals.fail_goal(goal_id, update.get("result"))
            elif status == "active":
                self.goals.link_tasks(goal_id, update.get("pids", []))

    async def _do_kill(self, pids):
        if not self.dispatch.is_connected:
            return
        result = await self.dispatch.kill_tasks(pids)
        if "error" in result:
            logger.error(f"JARVIS: Kill error: {result['error']}")
        else:
            logger.info(f"JARVIS: Killed PID(s): {pids}")

    async def _do_defer(self, goal_id: str, duration: int, reason: str = ""):
        if not self.dispatch.is_connected:
            logger.warning("JARVIS: Dispatch not connected, cannot defer goal")
            self._activity(
                "Cannot set reminder: dispatch not connected.", kind="dispatch"
            )
            self.output_manager.handle_response(
                {
                    "output": "I can't defer goals right now — dispatch is not connected.",
                }
            )
            return

        label = f"goal_reminder:{goal_id}"
        metadata = {"goal_id": goal_id, "type": "goal_defer"}
        if reason:
            metadata["reason"] = reason

        result = await self.dispatch.set_timer(label, duration, metadata)

        if "error" in result:
            logger.error(f"JARVIS: Timer error: {result['error']}")
            self._activity("Failed to set reminder timer.", kind="dispatch")
        else:
            timer_pid = result.get("pid", 0)
            self.goals.defer_goal(goal_id, timer_pid)
            logger.info(
                f"JARVIS: Deferred goal [{goal_id}] for {duration}s (timer PID {timer_pid})"
            )
            self._activity(
                f"Reminder set for {duration}s (goal {goal_id}, timer pid {timer_pid}).",
                kind="dispatch",
            )

    # ------------------------------------------------------------------
    # Input sources (fed to EventMerger)
    # ------------------------------------------------------------------

    def _has_stdin(self) -> bool:
        """True if stdin is a TTY (interactive chat mode)."""
        return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()

    def _run_voice_activation(self) -> None:
        """Run voice activation in a thread; commands are injected into the event loop."""
        vm = self.voice_manager
        vm._wake_word_detected = False
        try:
            if hasattr(vm.activation, "on_wake_word"):
                vm.activation.on_wake_word = lambda: setattr(
                    vm, "_wake_word_detected", True
                )
            if not self.voice_manager.activation.start_listening():
                logger.error("JARVIS: Failed to start voice activation")
                return
            while self._running:
                if getattr(self.voice_manager, "_wake_word_detected", False):
                    self.voice_manager._wake_word_detected = False
                    self._process_voice_command_inject()
                time.sleep(0.3)
        except Exception as e:
            logger.error(f"JARVIS: Voice thread error: {e}", exc_info=True)
        finally:
            if hasattr(self.voice_manager, "activation"):
                self.voice_manager.activation.cleanup()

    def _process_voice_command_inject(self) -> None:
        """Process voice command and inject into event loop (no direct ask)."""
        try:
            self.voice_manager.activation.stop_listening()
            self.voice_manager.stt.start()
            try:
                for text, is_final in self.voice_manager.stt.iter_results():
                    if is_final and text.strip():
                        logger.info(f"Voice input: {text}")
                        self.events.inject_user_input(text.strip())
                        break
            finally:
                self.voice_manager.stt.stop()
        except Exception as e:
            logger.error(f"JARVIS: Voice processing error: {e}")
        finally:
            if self._running and hasattr(self.voice_manager, "activation"):
                self.voice_manager.activation.start_listening()

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
