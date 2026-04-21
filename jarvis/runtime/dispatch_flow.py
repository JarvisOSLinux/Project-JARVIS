"""Dispatch-mode orchestration helpers."""

from __future__ import annotations

import json
import uuid
from logging import Logger
from typing import Any

from ..config import Config
from .goal_updates import apply_goal_updates
from .root_context import build_root_context, compact_payload_for_llm


async def run_dispatch_subchain(
    app: Any,
    logger: Logger,
    intent: str,
    max_chain_depth: int,
) -> str:
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
    mode = await app.dispatch.select_discovery_mode(app._get_embeddings())
    dispatch_prompt = (
        Config.LLM_DISPATCH_PROMPT_EMBEDDING
        if mode == "embedding"
        else Config.LLM_DISPATCH_PROMPT_KEYWORD
    )
    app.llm.set_prompt("dispatch", dispatch_prompt)

    app.llm.switch_mode("dispatch")

    context_parts = [f"INTENT: {intent}"]
    goals = app.goals.get_context()
    if goals:
        context_parts.append(f"GOALS: {json.dumps(goals)}")
    context = "\n".join(context_parts)

    for step in range(max_chain_depth):
        logger.info(
            "JARVIS: Dispatch iteration start "
            f"(step={step}, context_chars={len(context)})"
        )
        app._activity(f"Dispatch step {step + 1}: reasoning…", kind="dispatch")
        response = await app._ask_llm(context, tag=f"dispatch-step-{step}")
        parsed = app.task_parser.parse(response)

        if "error" in parsed:
            logger.warning(f"JARVIS: Dispatch sub-chain parse error: {parsed['error']}")
            return f"Error: {parsed['error']}"

        action = parsed["action"]
        logger.info(f"JARVIS: Dispatch iteration action (step={step}, action={action})")
        apply_goal_updates(app, parsed.get("goal_updates", []))

        if action == "done":
            logger.info(f"JARVIS: Dispatch sub-chain completed: {parsed['summary']}")
            app._activity("Dispatch completed.", kind="dispatch")
            return parsed["summary"]

        if action == "plan":
            # LLM split the intent into sub-tasks — search for tools
            sub_tasks = parsed.get("tasks", [])
            logger.info(f"JARVIS: Plan has {len(sub_tasks)} sub-task(s)")
            app._activity(
                f"Planned {len(sub_tasks)} sub-task(s); finding tools…",
                kind="dispatch",
            )

            available_tools = await app.dispatch.discover_tools(
                tasks=sub_tasks,
                embeddings=app._get_embeddings(),
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
            app._activity("Searching MCP servers…", kind="dispatch")
            result = await app.dispatch.search_servers(parsed["keywords"])
            context = f"SEARCH_RESULTS: {compact_payload_for_llm(result)}"

        elif action == "list_tools":
            app._activity(f"Listing tools for {parsed['server_id']}…", kind="dispatch")
            result = await app.dispatch.list_server_tools(parsed["server_id"])
            context = f"TOOLS: {compact_payload_for_llm(result)}"

        elif action == "install":
            app._activity(f"Installing server {parsed['server_id']}…", kind="dispatch")
            result = await app.dispatch.install_server(parsed["server_id"])
            context = f"INSTALL_RESULT: {compact_payload_for_llm(result)}"

            # Auto-index non-approved servers after successful install
            server_id = parsed.get("server_id", "")
            if "error" not in result and server_id:
                await app.dispatch.auto_index_server(
                    server_id=server_id,
                    embeddings=app._get_embeddings(),
                )

        elif action == "dispatch" and "tasks" in parsed:
            app._activity(
                f"Dispatching {len(parsed['tasks'])} task(s)…", kind="dispatch"
            )
            result = await app._dispatch_send(parsed["tasks"])
            if isinstance(result, dict) and result.get("awaiting_confirmation"):
                # Non-blocking: confirmation sent, return to event loop.
                # Dispatch will resume when CONFIRMATION_RESPONSE arrives.
                logger.info(
                    f"JARVIS: Dispatch sub-chain paused for confirmation "
                    f"id={result['confirmation_id']}"
                )
                app._activity(
                    "Waiting for your confirmation before running tools.",
                    kind="dispatch",
                )
                return "Waiting for user confirmation."
            if isinstance(result, dict) and "error" in result:
                context = f"DISPATCH_ERROR: {compact_payload_for_llm(result)}"
            else:
                app._activity("Tool results received.", kind="dispatch")
                context = f"DISPATCH_RESULT: {compact_payload_for_llm(result)}"

        elif action == "wait":
            logger.info("JARVIS: Dispatch sub-chain waiting")
            app._activity("Waiting for tasks to complete…", kind="dispatch")
            return "Waiting for tasks to complete."

        elif action == "kill":
            app._activity("Stopping selected task(s)…", kind="dispatch")
            await do_kill(app, logger, parsed["pids"])
            context = f"KILL_RESULT: Killed PID(s) {parsed['pids']}"

        elif action == "defer":
            app._activity(
                f"Setting reminder for {parsed['duration']}s (goal {parsed['goal_id']})…",
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
            app._activity("Composing response…", kind="llm")
            out = parsed["output"]
            app.output_manager.handle_response({"output": out})
            app._persist_assistant_turn(out)
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
) -> dict[str, Any]:
    """Low-level send to dispatch adapter, gated by TLA confirmation."""
    if not app.dispatch.is_connected:
        app._activity("Dispatch is unavailable right now.", kind="dispatch")
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

    # No confirmation needed — dispatch everything now.
    if not tools_needing_confirmation:
        return await app.dispatch.send_tasks(approved_tasks)

    # Some tools need confirmation — stash and notify, return immediately.
    request_id = str(uuid.uuid4())[:8]

    # Use the first tool's notification_silent preference for the batch.
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
    app._activity(
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
            {
                "output": "I can't execute tools right now — dispatch is not connected.",
            }
        )
        return

    result = await dispatch_send(app, logger, tasks)

    # If awaiting confirmation, return to event loop — the
    # CONFIRMATION_RESPONSE event will resume this flow.
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

    response = await app._ask_llm(context, tag="root-dispatch-result")
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
        app._activity("Cannot set reminder: dispatch not connected.", kind="dispatch")
        app.output_manager.handle_response(
            {
                "output": "I can't defer goals right now — dispatch is not connected.",
            }
        )
        return

    label = f"goal_reminder:{goal_id}"
    metadata: dict[str, Any] = {"goal_id": goal_id, "type": "goal_defer"}
    if reason:
        metadata["reason"] = reason

    result = await app.dispatch.set_timer(label, duration, metadata)

    if "error" in result:
        logger.error(f"JARVIS: Timer error: {result['error']}")
        app._activity("Failed to set reminder timer.", kind="dispatch")
    else:
        timer_pid = result.get("pid", 0)
        app.goals.defer_goal(goal_id, timer_pid)
        logger.info(
            f"JARVIS: Deferred goal [{goal_id}] for {duration}s (timer PID {timer_pid})"
        )
        app._activity(
            f"Reminder set for {duration}s (goal {goal_id}, timer pid {timer_pid}).",
            kind="dispatch",
        )
