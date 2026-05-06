"""Dispatch-mode orchestration helpers."""

from __future__ import annotations

import json
import re
import uuid
from logging import Logger
from typing import Any, Optional

from ..config import Config
from ..dispatch.goal_manager import Goal
from .goal_updates import apply_goal_updates
from .llm_bridge import ask_llm
from .output_hooks import emit_activity, get_embeddings, persist_assistant_turn
from .root_context import build_root_context, compact_payload_for_llm

# Signal window text line: "[14:11:04] PID 2 INIT server tool {...}"
_PID_INIT_RE = re.compile(r"PID (\d+) INIT")


def _extract_pids_from_result(result: Any) -> list[int]:
    """
    Pull task PIDs from a send_tasks / wait_task result.

    Tries structured signal dicts first (future-proof); falls back to parsing
    the signal-window text format that the dispatch binary currently returns:
      "Signal window (last N):\n[time] PID N INIT server tool {...}\n..."
    """
    # --- structured path ---
    signals: list[dict] = []
    if isinstance(result, list):
        signals = result
    elif isinstance(result, dict):
        signals = result.get("signals", [])
        if not signals:
            for v in result.values():
                if isinstance(v, list):
                    signals = v
                    break
    structured = [
        s["pid"]
        for s in signals
        if isinstance(s, dict) and s.get("type") == "INIT" and s.get("pid") is not None
    ]
    if structured:
        return structured

    # --- text fallback: parse "PID N INIT" lines from signal window string ---
    text = ""
    if isinstance(result, str):
        text = result
    elif isinstance(result, dict):
        text = result.get("output", "") or ""
    if text:
        return [int(m) for m in _PID_INIT_RE.findall(text)]

    return []


def _signals_from_result(result: Any) -> list[dict]:
    """Return all signal dicts embedded in a tool result."""
    if isinstance(result, list):
        return [s for s in result if isinstance(s, dict)]
    if isinstance(result, dict):
        sigs = result.get("signals", [])
        if isinstance(sigs, list):
            return [s for s in sigs if isinstance(s, dict)]
    return []


def _find_exits_for_pids(window: list[dict], pids: list[int]) -> list[dict]:
    """Return EXIT/TIMEOUT signals from the window that match any of the given PIDs."""
    pid_set = set(pids)
    return [
        s
        for s in window
        if s.get("type") in ("EXIT", "TIMEOUT") and s.get("pid") in pid_set
    ]


