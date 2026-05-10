"""ROOT-mode response action handling helpers."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any, Optional

from ..config import Config
from .goal_updates import apply_goal_updates
from .llm_bridge import ask_llm
from .output_hooks import emit_activity, persist_assistant_turn
from .root_context import build_root_context, compact_payload_for_llm

# Emitted by the LLM retry fallback when all attempts return empty.
_DISPATCH_FAILURE_SENTINEL = "I had trouble formatting my response."

# Words that indicate a non-sentinel failure summary.
_FAILURE_WORDS = ("error", "fail", "couldn't", "unable", "timed out", "exception")


def _is_failure_summary(summary: str) -> bool:
    lower = summary.lower()
    return any(w in lower for w in _FAILURE_WORDS)


async def feed_root_summary(
    app: Any,
    logger: Logger,
    label: str,
    summary: str,
    depth: int,
    intent: Optional[str] = None,
) -> None:
    """Feed a subsystem summary back into ROOT for the next decision."""
    app.llm.switch_mode("root")
    context = build_root_context(app, logger)

    if label == "DISPATCH_SUMMARY" and _DISPATCH_FAILURE_SENTINEL in summary:
        # Sub-chain exhausted LLM retries. Bypass root to prevent hallucination;
        # emit a direct reply that names the original intent.
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

    # For non-sentinel failures: use DISPATCH_FAILED label and inject
    # ATTEMPTED_INTENT so root can name what was being attempted.
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

    if action == "respond":
        output = parsed["output"]
        if not output.strip():
            logger.warning("JARVIS: LLM returned empty respond — retrying")
            context = build_root_context(app, logger)
            context += "\nYour previous response had an empty output. Please respond to the user."
            retry_response = await ask_llm(
                app, logger, context, tag="root-retry-empty"
            )
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
            # Create a root goal so the sub-chain has scoped context, PID
            # linking, output bubbling, and strategy persistence.
            goal = app.goals.add_goal(parsed["intent"])
            summary = await app._run_dispatch_subchain(
                parsed["intent"], goal_id=goal.id
            )
            await feed_root_summary(
                app,
                logger,
                "DISPATCH_SUMMARY",
                summary,
                depth,
                intent=parsed["intent"],
            )

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

    else:
        logger.warning(
            f"JARVIS: Unknown root action '{action}' — retrying with valid-action prompt"
        )
        context = build_root_context(app, logger)
        context += (
            f"\nSYSTEM: '{action}' is not a valid action. "
            "Valid actions: respond, dispatch, store, recall, search_memory, list_memory. "
            "Output one of those now."
        )
        retry_response = await ask_llm(
            app, logger, context, tag="root-unknown-action"
        )
        await app._act_on_root_response(retry_response, depth + 1)
