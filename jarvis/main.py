"""
JARVIS — the brain.

Hierarchical orchestrator with three prompt modes:

  ROOT  →  routes to DISPATCH or CONTEXTOR subsystems, or responds directly
  DISPATCH  →  tool discovery and execution sub-chain (search → install → dispatch → done)
  CONTEXTOR →  long-term memory sub-chain (store / recall / done)

Each subsystem runs in its own LLM mode with an isolated conversation
history. When a subsystem finishes (action "done"), its summary is fed
back to the ROOT prompt which decides the next step.

Dual input: voice (wake word) and socket/CLI ("jarvis send") feed the same
event queue. Both can be active simultaneously.
"""

import asyncio
import json
import os
import signal
import sys
import threading
import time
from typing import Dict, Any, Optional, List
from .config import Config
from .core import ComponentFactory
from .core.logger import get_logger
from .dispatch.event_merger import Event, EventType

logger = get_logger(__name__)

MAX_CHAIN_DEPTH = 15


class Jarvis:
    def __init__(self, text_mode=False):
        self.text_mode = text_mode
        self._running = False

        self.components = ComponentFactory.create_all_components(
            text_mode=text_mode,
            on_voice_command=self._handle_voice_command,
        )

        self.llm = self.components['llm']
        self.dispatch = self.components['dispatch_adapter']
        self.contextor = self.components['contextor']
        self.goals = self.components['goal_manager']
        self.events = self.components['event_merger']
        self.task_parser = self.components['task_parser']
        self.output_manager = self.components['output_manager']
        self.confirmation = self.components['confirmation_manager']
        self.voice_manager = self.components.get('voice_manager')
        self._output_clients: List[asyncio.StreamWriter] = []

    # ------------------------------------------------------------------
    # Event-driven main loop
    # ------------------------------------------------------------------

    async def run(self):
        self._running = True

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: self.stop())
            except (ValueError, OSError):
                pass

        try:
            await self.dispatch.connect()
        except Exception as e:
            logger.warning(f"JARVIS: Could not connect to dispatch: {e}")
            logger.info("JARVIS: Running in conversation-only mode")

        user_source = self._await_user_input if self._has_stdin() else None
        self.events.start(
            signal_source=self._await_dispatch_signal,
            user_source=user_source,
        )

        voice_thread: Optional[threading.Thread] = None
        if self.voice_manager:
            voice_thread = threading.Thread(
                target=self._run_voice_activation,
                daemon=True,
                name="jarvis-voice",
            )
            voice_thread.start()
            logger.info("JARVIS: Voice activation started (dual input)")

        socket_task: Optional[asyncio.Task] = None
        if Config.JARVIS_INPUT_SOCKET:
            socket_task = asyncio.create_task(self._run_socket_listener())
            logger.info(f"JARVIS: Socket listener at {Config.JARVIS_INPUT_SOCKET}")

        # Wire confirmation manager's event injector so responses flow
        # through the event loop instead of blocking.
        self.confirmation.set_event_injector(self.events.inject_confirmation_response)

        output_task: Optional[asyncio.Task] = None
        if Config.JARVIS_OUTPUT_SOCKET:
            self.output_manager.add_output_callback(self._on_output_for_broadcast)
            self.confirmation.set_output_callback(
                self._on_output_for_broadcast,
                has_clients=lambda: len(self._output_clients) > 0,
            )
            output_task = asyncio.create_task(self._run_output_socket_listener())
            logger.info(f"JARVIS: Output socket at {Config.JARVIS_OUTPUT_SOCKET}")

        logger.info("JARVIS: Event loop started")

        try:
            async for event in self.events:
                await self._handle_event(event)
        except KeyboardInterrupt:
            logger.info("JARVIS: Interrupted")
        finally:
            if socket_task and not socket_task.done():
                socket_task.cancel()
            if output_task and not output_task.done():
                output_task.cancel()
            await self._shutdown()

    async def _handle_event(self, event: Event):
        if event.type == EventType.USER_INPUT:
            await self._on_user_input(event.data)
        elif event.type == EventType.DISPATCH_SIGNAL:
            await self._on_dispatch_signal(event.data)
        elif event.type == EventType.CONFIRMATION_RESPONSE:
            await self._on_confirmation_response(event.data)

    # ------------------------------------------------------------------
    # ROOT-level handlers
    # ------------------------------------------------------------------

    async def _on_user_input(self, text: str):
        logger.info(f"JARVIS: User input: '{text}'")
        self.goals.add_goal(text)

        self.llm.switch_mode("root")
        context = self._build_root_context(new_input=text)

        response = self._ask_llm(context, tag="root")
        await self._act_on_root_response(response)

    async def _on_dispatch_signal(self, signal: Dict[str, Any]):
        sig_type = signal.get("type")
        sig_pid = signal.get("pid")
        logger.info(f"JARVIS: Dispatch signal: type={sig_type}, pid={sig_pid}")

        self.goals.update_from_signal(signal)

        self.llm.switch_mode("root")
        context = self._build_root_context(signal=signal)

        response = self._ask_llm(context, tag="root")
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
            response = self._ask_llm(context, tag="root-confirmation-denied")
            await self._act_on_root_response(response)
            return

        # Some or all approved — dispatch the approved tasks.
        if pending.approved_tasks:
            result = await self.dispatch.send_tasks(pending.approved_tasks)

            self.llm.switch_mode("root")
            context = self._build_root_context()

            if isinstance(result, dict) and "error" in result:
                context += f"\nDISPATCH_ERROR: {json.dumps(result)}"
            else:
                context += f"\nDISPATCH_RESULT: {json.dumps(result)}"

            # Include partial denial if some tools were denied.
            if pending.denied_tools:
                denied_list = ", ".join(pending.denied_tools)
                context += f"\nUSER_DENIAL: Action {denied_list} was denied by the user"

            response = self._ask_llm(context, tag="root-confirmation-result")
            await self._act_on_root_response(response)

    async def _act_on_root_response(self, response: Dict[str, Any], depth: int = 0):
        """Handle a ROOT-mode LLM response."""
        if depth >= MAX_CHAIN_DEPTH:
            logger.error("JARVIS: Max chain depth reached, forcing respond")
            self.output_manager.handle_response({
                "output": "I got stuck in a loop. Could you try again?",
            })
            return

        parsed = self.task_parser.parse(response)

        if "error" in parsed:
            logger.warning(f"JARVIS: Root parse error: {parsed['error']}")
            self.output_manager.handle_response({
                "output": "I had trouble processing that. Could you try again?",
            })
            return

        action = parsed["action"]
        logger.info(f"JARVIS: Root action='{action}'")

        self._apply_goal_updates(parsed.get("goal_updates", []))

        if action == "respond":
            self.output_manager.handle_response({"output": parsed["output"]})
            dismissed = self.goals.dismiss_completed()
            if dismissed:
                logger.info(f"JARVIS: Dismissed {len(dismissed)} completed goal(s)")
            if Config.RESET_HISTORY_AFTER_RESPONSE:
                self.llm.reset_history()

        elif action == "dispatch":
            if "tasks" in parsed:
                await self._dispatch_execute_tasks(parsed["tasks"], depth)
            else:
                summary = await self._run_dispatch_subchain(parsed["intent"])
                await self._feed_root_summary("DISPATCH_SUMMARY", summary, depth)

        elif action == "contextor":
            if not self.contextor:
                self.output_manager.handle_response({
                    "output": "Memory is disabled. I can't remember or recall information.",
                })
                return
            summary = await self._run_contextor_subchain(parsed["intent"])
            await self._feed_root_summary("CONTEXTOR_SUMMARY", summary, depth)

    async def _feed_root_summary(self, label: str, summary: str, depth: int):
        """Feed a subsystem summary back into ROOT for the next decision."""
        self.llm.switch_mode("root")
        context = self._build_root_context()
        context += f"\n{label}: {summary}"

        response = self._ask_llm(context, tag="root-chain")
        await self._act_on_root_response(response, depth + 1)

    # ------------------------------------------------------------------
    # DISPATCH sub-chain
    # ------------------------------------------------------------------

    async def _run_dispatch_subchain(self, intent: str) -> str:
        """
        Enter dispatch mode, give it the intent, and loop until it
        returns "done" with a summary.
        """
        self.llm.switch_mode("dispatch")

        context_parts = [f"INTENT: {intent}"]
        goals = self.goals.get_context()
        if goals:
            context_parts.append(f"GOALS: {json.dumps(goals)}")
        context = "\n".join(context_parts)

        for step in range(MAX_CHAIN_DEPTH):
            response = self._ask_llm(context, tag=f"dispatch-step-{step}")
            parsed = self.task_parser.parse(response)

            if "error" in parsed:
                logger.warning(f"JARVIS: Dispatch sub-chain parse error: {parsed['error']}")
                return f"Error: {parsed['error']}"

            action = parsed["action"]
            self._apply_goal_updates(parsed.get("goal_updates", []))

            if action == "done":
                logger.info(f"JARVIS: Dispatch sub-chain completed: {parsed['summary']}")
                return parsed["summary"]

            if action == "search":
                result = await self.dispatch.search_servers(parsed["keywords"])
                context = f"SEARCH_RESULTS: {json.dumps(result)}"

            elif action == "list_tools":
                result = await self.dispatch.list_server_tools(parsed["server_id"])
                context = f"TOOLS: {json.dumps(result)}"

            elif action == "install":
                result = await self.dispatch.install_server(parsed["server_id"])
                context = f"INSTALL_RESULT: {json.dumps(result)}"

            elif action == "dispatch" and "tasks" in parsed:
                result = await self._dispatch_send(parsed["tasks"])
                if isinstance(result, dict) and result.get("awaiting_confirmation"):
                    # Non-blocking: confirmation sent, return to event loop.
                    # Dispatch will resume when CONFIRMATION_RESPONSE arrives.
                    logger.info(
                        f"JARVIS: Dispatch sub-chain paused for confirmation "
                        f"id={result['confirmation_id']}"
                    )
                    return "Waiting for user confirmation."
                elif isinstance(result, dict) and "error" in result:
                    context = f"DISPATCH_ERROR: {json.dumps(result)}"
                else:
                    context = f"DISPATCH_RESULT: {json.dumps(result)}"

            elif action == "wait":
                logger.info("JARVIS: Dispatch sub-chain waiting")
                return "Waiting for tasks to complete."

            elif action == "kill":
                await self._do_kill(parsed["pids"])
                context = f"KILL_RESULT: Killed PID(s) {parsed['pids']}"

            elif action == "defer":
                await self._do_defer(
                    parsed["goal_id"], parsed["duration"], parsed.get("reason", ""),
                )
                return f"Deferred goal {parsed['goal_id']} for {parsed['duration']}s."

            elif action == "respond":
                self.output_manager.handle_response({"output": parsed["output"]})
                return parsed["output"]

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
            return {"error": "Dispatch not connected"}

        approved_tasks = []
        tools_needing_confirmation = []

        for task in tasks:
            tool_name = f"{task.get('server', '?')}.{task.get('tool', '?')}"
            tool_meta = await self._get_tool_metadata(task)

            if self.confirmation.should_confirm(tool_meta):
                notification_silent = tool_meta.get(
                    "notification_silent", Config.NOTIFICATION_SILENT,
                )
                tools_needing_confirmation.append({
                    "tool_name": tool_name,
                    "task": task,
                    "params": task.get("params", {}),
                    "notification_silent": notification_silent,
                })
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
            "notification_silent", Config.NOTIFICATION_SILENT,
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
            self.output_manager.handle_response({
                "output": "I can't execute tools right now — dispatch is not connected.",
            })
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
            context += f"\nDISPATCH_ERROR: {json.dumps(result)}"
        else:
            context += f"\nDISPATCH_RESULT: {json.dumps(result)}"

        response = self._ask_llm(context, tag="root-dispatch-result")
        await self._act_on_root_response(response, depth + 1)

    # ------------------------------------------------------------------
    # CONTEXTOR sub-chain — long-term memory
    # ------------------------------------------------------------------

    async def _run_contextor_subchain(self, intent: str) -> str:
        """
        Enter contextor mode, give it the intent, and loop until it
        returns "done" with a summary.
        """
        self.llm.switch_mode("contextor")

        context = f"INTENT: {intent}"

        for step in range(MAX_CHAIN_DEPTH):
            response = self._ask_llm(context, tag=f"contextor-step-{step}")
            parsed = self.task_parser.parse(response)

            if "error" in parsed:
                logger.warning(f"JARVIS: Contextor sub-chain parse error: {parsed['error']}")
                return f"Error: {parsed['error']}"

            action = parsed["action"]

            if action == "done":
                logger.info(f"JARVIS: Contextor sub-chain completed: {parsed['summary']}")
                return parsed["summary"]

            if action == "store":
                result = self.contextor.store(parsed["theme"], parsed["content"])
                context = f"STORE_RESULT: {json.dumps(result)}"

            elif action == "recall":
                result = self.contextor.recall(parsed["theme"])
                context = f"RECALL_RESULT: {json.dumps(result)}"

            elif action == "search_memory":
                result = self.contextor.search(parsed["keywords"])
                context = f"SEARCH_MEMORY_RESULT: {json.dumps(result)}"

            elif action == "list_memory":
                result = self.contextor.list_themes()
                context = f"LIST_MEMORY_RESULT: {json.dumps(result)}"

            else:
                logger.warning(f"JARVIS: Unexpected contextor action '{action}'")
                return f"Unexpected action: {action}"

        logger.error("JARVIS: Contextor sub-chain hit max steps")
        return "Memory operation timed out."

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _ask_llm(self, context: str, tag: str = "") -> Dict[str, Any]:
        """Single LLM call with timing logs."""
        logger.info(f"JARVIS [{tag}]: Calling LLM (mode={self.llm.mode})...")
        logger.debug(f"JARVIS [{tag}]: LLM context:\n{context}")

        t0 = time.perf_counter()
        response = self.llm.ask(context)
        elapsed = time.perf_counter() - t0

        logger.info(f"JARVIS [{tag}]: LLM responded in {elapsed:.2f}s")
        logger.debug(f"JARVIS [{tag}]: LLM raw response: {response}")
        return response

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

        # Tier 3 (Cold): RAG retrieval — inject relevant memories from
        # the vector store based on the current user input.
        # This happens here (in addition to the context manager's system
        # prompt augmentation) so the ROOT context string itself contains
        # relevant memories for the LLM to reference.
        if new_input and self.contextor:
            rag_context = self.contextor.retrieve_context(
                query=new_input,
                top_k=getattr(Config, "RAG_TOP_K", 5),
                min_score=getattr(Config, "RAG_MIN_SCORE", 0.3),
            )
            if rag_context:
                parts.append(rag_context)

        if new_input:
            parts.append(f"NEW INPUT: {new_input}")

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
            self.output_manager.handle_response({
                "output": "I can't defer goals right now — dispatch is not connected.",
            })
            return

        label = f"goal_reminder:{goal_id}"
        metadata = {"goal_id": goal_id, "type": "goal_defer"}
        if reason:
            metadata["reason"] = reason

        result = await self.dispatch.set_timer(label, duration, metadata)

        if "error" in result:
            logger.error(f"JARVIS: Timer error: {result['error']}")
        else:
            timer_pid = result.get("pid", 0)
            self.goals.defer_goal(goal_id, timer_pid)
            logger.info(f"JARVIS: Deferred goal [{goal_id}] for {duration}s (timer PID {timer_pid})")

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
                vm.activation.on_wake_word = lambda: setattr(vm, "_wake_word_detected", True)
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
        """Listen on Unix socket for text input (jarvis send, apps)."""
        path = Config.JARVIS_INPUT_SOCKET
        if not path:
            return
        sock_dir = os.path.dirname(path)
        os.makedirs(sock_dir, exist_ok=True)
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        server = await asyncio.start_unix_server(
            self._handle_socket_connection,
            path=path,
        )
        try:
            if os.path.exists(path):
                try:
                    os.chmod(path, 0o660)
                except OSError:
                    pass
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            server.close()
            await server.wait_closed()
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    async def _handle_socket_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle one socket connection; read lines and inject.

        Lines that parse as JSON with ``"type": "confirmation_response"``
        are routed to the ConfirmationManager instead of the event queue.
        """
        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                # Check for confirmation responses — inject into event loop.
                if text.startswith("{"):
                    try:
                        msg = json.loads(text)
                        if msg.get("type") == "confirmation_response":
                            self.events.inject_confirmation_response(msg)
                            continue
                    except json.JSONDecodeError:
                        pass

                self.events.inject_user_input(text)
                logger.info(f"JARVIS: Socket input: {text[:80]}...")
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _on_output_for_broadcast(self, response: Dict[str, Any]) -> None:
        """Callback: schedule broadcast to output socket clients."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast_to_output_clients(response))
        except RuntimeError:
            pass

    async def _broadcast_to_output_clients(self, response: Dict[str, Any]) -> None:
        """Send response as JSON line to all connected output clients."""
        line = json.dumps(response, ensure_ascii=False) + "\n"
        data = line.encode("utf-8")
        dead: List[asyncio.StreamWriter] = []
        for w in self._output_clients:
            try:
                w.write(data)
                await w.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                dead.append(w)
        for w in dead:
            if w in self._output_clients:
                self._output_clients.remove(w)
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass

    async def _run_output_socket_listener(self) -> None:
        """Listen on Unix socket for output subscribers (apps, widgets)."""
        path = Config.JARVIS_OUTPUT_SOCKET
        if not path:
            return
        sock_dir = os.path.dirname(path)
        os.makedirs(sock_dir, exist_ok=True)
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        server = await asyncio.start_unix_server(
            self._handle_output_connection,
            path=path,
        )
        try:
            if os.path.exists(path):
                try:
                    os.chmod(path, 0o660)
                except OSError:
                    pass
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            self._output_clients.clear()
            server.close()
            await server.wait_closed()
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    async def _handle_output_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle output subscriber: add to list, wait for disconnect."""
        self._output_clients.append(writer)
        logger.info(f"JARVIS: Output subscriber connected ({len(self._output_clients)} total)")
        try:
            await reader.read()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            if writer in self._output_clients:
                self._output_clients.remove(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            logger.debug("JARVIS: Output subscriber disconnected")

    async def _await_user_input(self) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, input, "",
        )

    async def _await_dispatch_signal(self) -> Optional[Dict[str, Any]]:
        if not self.dispatch.is_connected:
            await asyncio.sleep(1)
            return None

        signals = await self.dispatch.get_signal_window()
        if signals:
            latest = signals[-1]
            logger.debug(
                f"JARVIS: Received {len(signals)} signal(s), forwarding latest: "
                f"type={latest.get('type')}, pid={latest.get('pid')}",
            )
            return latest

        await asyncio.sleep(0.5)
        return None

    # ------------------------------------------------------------------
    # Synchronous / legacy interface
    # ------------------------------------------------------------------

    def ask(self, prompt: str) -> Dict[str, Any]:
        """Synchronous single-prompt interface for one-shot CLI usage."""
        logger.info(f"JARVIS: Processing: '{prompt}'")

        self.goals.add_goal(prompt)
        self.llm.switch_mode("root")
        context = self._build_root_context(new_input=prompt)

        response = self._ask_llm(context, tag="ask")
        parsed = self.task_parser.parse(response)

        if "error" in parsed:
            result = {"output": "I had trouble processing that. Could you try again?"}
        elif parsed["action"] == "respond":
            result = {"output": parsed["output"]}
        else:
            result = {"output": f"Action: {parsed.get('action', 'unknown')}"}

        self.output_manager.handle_response(result)

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