async def run_dispatch_subchain(
    app: Any,
    logger: Logger,
    intent: str,
    max_chain_depth: int,
    goal_id: Optional[str] = None,
) -> str:
    """
    Enter dispatch mode, give it the intent, and loop until the LLM
    calls 'done' with a summary.

    goal_id: if provided, the sub-chain operates on behalf of this Goal
    node — linking PIDs, creating subgoals, writing output on completion,
    and always including the goal's scoped context in every LLM turn.
    """
    goal: Optional[Goal] = app.goals.get_goal(goal_id) if goal_id else None

    mode = await app.dispatch.select_discovery_mode(get_embeddings(app))
    dispatch_prompt = (
        Config.LLM_DISPATCH_PROMPT_EMBEDDING
        if mode == "embedding"
        else Config.LLM_DISPATCH_PROMPT_KEYWORD
    )
    app.llm.set_prompt("dispatch", dispatch_prompt)
    app.llm.switch_mode("dispatch")

    # ------------------------------------------------------------------
    # Context builder — always leads with INTENT + current goal state.
    # The LLM never loses its bearings after the first dispatch wakeup.
    # ------------------------------------------------------------------
    def _ctx(result_key: str, payload: Any) -> str:
        parts = [f"INTENT: {intent}"]
        if goal:
            gctx = app.goals.get_goal_context(goal.id)
            if gctx:
                parts.append(f"GOAL_STATE: {compact_payload_for_llm(gctx)}")
        elif app.goals.get_context():
            parts.append(f"GOALS: {json.dumps(app.goals.get_context())}")
        parts.append(f"{result_key}: {compact_payload_for_llm(payload)}")
        return "\n".join(parts)

    # Initial context (before any dispatch)
    context_parts = [f"INTENT: {intent}"]
    if goal:
        gctx = app.goals.get_goal_context(goal.id)
        if gctx:
            context_parts.append(f"GOAL_STATE: {compact_payload_for_llm(gctx)}")
    else:
        goals_ctx = app.goals.get_context()
        if goals_ctx:
            context_parts.append(f"GOALS: {json.dumps(goals_ctx)}")
    context = "\n".join(context_parts)

    # PIDs of the most recently dispatched task batch.
    pending_pids: list[int] = []

    for step in range(max_chain_depth):
        logger.info(
            "JARVIS: Dispatch iteration start "
            f"(step={step}, context_chars={len(context)})"
        )
        emit_activity(app, f"Dispatch step {step + 1}: reasoning…", kind="dispatch")
        response = await ask_llm(app, logger, context, tag=f"dispatch-step-{step}")
        parsed = app.task_parser.parse(response)

        if "error" in parsed:
            logger.warning(f"JARVIS: Dispatch sub-chain parse error: {parsed['error']}")
            return f"Error: {parsed['error']}"

        action = parsed["action"]
        logger.info(f"JARVIS: Dispatch iteration action (step={step}, action={action})")
        apply_goal_updates(app, parsed.get("goal_updates", []))

        # Persist strategy update if the LLM provided one
        if goal and parsed.get("strategy"):
            app.goals.update_strategy(goal.id, parsed["strategy"])

        if action == "done":
            summary = parsed["summary"]
            logger.info(f"JARVIS: Dispatch sub-chain completed: {summary}")
            emit_activity(app, "Dispatch completed.", kind="dispatch")
            if goal:
                app.goals.complete_goal(goal.id, output=summary)
            return summary

        if action == "plan":
            sub_tasks = parsed.get("tasks", [])
            logger.info(f"JARVIS: Plan has {len(sub_tasks)} sub-task(s)")
            emit_activity(
                app,
                f"Planned {len(sub_tasks)} sub-task(s); finding tools…",
                kind="dispatch",
            )

            # Create child goal nodes for each sub-task intent so the tree
            # grows as planning recurses.
            if goal:
                for sub_task in sub_tasks:
                    sub_intent = (
                        sub_task.get("intent")
                        or sub_task.get("description")
                        or str(sub_task)
                    )
                    app.goals.add_subgoal(goal.id, sub_intent)

            available_tools = await app.dispatch.discover_tools(
                tasks=sub_tasks,
                embeddings=get_embeddings(app),
            )

            if available_tools:
                context = _ctx("MATCHED_TOOLS", available_tools)
            else:
                context = _ctx(
                    "NO_TOOLS_FOUND",
                    "No matching tools were found. Re-plan with different sub-task "
                    "intents, or use 'done' if the request cannot be fulfilled.",
                )

        elif action == "search":
            emit_activity(app, "Searching MCP servers…", kind="dispatch")
            result = await app.dispatch.search_servers(parsed["keywords"])
            context = _ctx("SEARCH_RESULTS", result)

        elif action == "list_tools":
            emit_activity(
                app, f"Listing tools for {parsed['server_id']}…", kind="dispatch"
            )
            result = await app.dispatch.list_server_tools(parsed["server_id"])
            context = _ctx("TOOLS", result)

        elif action == "install":
            emit_activity(
                app, f"Installing server {parsed['server_id']}…", kind="dispatch"
            )
            result = await app.dispatch.install_server(parsed["server_id"])
            context = _ctx("INSTALL_RESULT", result)

            server_id = parsed.get("server_id", "")
            if "error" not in result and server_id:
                await app.dispatch.auto_index_server(
                    server_id=server_id,
                    embeddings=get_embeddings(app),
                )

        elif action == "dispatch" and "tasks" in parsed:
            emit_activity(
                app, f"Dispatching {len(parsed['tasks'])} task(s)…", kind="dispatch"
            )
            result = await app._dispatch_send(
                parsed["tasks"],
                session_id=goal.id if goal else None,
            )
            if isinstance(result, dict) and result.get("awaiting_confirmation"):
                logger.info(
                    f"JARVIS: Dispatch sub-chain paused for confirmation "
                    f"id={result['confirmation_id']}"
                )
                emit_activity(
                    app,
                    "Waiting for your confirmation before running tools.",
                    kind="dispatch",
                )
                return "Waiting for user confirmation."
            if isinstance(result, dict) and "error" in result:
                context = _ctx("DISPATCH_ERROR", result)
            else:
                pending_pids = _extract_pids_from_result(result)
                if pending_pids:
                    logger.info(
                        f"JARVIS: Tracking {len(pending_pids)} dispatched PID(s): "
                        f"{pending_pids}"
                    )
                    if goal:
                        app.goals.link_tasks(goal.id, pending_pids)
                emit_activity(app, "Tool results received.", kind="dispatch")
                context = _ctx("DISPATCH_RESULT", result)

        elif action == "wait":
            pids = parsed.get("pids") or pending_pids

            if pids and app.dispatch.is_connected:
                emit_activity(app, "Waiting for tasks to complete…", kind="dispatch")

                current_window = await app.dispatch.get_signal_window()
                already_done = _find_exits_for_pids(current_window, pids)

                if already_done:
                    logger.info(
                        f"JARVIS: PIDs {[s['pid'] for s in already_done]} already "
                        "completed — using EXIT from window directly"
                    )
                    if hasattr(app, "events"):
                        app.events.mark_signals_seen(already_done)
                    context = _ctx("WAIT_RESULT", {"signals": already_done})
                else:
                    logger.info(f"JARVIS: Blocking via wait_task for PIDs {pids}")
                    wait_result = await app.dispatch.wait_task(pids)

                    returned_signals = _signals_from_result(wait_result)
                    if returned_signals and hasattr(app, "events"):
                        app.events.mark_signals_seen(returned_signals)

                    if isinstance(wait_result, dict) and "error" in wait_result:
                        logger.warning(
                            f"JARVIS: wait_task returned error: {wait_result['error']}"
                        )
                        context = _ctx("WAIT_ERROR", wait_result)
                    else:
                        logger.info(
                            f"JARVIS: wait_task completed — "
                            f"{len(returned_signals)} signal(s) returned"
                        )
                        context = _ctx("WAIT_RESULT", wait_result)
            else:
                logger.info(
                    "JARVIS: Dispatch sub-chain wait — no pids, returning to event loop"
                )
                emit_activity(app, "Waiting for tasks to complete…", kind="dispatch")
                return "Waiting for tasks to complete."

        elif action == "kill":
            emit_activity(app, "Stopping selected task(s)…", kind="dispatch")
            await do_kill(app, logger, parsed["pids"])
            context = _ctx("KILL_RESULT", f"Killed PID(s) {parsed['pids']}")

        elif action == "defer":
            emit_activity(
                app,
                f"Setting reminder for {parsed['duration']}s "
                f"(goal {parsed['goal_id']})…",
                kind="dispatch",
            )
            await do_defer(
                app,
                logger,
                parsed["goal_id"],
                parsed["duration"],
                parsed.get("reason", ""),
            )
            return f"Deferred goal {parsed['goal_id']} for {parsed['duration']}s."

        elif action == "respond":
            emit_activity(app, "Composing response…", kind="llm")
            out = parsed["output"]
            app.output_manager.handle_response({"output": out})
            persist_assistant_turn(app, out)
            return out

        else:
            logger.warning(f"JARVIS: Unexpected dispatch action '{action}'")
            return f"Unexpected action: {action}"

    logger.error("JARVIS: Dispatch sub-chain hit max steps")
    return "Tool execution timed out (too many steps)."


