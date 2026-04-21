"""ROOT-mode response action handling helpers."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any

from ..config import Config


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
        app._activity("Composing response…", kind="llm")
    elif action == "dispatch":
        app._activity("Planning tool execution…", kind="dispatch")
    elif action in ("store", "recall", "search_memory", "list_memory"):
        app._activity(f"Running memory action: {action}", kind="memory")

    app._apply_goal_updates(parsed.get("goal_updates", []))

    if action == "respond":
        output = parsed["output"]
        if not output.strip():
            logger.warning("JARVIS: LLM returned empty respond — retrying")
            context = app._build_root_context()
            context += "\nYour previous response had an empty output. Please respond to the user."
            retry_response = await app._ask_llm(context, tag="root-retry-empty")
            await app._act_on_root_response(retry_response, depth + 1)
            return
        app.output_manager.handle_response({"output": output})
        app._persist_assistant_turn(output)
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
            await app._feed_root_summary("DISPATCH_SUMMARY", summary, depth)

    # -- Memory actions (direct, no sub-chain) --
    elif action == "store":
        if not app.contextor:
            await app._feed_root_summary(
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
        await app._feed_root_summary(
            "STORE_RESULT",
            app._compact_payload_for_llm(result),
            depth,
        )

    elif action == "recall":
        if not app.contextor:
            await app._feed_root_summary(
                "RECALL_RESULT",
                json.dumps({"error": "Memory is disabled"}),
                depth,
            )
            return
        result = app.contextor.recall(
            parsed["theme"],
            session_id=app.sessions.current_id,
        )
        await app._feed_root_summary(
            "RECALL_RESULT",
            app._compact_payload_for_llm(result),
            depth,
        )

    elif action == "search_memory":
        if not app.contextor:
            await app._feed_root_summary(
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
        await app._feed_root_summary(
            "SEARCH_MEMORY_RESULT",
            app._compact_payload_for_llm(result),
            depth,
        )

    elif action == "list_memory":
        if not app.contextor:
            await app._feed_root_summary(
                "LIST_MEMORY_RESULT",
                json.dumps({"themes": []}),
                depth,
            )
            return
        result = app.contextor.list_themes(
            session_id=app.sessions.current_id,
        )
        await app._feed_root_summary(
            "LIST_MEMORY_RESULT",
            app._compact_payload_for_llm(result),
            depth,
        )
