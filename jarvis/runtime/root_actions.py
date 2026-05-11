"""ROOT-mode response action handling helpers."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any

from ..config import Config
from .dispatch_flow import dispatch_send
from .goal_updates import apply_goal_updates
from .llm_bridge import ask_llm
from .output_hooks import emit_activity, get_embeddings, persist_assistant_turn
from .root_context import build_root_context, compact_payload_for_llm


async def feed_root_summary(
    app: Any,
    logger: Logger,
    label: str,
    summary: str,
    depth: int,
) -> None:
    """Feed a subsystem summary back into ROOT for the next decision."""
    app.llm.switch_mode("root")
    context = build_root_context(app, logger)
    context += f"\n{label}: {summary}"

    response = await ask_llm(app, logger, context, tag="root-chain")
    await app._act_on_root_response(response, depth + 1)


async def _continue_root(
    app: Any,
    logger: Logger,
    extra: str,
    depth: int,
    tag: str,
    max_chain_depth: int,
) -> None:
    """Re-enter ROOT with extra context injected (used by run handler)."""
    app.llm.switch_mode("root")
    context = build_root_context(app, logger)
    context += f"\n{extra}"
    response = await ask_llm(app, logger, context, tag=tag)
    await app._act_on_root_response(response, depth + 1)


async def _run_handle(
    app: Any,
    logger: Logger,
    parsed: dict,
    depth: int,
    max_chain_depth: int,
) -> None:
    """Handle the 'run' action: discover → (auto-install) → dispatch → wait → respond."""
    from ..dispatch.tool_discovery import discover_tools as _discover_tools

    intent = parsed.get("intent", "")
    emit_activity(app, f"Running: {intent[:60]}…", kind="dispatch")

    # Phase 1: discover tools
    tool_results = await _discover_tools(
        adapter=app.dispatch,
        logger=logger,
        tasks=[{"intent": intent}],
        embeddings=get_embeddings(app),
    )

    if not tool_results:
        logger.warning(f"JARVIS: run — no tools found for '{intent}'")
        await _continue_root(
            app, logger,
            f"NO_TOOLS_FOUND: Could not find any tool for '{intent}'. "
            "MUST retry with a more specific or different intent. "
            "Only give up and respond to the user after 2+ retries.",
            depth, "root-run-no-tools", max_chain_depth,
        )
        return

    # Phase 1b: if only CANDIDATE_SERVERS returned, auto-install the first one
    if "MATCHED_TOOLS" not in tool_results and "CANDIDATE_SERVERS" in tool_results:
        server_id = _first_candidate_id(tool_results)
        if server_id:
            emit_activity(app, f"Installing {server_id}…", kind="dispatch")
            install_result = await app.dispatch.install_server(server_id)
            if "error" not in install_result:
                logger.info(f"JARVIS: run — auto-installed '{server_id}'")
                await app.dispatch.auto_index_server(
                    server_id=server_id,
                    embeddings=get_embeddings(app),
                )
                # Re-discover now that server is installed
                tool_results = await _discover_tools(
                    adapter=app.dispatch,
                    logger=logger,
                    tasks=[{"intent": intent}],
                    embeddings=get_embeddings(app),
                )
            else:
                logger.warning(
                    f"JARVIS: run — auto-install failed for '{server_id}': "
                    f"{install_result.get('error')}"
                )

    if not tool_results or "MATCHED_TOOLS" not in tool_results:
        await _continue_root(
            app, logger,
            f"NO_TOOLS_FOUND: Could not find any installed tool for '{intent}'. "
            "MUST retry with a more specific or different intent. "
            "Only give up and respond to the user after 2+ retries.",
            depth, "root-run-no-tools-after-install", max_chain_depth,
        )
        return

    # Phase 2: hidden LLM dispatch call — produce concrete tasks
    dispatch_context = build_root_context(app, logger)
    dispatch_context += f"\n{tool_results}"
    dispatch_context += (
        f"\nSYSTEM: Tools found for '{intent}'. "
        "Output a dispatch action with concrete tasks now. "
        "Use only tool names from MATCHED_TOOLS above. "
        "Format: {{\"action\": \"dispatch\", \"tasks\": [{{\"server\": \"<id>\", "
        "\"tool\": \"<name>\", \"params\": {{}}}}]}}"
    )
    app.llm.switch_mode("root")
    dispatch_response = await ask_llm(app, logger, dispatch_context, tag="root-run-dispatch")
    dispatch_parsed = app.task_parser.parse(dispatch_response)

    tasks = dispatch_parsed.get("tasks") if dispatch_parsed.get("action") == "dispatch" else None
    if not tasks:
        logger.warning("JARVIS: run — dispatch step didn't yield tasks; falling back")
        await _continue_root(app, logger, tool_results, depth, "root-run-fallback", max_chain_depth)
        return

    # Phase 3: execute dispatch
    result = await dispatch_send(app, logger, tasks)
    if isinstance(result, dict) and result.get("awaiting_confirmation"):
        return

    # Phase 4: auto-wait if dispatch returned PIDs
    pids = []
    if isinstance(result, dict):
        pids = result.get("pids", [])

    if pids and app.dispatch.is_connected:
        app._pending_dispatch_pids = pids
        wait_result = await app.dispatch.wait_task(pids)
        extra = f"WAIT_RESULT: {compact_payload_for_llm(wait_result)}"
    elif isinstance(result, dict) and "error" in result:
        extra = f"DISPATCH_ERROR: {compact_payload_for_llm(result)}"
    else:
        extra = f"DISPATCH_RESULT: {compact_payload_for_llm(result)}"

    await _continue_root(app, logger, extra, depth, "root-run-result", max_chain_depth)


def _first_candidate_id(tool_results: str) -> str:
    """Extract the first server ID from a CANDIDATE_SERVERS block."""
    in_candidates = False
    for line in tool_results.splitlines():
        if line.startswith("CANDIDATE_SERVERS"):
            in_candidates = True
            continue
        if in_candidates and line.startswith("  ") and not line.startswith("   "):
            candidate_id = line.strip().split()[0]
            if candidate_id:
                return candidate_id
    return ""


async def act_on_root_response(
    app: Any,
    logger: Logger,
    response: dict[str, Any],
    depth: int,
    max_chain_depth: int,
) -> None:
    """Handle a ROOT-mode LLM response."""
    if depth >= max_chain_depth:
        logger.error("JARVIS: Max chain depth reached, forcing respond")
        app.output_manager.handle_response(
            {
                "output": "I got stuck in a loop. Could you try again?",
            }
        )
        return

    parsed = app.task_parser.parse(response)

    if "error" in parsed:
        logger.warning(f"JARVIS: Root parse error: {parsed['error']}")
        app.output_manager.handle_response(
            {
                "output": "I had trouble processing that. Could you try again?",
            }
        )
        return

    action = parsed["action"]
    logger.info(f"JARVIS: Root action='{action}'")
    if action == "respond":
        emit_activity(app, "Composing response…", kind="llm")
    elif action == "dispatch":
        emit_activity(app, "Planning tool execution…", kind="dispatch")
    elif action in ("store", "recall", "search_memory", "list_memory"):
        emit_activity(app, f"Running memory action: {action}", kind="memory")

    apply_goal_updates(app, parsed.get("goal_updates", []))

    if action == "run":
        await _run_handle(app, logger, parsed, depth, max_chain_depth)
        return

    if action == "respond":
        output = parsed["output"]
        if not output.strip():
            logger.warning("JARVIS: LLM returned empty respond — retrying")
            context = build_root_context(app, logger)
            context += "\nYour previous response had an empty output. Please respond to the user."
            retry_response = await ask_llm(app, logger, context, tag="root-retry-empty")
            await app._act_on_root_response(retry_response, depth + 1)
            return
        app.output_manager.handle_response({"output": output})
        persist_assistant_turn(app, output)
        dismissed = app.goals.dismiss_completed()
        if dismissed:
            logger.info(f"JARVIS: Dismissed {len(dismissed)} completed goal(s)")
        if Config.RESET_HISTORY_AFTER_RESPONSE:
            app.llm.reset_history()

    elif action == "dispatch":
        if "tasks" in parsed:
            await app._dispatch_execute_tasks(parsed["tasks"], depth)
        else:
            summary = await app._run_dispatch_subchain(parsed["intent"])
            await feed_root_summary(app, logger, "DISPATCH_SUMMARY", summary, depth)

    # -- Memory actions (direct, no sub-chain) --
    elif action == "store":
        if not app.contextor:
            await feed_root_summary(
                app,
                logger,
                "STORE_RESULT",
                json.dumps({"error": "Memory is disabled"}),
                depth,
            )
            return
        # Global scope when LLM explicitly sets scope="global";
        # otherwise file under the active session.
        scope = parsed.get("scope", "session")
        sid = None if scope == "global" else app.sessions.current_id
        result = app.contextor.store(
            parsed["theme"],
            parsed["content"],
            session_id=sid,
        )
        await feed_root_summary(
            app,
            logger,
            "STORE_RESULT",
            compact_payload_for_llm(result),
            depth,
        )

    elif action == "recall":
        if not app.contextor:
            await feed_root_summary(
                app,
                logger,
                "RECALL_RESULT",
                json.dumps({"error": "Memory is disabled"}),
                depth,
            )
            return
        result = app.contextor.recall(
            parsed["theme"],
            session_id=app.sessions.current_id,
        )
        await feed_root_summary(
            app,
            logger,
            "RECALL_RESULT",
            compact_payload_for_llm(result),
            depth,
        )

    elif action == "search_memory":
        if not app.contextor:
            await feed_root_summary(
                app,
                logger,
                "SEARCH_MEMORY_RESULT",
                json.dumps(
                    {
                        "results": [],
                        "available": False,
                        "reason": "Memory is disabled",
                    }
                ),
                depth,
            )
            return
        result = app.contextor.semantic_search(
            query=parsed["query"],
            top_k=parsed.get("top_k", 5),
            offset=parsed.get("offset", 0),
            min_score=parsed.get("min_score", 0.3),
            session_id=app.sessions.current_id,
            include_global=True,
        )
        await feed_root_summary(
            app,
            logger,
            "SEARCH_MEMORY_RESULT",
            compact_payload_for_llm(result),
            depth,
        )

    elif action == "list_memory":
        if not app.contextor:
            await feed_root_summary(
                app,
                logger,
                "LIST_MEMORY_RESULT",
                json.dumps({"themes": []}),
                depth,
            )
            return
        result = app.contextor.list_themes(
            session_id=app.sessions.current_id,
        )
        await feed_root_summary(
            app,
            logger,
            "LIST_MEMORY_RESULT",
            compact_payload_for_llm(result),
            depth,
        )