async def dispatch_send(
    app: Any,
    logger: Logger,
    tasks: list[dict[str, Any]],
    dispatch_context: Any = None,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """Low-level send to dispatch adapter, gated by TLA confirmation."""
    if not app.dispatch.is_connected:
        emit_activity(app, "Dispatch is unavailable right now.", kind="dispatch")
        return {"error": "Dispatch not connected"}

    approved_tasks: list[dict[str, Any]] = []
    tools_needing_confirmation: list[dict[str, Any]] = []

    for task in tasks:
        tool_name = f"{task.get('server', '?')}.{task.get('tool', '?')}"
        tool_meta = await get_tool_metadata(app, logger, task)

        if app.confirmation.should_confirm(tool_meta):
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

    if not tools_needing_confirmation:
        return await app.dispatch.send_tasks(approved_tasks, session_id=session_id)

    request_id = str(uuid.uuid4())[:8]
    notification_silent = tools_needing_confirmation[0].get(
        "notification_silent",
        Config.NOTIFICATION_SILENT,
    )
    await app.confirmation.request_confirmation(
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
    emit_activity(
        app,
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


async def get_tool_metadata(
    app: Any, logger: Logger, task: dict[str, Any]
) -> dict[str, Any]:
    """Retrieve metadata for a task's tool from the dispatch registry."""
    server_id = task.get("server")
    tool_name = task.get("tool")
    if not server_id or not tool_name:
        return {}
    try:
        tools = await app.dispatch.list_server_tools(server_id)
        if isinstance(tools, dict) and "tools" in tools:
            for tool in tools["tools"]:
                if tool.get("name") == tool_name:
                    return tool
    except Exception as e:
        logger.debug(f"Could not fetch metadata for {server_id}.{tool_name}: {e}")
    return {}


async def dispatch_execute_tasks(
    app: Any,
    logger: Logger,
    tasks: list[dict[str, Any]],
    depth: int,
) -> None:
    """Handle a dispatch action that already has concrete tasks (from root)."""
    if not app.dispatch.is_connected:
        app.output_manager.handle_response(
            {"output": "I can't execute tools right now — dispatch is not connected."}
        )
        return

    result = await dispatch_send(app, logger, tasks)

    if isinstance(result, dict) and result.get("awaiting_confirmation"):
        logger.info(
            f"JARVIS: Root dispatch paused for confirmation "
            f"id={result['confirmation_id']}"
        )
        return

    app.llm.switch_mode("root")
    context = build_root_context(app, logger)
    if isinstance(result, dict) and "error" in result:
        context += f"\nDISPATCH_ERROR: {compact_payload_for_llm(result)}"
    else:
        context += f"\nDISPATCH_RESULT: {compact_payload_for_llm(result)}"

    response = await ask_llm(app, logger, context, tag="root-dispatch-result")
    await app._act_on_root_response(response, depth + 1)


async def do_kill(app: Any, logger: Logger, pids: Any) -> None:
    if not app.dispatch.is_connected:
        return
    result = await app.dispatch.kill_tasks(pids)
    if "error" in result:
        logger.error(f"JARVIS: Kill error: {result['error']}")
    else:
        logger.info(f"JARVIS: Killed PID(s): {pids}")


async def do_defer(
    app: Any,
    logger: Logger,
    goal_id: str,
    duration: int,
    reason: str = "",
) -> None:
    if not app.dispatch.is_connected:
        logger.warning("JARVIS: Dispatch not connected, cannot defer goal")
        emit_activity(
            app, "Cannot set reminder: dispatch not connected.", kind="dispatch"
        )
        app.output_manager.handle_response(
            {"output": "I can't defer goals right now — dispatch is not connected."}
        )
        return

    label = f"goal_reminder:{goal_id}"
    metadata: dict[str, Any] = {"goal_id": goal_id, "type": "goal_defer"}
    if reason:
        metadata["reason"] = reason

    result = await app.dispatch.set_timer(label, duration, metadata)

    if "error" in result:
        logger.error(f"JARVIS: Timer error: {result['error']}")
        emit_activity(app, "Failed to set reminder timer.", kind="dispatch")
    else:
        timer_pid = result.get("pid", 0)
        app.goals.defer_goal(goal_id, timer_pid)
        logger.info(
            f"JARVIS: Deferred goal [{goal_id}] for {duration}s (timer PID {timer_pid})"
        )
        emit_activity(
            app,
            f"Reminder set for {duration}s (goal {goal_id}, timer pid {timer_pid}).",
            kind="dispatch",
        )
