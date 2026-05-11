"""ROOT-mode response action handling — unified tool + memory dispatch."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any, Optional

from ..config import Config
from .goal_updates import apply_goal_updates
from .llm_bridge import ask_llm
from .output_hooks import emit_activity, get_embeddings, persist_assistant_turn
from .root_context import build_root_context, compact_payload_for_llm

# Emitted by the LLM retry fallback when all attempts return empty.
_DISPATCH_FAILURE_SENTINEL = "I had trouble formatting my response."

_FAILURE_WORDS = ("error", "fail", "couldn't", "unable", "timed out", "exception")


def _is_failure_summary(summary: str) -> bool:
    lower = summary.lower()
    return any(w in lower for w in _FAILURE_WORDS)


async def _continue_root(
    app: Any,
    logger: Logger,
    extra_context: str,
    depth: int,
    tag: str,
    max_chain_depth: int,
) -> None:
    """Re-enter the root LLM loop with additional context appended."""
    context = build_root_context(app, logger)
    context += f"\n{extra_context}"
    response = await ask_llm(app, logger, context, tag=tag)
    await app._act_on_root_response(response, depth + 1)


async def feed_root_summary(
    app: Any,
    logger: Logger,
    label: str,
    summary: str,
    depth: int,
    intent: Optional[str] = None,
) -> None:
    """Feed a subsystem summary back into ROOT for the next decision."""
    context = build_root_context(app, logger)

    if label == "DISPATCH_SUMMARY" and _DISPATCH_FAILURE_SENTINEL in summary:
        intent_clause = f' while trying to: "{intent}"' if intent else ""
        msg = (
            f"I ran into a problem{intent_clause} and couldn't complete the task. "
            "Please try again."
        )
        logger.warning("JARVIS: Dispatch failure sentinel — short-circuiting root LLM")
        app.output_manager.handle_response({"output": msg})
        persist_assistant_turn(app, msg)
        dismissed = app.goals.dismiss_completed()
        if dismissed:
            logger.info(f"JARVIS: Dismissed {len(dismissed)} completed goal(s)")
        return

    actual_label = label
    if label == "DISPATCH_SUMMARY" and intent:
        context += f"\nATTEMPTED_INTENT: {intent}"
        if _is_failure_summary(summary):
            actual_label = "DISPATCH_FAILED"

    context += f"\n{actual_label}: {summary}"

    if actual_label == "DISPATCH_FAILED":
        context += (
            "\nSYSTEM: The task above failed. Tell the user what was attempted "
            "(see ATTEMPTED_INTENT) and that it did not succeed. "
            "Do not fabricate a success."
        )

    response = await ask_llm(app, logger, context, tag="root-chain")
    await app._act_on_root_response(response, depth + 1)


async def act_on_root_response(
    app: Any,
    logger: Logger,
    response: dict[str, Any],
    depth: int,
    max_chain_depth: int,
) -> None:
    """Handle a ROOT-mode LLM response.

    Unified mode: the root LLM handles tool discovery, install, dispatch,
    wait, kill, and defer directly — no separate dispatch sub-chain.
    """
    if depth >= max_chain_depth:
        logger.error("JARVIS: Max chain depth reached, forcing respond")
        app.output_manager.handle_response(
            {"output": "I got stuck in a loop. Could you try again?"}
        )
        return

    parsed = app.task_parser.parse(response)

    if "error" in parsed:
        logger.warning(f"JARVIS: Root parse error: {parsed['error']}")
        # Retry with an explicit corrective hint rather than bailing out.
        context = build_root_context(app, logger)
        context += (
            "\nSYSTEM: Your last response was not a recognised action. "
            "You MUST output a JSON object with an \"action\" field. "
            'Valid actions: respond, find_tools, list_tools, install, dispatch, '
            'wait, kill, defer, store, recall, search_memory, list_memory. '
            'Example: {"action": "find_tools", "intent": "what you need to do"}'
        )
        retry_response = await ask_llm(
            app, logger, context, tag="root-parse-error-retry"
        )
        retry_parsed = app.task_parser.parse(retry_response)
        if "error" in retry_parsed:
            # Still bad — give up and surface a user-facing message.
            logger.error("JARVIS: Root parse error on retry — giving up")
            app.output_manager.handle_response(
                {"output": "I had trouble processing that. Could you try again?"}
            )
        else:
            await act_on_root_response(
                app, logger, retry_response, depth + 1, max_chain_depth
            )
        return

    action = parsed["action"]
    logger.info(f"JARVIS: Root action='{action}'")

    if action == "respond":
        emit_activity(app, "Composing response…", kind="llm")
    elif action in ("find_tools", "list_tools", "install", "dispatch", "wait", "kill", "defer"):
        emit_activity(app, f"Tool action: {action}…", kind="dispatch")
    elif action in ("store", "recall", "search_memory", "list_memory"):
        emit_activity(app, f"Running memory action: {action}", kind="memory")

    apply_goal_updates(app, parsed.get("goal_updates", []))

    # ------------------------------------------------------------------ respond
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

    # --------------------------------------------------------------- find_tools
    elif action == "find_tools":
        from ..dispatch.tool_discovery import discover_tools as _discover_tools

        intent = parsed.get("intent", "")
        emit_activity(app, f"Searching for tools: {intent[:60]}…", kind="dispatch")
        logger.info(f"JARVIS: find_tools intent='{intent}'")

        tool_results = await _discover_tools(
            adapter=app.dispatch,
            logger=logger,
            tasks=[{"intent": intent}],
            embeddings=get_embeddings(app),
        )

        if tool_results:
            # tool_results already contains MATCHED_TOOLS:/CANDIDATE_SERVERS: headers
            await _continue_root(
                app, logger, tool_results, depth, "root-find-tools", max_chain_depth
            )
        else:
            _no_found = (
                f"NO_TOOLS_FOUND: Searching for '{intent}' found no tools.\n"
                "IMPORTANT: You MUST retry with find_tools using a more specific intent.\n"
                "Use the EXACT TASK GOAL as intent — e.g. 'check python version', "
                "'open Firefox', 'search web for X'.\n"
                "Do NOT use generic tool types like 'run shell command'.\n"
                "Only use respond if you have retried at least twice with different intents."
            )
            await _continue_root(
                app,
                logger,
                _no_found,
                depth,
                "root-find-tools-empty",
                max_chain_depth,
            )

    # --------------------------------------------------------------- list_tools
    elif action == "list_tools":
        server_id = parsed.get("server_id", "")
        emit_activity(app, f"Listing tools for {server_id}…", kind="dispatch")
        result = await app.dispatch.list_server_tools(server_id)
        await _continue_root(
            app,
            logger,
            f"TOOLS: {compact_payload_for_llm(result)}",
            depth,
            "root-list-tools",
            max_chain_depth,
        )

    # ----------------------------------------------------------------- install
    elif action == "install":
        from .dispatch_flow import _collect_server_config

        server_id = parsed.get("server_id", "")
        emit_activity(app, f"Installing {server_id}…", kind="dispatch")
        result = await app.dispatch.install_server(server_id)
        extra = f"INSTALL_RESULT: {compact_payload_for_llm(result)}"
        if "error" not in result and server_id:
            await _collect_server_config(app, logger, server_id)
            await app.dispatch.auto_index_server(
                server_id=server_id,
                embeddings=get_embeddings(app),
            )
        await _continue_root(app, logger, extra, depth, "root-install", max_chain_depth)

    # --------------------------------------------------------------- dispatch
    elif action == "dispatch":
        from .dispatch_flow import _extract_pids_from_result, dispatch_send

        tasks = parsed.get("tasks", [])

        # Graceful fallback: old-style dispatch with intent only (no tasks).
        # Treat it as find_tools so the LLM can recover without hard failure.
        if not tasks:
            intent = parsed.get("intent", "")
            if intent:
                logger.info(
                    f"JARVIS: dispatch without tasks — routing to find_tools: '{intent}'"
                )
                from ..dispatch.tool_discovery import discover_tools as _discover_tools

                tool_results = await _discover_tools(
                    adapter=app.dispatch,
                    logger=logger,
                    tasks=[{"intent": intent}],
                    embeddings=get_embeddings(app),
                )
                if tool_results:
                    await _continue_root(
                        app, logger, tool_results, depth, "root-find-tools", max_chain_depth
                    )
                else:
                    _no_found = (
                        f"NO_TOOLS_FOUND: Searching for '{intent}' found no tools.\n"
                        "IMPORTANT: You MUST retry with find_tools using a more specific intent.\n"
                        "Use the EXACT TASK GOAL — e.g. 'check python version', "
                        "'open Firefox', 'search web for X'.\n"
                        "Do NOT use generic types like 'run shell command'.\n"
                        "Only use respond if you have retried at least twice."
                    )
                    await _continue_root(
                        app,
                        logger,
                        _no_found,
                        depth,
                        "root-find-tools-empty",
                        max_chain_depth,
                    )
            else:
                await _continue_root(
                    app,
                    logger,
                    "SYSTEM: dispatch requires a tasks array. Use find_tools first "
                    "to discover available tools, then dispatch with their server/tool names.",
                    depth,
                    "root-dispatch-no-tasks",
                    max_chain_depth,
                )
            return

        emit_activity(app, f"Dispatching {len(tasks)} task(s)…", kind="dispatch")
        result = await dispatch_send(app, logger, tasks)

        if isinstance(result, dict) and result.get("awaiting_confirmation"):
            logger.info(
                f"JARVIS: Dispatch paused for confirmation "
                f"id={result['confirmation_id']}"
            )
            emit_activity(
                app,
                "Waiting for your confirmation before running tools.",
                kind="dispatch",
            )
            return

        pending_pids = _extract_pids_from_result(result)
        if pending_pids:
            app._pending_dispatch_pids = pending_pids
            logger.info(
                f"JARVIS: Tracking {len(pending_pids)} dispatched PID(s): {pending_pids}"
            )

        if isinstance(result, dict) and "error" in result:
            extra = f"DISPATCH_ERROR: {compact_payload_for_llm(result)}"
        else:
            extra = f"DISPATCH_RESULT: {compact_payload_for_llm(result)}"
            if pending_pids:
                extra += f"\nRUNNING_PIDS: {pending_pids}"

        emit_activity(app, "Tool results received.", kind="dispatch")
        await _continue_root(app, logger, extra, depth, "root-dispatch-result", max_chain_depth)

    # -------------------------------------------------------------------- wait
    elif action == "wait":
        from .dispatch_flow import (
            _find_exits_for_pids,
            _signals_from_result,
        )

        pids = parsed.get("pids") or getattr(app, "_pending_dispatch_pids", [])

        if pids and app.dispatch.is_connected:
            emit_activity(app, "Waiting for tasks to complete…", kind="dispatch")

            current_window = await app.dispatch.get_signal_window()
            already_done = _find_exits_for_pids(current_window, pids)

            if already_done:
                logger.info(
                    f"JARVIS: PIDs {[s['pid'] for s in already_done]} already "
                    "completed — using EXIT from window"
                )
                if hasattr(app, "events"):
                    app.events.mark_signals_seen(already_done)
                await _continue_root(
                    app,
                    logger,
                    f"WAIT_RESULT: {compact_payload_for_llm({'signals': already_done})}",
                    depth,
                    "root-wait-done",
                    max_chain_depth,
                )
            else:
                logger.info(f"JARVIS: Blocking via wait_task for PIDs {pids}")
                wait_result = await app.dispatch.wait_task(pids)
                returned_signals = _signals_from_result(wait_result)
                if returned_signals and hasattr(app, "events"):
                    app.events.mark_signals_seen(returned_signals)

                if isinstance(wait_result, dict) and "error" in wait_result:
                    await _continue_root(
                        app,
                        logger,
                        f"WAIT_ERROR: {compact_payload_for_llm(wait_result)}",
                        depth,
                        "root-wait-error",
                        max_chain_depth,
                    )
                else:
                    await _continue_root(
                        app,
                        logger,
                        f"WAIT_RESULT: {compact_payload_for_llm(wait_result)}",
                        depth,
                        "root-wait-result",
                        max_chain_depth,
                    )
        else:
            logger.info("JARVIS: wait — no pids or dispatch not connected")
            await _continue_root(
                app,
                logger,
                "WAIT_RESULT: No active tasks to wait for.",
                depth,
                "root-wait-noop",
                max_chain_depth,
            )

    # -------------------------------------------------------------------- kill
    elif action == "kill":
        from .dispatch_flow import do_kill

        pids = parsed.get("pids", [])
        emit_activity(app, f"Stopping task(s) {pids}…", kind="dispatch")
        await do_kill(app, logger, pids)
        await _continue_root(
            app,
            logger,
            f"KILL_RESULT: Killed PID(s) {pids}",
            depth,
            "root-kill",
            max_chain_depth,
        )

    # ------------------------------------------------------------------- defer
    elif action == "defer":
        from .dispatch_flow import do_defer

        emit_activity(
            app,
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

    # ------------------------------------------------------------------- store
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
        scope = parsed.get("scope", "session")
        sid = None if scope == "global" else app.sessions.current_id
        result = app.contextor.store(
            parsed["theme"],
            parsed["content"],
            session_id=sid,
        )
        await feed_root_summary(
            app, logger, "STORE_RESULT", compact_payload_for_llm(result), depth
        )

    # ------------------------------------------------------------------ recall
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
            app, logger, "RECALL_RESULT", compact_payload_for_llm(result), depth
        )

    # ---------------------------------------------------------- search_memory
    elif action == "search_memory":
        if not app.contextor:
            await feed_root_summary(
                app,
                logger,
                "SEARCH_MEMORY_RESULT",
                json.dumps(
                    {"results": [], "available": False, "reason": "Memory is disabled"}
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
            app, logger, "SEARCH_MEMORY_RESULT", compact_payload_for_llm(result), depth
        )

    # ------------------------------------------------------------- list_memory
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
        result = app.contextor.list_themes(session_id=app.sessions.current_id)
        await feed_root_summary(
            app, logger, "LIST_MEMORY_RESULT", compact_payload_for_llm(result), depth
        )

    # ---------------------------------------------------------- rename_session
    elif action == "rename_session":
        title = parsed.get("title", "")
        if title and app.sessions.current:
            app.sessions.rename(title)
            app.sessions.current.title = title
            if hasattr(app, "schedule_sidebar_refresh"):
                app.schedule_sidebar_refresh()
            logger.info(f"JARVIS: Session silently renamed to '{title}'")
        context = build_root_context(app, logger)
        retry_response = await ask_llm(app, logger, context, tag="root-post-rename")
        await app._act_on_root_response(retry_response, depth + 1)

    # ---------------------------------------------------------- unknown action
    else:
        logger.warning(
            f"JARVIS: Unknown root action '{action}' — retrying with valid-action prompt"
        )
        context = build_root_context(app, logger)
        context += (
            f"\nSYSTEM: '{action}' is not a valid action. "
            "Valid actions: respond, find_tools, list_tools, install, dispatch, "
            "wait, kill, defer, store, recall, search_memory, list_memory. "
            "Output one of those now."
        )
        retry_response = await ask_llm(
            app, logger, context, tag="root-unknown-action"
        )
        await app._act_on_root_response(retry_response, depth + 1)
