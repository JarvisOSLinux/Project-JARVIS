"""
JARVIS — the brain.

This is the single orchestrator that ties all components together.
It runs an async event loop that reacts to two input sources:
1. User input (text or voice)
2. Dispatch signals (task completions, reminders, failures)

No other class makes decisions. LLM is pure inference, DispatchAdapter
is pure transport, GoalManager is pure state, EventMerger is pure
multiplexing. Jarvis is the only one that knows the workflow.
"""

import asyncio
import json
import time
from typing import Dict, Any, Optional
from .config import Config
from .core import ComponentFactory
from .core.logger import get_logger
from .dispatch.event_merger import Event, EventType

logger = get_logger(__name__)


class Jarvis:
    def __init__(self, text_mode=False):
        """
        Initialize JARVIS AI Assistant.

        Args:
            text_mode: If True, skip voice input components (STT, Voice Activation)
                       for CLI text-only mode.
        """
        self.text_mode = text_mode
        self._running = False

        # Create all components using factory
        self.components = ComponentFactory.create_all_components(
            text_mode=text_mode,
            on_voice_command=self._handle_voice_command,
        )

        # Extract components for easy access
        self.llm = self.components['llm']
        self.dispatch = self.components['dispatch_adapter']
        self.goals = self.components['goal_manager']
        self.events = self.components['event_merger']
        self.task_parser = self.components['task_parser']
        self.output_manager = self.components['output_manager']

        # Voice manager only exists in voice mode
        self.voice_manager = self.components.get('voice_manager')

    # ------------------------------------------------------------------
    # Event-driven main loop
    # ------------------------------------------------------------------

    async def run(self):
        """
        Main event loop. Listens for user input and dispatch signals,
        wakes the LLM when either arrives, and acts on its decision.
        """
        self._running = True

        # Connect to dispatch
        try:
            await self.dispatch.connect()
        except Exception as e:
            logger.warning(f"JARVIS: Could not connect to dispatch: {e}")
            logger.info("JARVIS: Running in conversation-only mode")

        # Start event merger with input sources
        self.events.start(
            user_source=self._await_user_input,
            signal_source=self._await_dispatch_signal,
        )

        logger.info("JARVIS: Event loop started")

        try:
            async for event in self.events:
                await self._handle_event(event)
        except KeyboardInterrupt:
            logger.info("JARVIS: Interrupted")
        finally:
            await self._shutdown()

    async def _handle_event(self, event: Event):
        """Route an event to the appropriate handler."""
        if event.type == EventType.USER_INPUT:
            await self._on_user_input(event.data)
        elif event.type == EventType.DISPATCH_SIGNAL:
            await self._on_dispatch_signal(event.data)

    async def _on_user_input(self, text: str):
        """Handle new user input — add as goal, ask LLM what to do."""
        logger.info(f"JARVIS: User input: '{text}'")

        self.goals.add_goal(text)

        context = self._build_context(new_input=text)
        logger.debug(f"JARVIS: LLM context:\n{context}")

        t0 = time.perf_counter()
        response = self.llm.ask(context)
        elapsed = time.perf_counter() - t0
        logger.info(f"JARVIS: LLM responded in {elapsed:.2f}s")
        logger.debug(f"JARVIS: LLM raw response: {response}")

        await self._act_on_response(response)

    async def _on_dispatch_signal(self, signal: Dict[str, Any]):
        """Handle a dispatch signal — update goals, ask LLM what to do."""
        logger.info(f"JARVIS: Dispatch signal: type={signal.get('type')}, pid={signal.get('pid')}, data={signal.get('data', '')}")

        self.goals.update_from_signal(signal)

        context = self._build_context(signal=signal)
        logger.debug(f"JARVIS: LLM context:\n{context}")

        t0 = time.perf_counter()
        response = self.llm.ask(context)
        elapsed = time.perf_counter() - t0
        logger.info(f"JARVIS: LLM responded in {elapsed:.2f}s")
        logger.debug(f"JARVIS: LLM raw response: {response}")

        await self._act_on_response(response)

    async def _act_on_response(self, response: Dict[str, Any]):
        """Parse LLM response and execute the chosen action."""
        parsed = self.task_parser.parse(response)

        if "error" in parsed:
            logger.warning(f"JARVIS: LLM response parse error: {parsed['error']}")
            logger.debug(f"JARVIS: Raw response that failed parsing: {parsed.get('raw', response)}")
            self.output_manager.handle_response({
                "output": "I had trouble processing that. Could you try again?"
            })
            return

        action = parsed["action"]
        logger.info(f"JARVIS: Parsed action='{action}'")

        goal_updates = parsed.get("goal_updates", [])
        if goal_updates:
            logger.info(f"JARVIS: Applying {len(goal_updates)} goal update(s): {goal_updates}")
        self._apply_goal_updates(goal_updates)

        if action == "dispatch":
            await self._do_dispatch(parsed["tasks"])

        elif action == "respond":
            self.output_manager.handle_response({"output": parsed["output"]})
            # Dismiss completed goals after responding
            dismissed = self.goals.dismiss_completed()
            if dismissed:
                logger.info(f"JARVIS: Dismissed {len(dismissed)} completed goal(s)")

        elif action == "wait":
            logger.info("JARVIS: LLM chose to wait")

        elif action == "kill":
            await self._do_kill(parsed["pids"])

        elif action == "defer":
            await self._do_defer(parsed["goal_id"], parsed["duration"], parsed.get("reason", ""))

    async def _do_dispatch(self, tasks):
        """Send tasks to dispatch and link PIDs to goals."""
        if not self.dispatch.is_connected:
            logger.warning("JARVIS: Dispatch not connected, cannot send tasks")
            self.output_manager.handle_response({
                "output": "I can't execute tools right now — dispatch is not connected."
            })
            return

        result = await self.dispatch.send_tasks(tasks)
        if "error" in result:
            logger.error(f"JARVIS: Dispatch error: {result['error']}")
        else:
            pids = result.get("pids", result.get("pid", "N/A"))
            logger.info(f"JARVIS: Dispatched {len(tasks)} task(s), assigned PIDs: {pids}")

    async def _do_kill(self, pids):
        """Kill tasks via dispatch."""
        if not self.dispatch.is_connected:
            return

        result = await self.dispatch.kill_tasks(pids)
        if "error" in result:
            logger.error(f"JARVIS: Kill error: {result['error']}")
        else:
            logger.info(f"JARVIS: Killed PID(s): {pids}")

    async def _do_defer(self, goal_id: str, duration: int, reason: str = ""):
        """Defer a goal by setting a timer in dispatch."""
        if not self.dispatch.is_connected:
            logger.warning("JARVIS: Dispatch not connected, cannot defer goal")
            self.output_manager.handle_response({
                "output": "I can't defer goals right now — dispatch is not connected."
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
            # Extract timer PID from result
            timer_pid = result.get("pid", 0)
            self.goals.defer_goal(goal_id, timer_pid)
            logger.info(f"JARVIS: Deferred goal [{goal_id}] for {duration}s (timer PID {timer_pid})")

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    def _build_context(
        self,
        new_input: Optional[str] = None,
        signal: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Build the context string sent to the LLM.

        Includes: active goals, recent signals, and any new user input.
        """
        parts = []

        # Goals
        active_goals = self.goals.get_context()
        if active_goals:
            parts.append(f"GOALS: {json.dumps(active_goals)}")

        # Signal
        if signal:
            parts.append(f"SIGNAL: {json.dumps(signal)}")

        # New user input
        if new_input:
            parts.append(f"NEW INPUT: {new_input}")

        return "\n".join(parts) if parts else "No active context."

    def _apply_goal_updates(self, updates):
        """Apply goal status updates from LLM response."""
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
            elif status == "deferred":
                # Deferral is handled by the defer action; this is just
                # a status acknowledgement from the LLM.
                pass

    # ------------------------------------------------------------------
    # Input sources (fed to EventMerger)
    # ------------------------------------------------------------------

    async def _await_user_input(self) -> str:
        """Async source for user input (text mode)."""
        return await asyncio.get_event_loop().run_in_executor(
            None, input, ""
        )

    async def _await_dispatch_signal(self) -> Optional[Dict[str, Any]]:
        """Async source for dispatch signals."""
        if not self.dispatch.is_connected:
            await asyncio.sleep(1)
            return None

        signals = await self.dispatch.get_signal_window()
        if signals:
            latest = signals[-1]
            logger.debug(f"JARVIS: Received {len(signals)} signal(s), forwarding latest: type={latest.get('type')}, pid={latest.get('pid')}")
            return latest

        await asyncio.sleep(0.5)
        return None

    # ------------------------------------------------------------------
    # Synchronous / legacy interface
    # ------------------------------------------------------------------

    def ask(self, prompt: str) -> Dict[str, Any]:
        """
        Synchronous single-prompt interface.

        Sends a prompt to the LLM without the full event loop.
        Useful for one-shot CLI usage and testing.
        """
        logger.info(f"JARVIS: Processing: '{prompt}'")

        self.goals.add_goal(prompt)
        context = self._build_context(new_input=prompt)
        logger.debug(f"JARVIS: LLM context:\n{context}")

        t0 = time.perf_counter()
        response = self.llm.ask(context)
        elapsed = time.perf_counter() - t0
        logger.info(f"JARVIS: LLM responded in {elapsed:.2f}s")
        logger.debug(f"JARVIS: LLM raw response: {response}")

        parsed = self.task_parser.parse(response)
        logger.info(f"JARVIS: Parsed action='{parsed.get('action', 'error')}'")

        if "error" in parsed:
            logger.warning(f"JARVIS: Parse error: {parsed['error']}")
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
        """Handle voice command from voice manager."""
        response = self.ask(prompt=text)
        logger.info(f"Response: {response['output']}")
        return response

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _shutdown(self):
        """Clean shutdown."""
        self._running = False
        await self.events.stop()
        await self.dispatch.disconnect()
        logger.info("JARVIS: Shutdown complete")

    def listen_with_activation(self):
        """Listen with voice activation (wake word detection)."""
        if not self.voice_manager:
            logger.error("Voice manager not available in text mode")
            return
        self.voice_manager.start_voice_activation_mode()

    def listen(self):
        """Legacy continuous listening mode (without wake word detection)."""
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
