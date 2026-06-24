"""ROOT-level event handlers (user input, dispatch signals, confirmations)."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any

from .llm_bridge import ask_llm
from .output_hooks import emit_activity
from .root_context import build_root_context, compact_payload_for_llm
from .session_commands import handle_slash_command

_NO_LLM_MSG = (
    "No LLM provider configured. "
    "Add one with `/providers add` or through settings, then restart."
)


async def on_user_input(app: Any, logger: Logger, text: str) -> None:
    logger.info(f"JARVIS: User input: '{text}'")

    if text.startswith("/"):
        handled = handle_slash_command(app, text)
        if handled:
            return

    if app.llm is None:
        app.output_manager.display({"output": _NO_LLM_MSG})
        return

    app.sessions.ensure_session()
    app.goals.add_goal(text)

    if app.contextor:
        app.contextor.auto_store_prompt(
            text,
            session_id=app.sessions.current_id,
        )

    app.llm.switch_mode("root")
    context = build_root_context(app, logger, new_input=text)
    emit_activity(app, "Thinking about your request…", kind="llm")

    response = await ask_llm(app, logger, context, tag="root")
    await app._act_on_root_response(response)


async def on_dispatch_signal(app: Any, logger: Logger, signal: dict[str, Any]) -> None:
    # Extract EventMerger enrichment keys before passing the signal downstream.
    remind_completed = signal.pop("_remind_completed", False)
    exit_data = signal.pop("_exit", None)

    sig_type = signal.get("type")
    sig_pid = signal.get("pid")
    logger.info(
        f"JARVIS: Dispatch signal: type={sig_type}, pid={sig_pid}"
        + (" (task already completed — REMIND+EXIT merged)" if remind_completed else "")
    )
    if sig_type:
        emit_activity(
            app, f"Dispatch signal: {sig_type} (pid {sig_pid})", kind="dispatch"
        )

    if app.llm is None:
        logger.warning("Dispatch signal received but no LLM configured — ignoring")
        return

    app.goals.update_from_signal(signal)
    app.llm.switch_mode("root")

    # Build a context scoped to the goal that owns this PID.
    # If no goal owns the PID (e.g. a timer from defer), fall back to
    # the full root context so the LLM still has useful information.
    owning_goal = app.goals.find_goal_by_task_pid(sig_pid) if sig_pid else None

    if owning_goal:
        goal_ctx = app.goals.get_goal_context(owning_goal.id)
        parts = []
        if goal_ctx:
            parts.append(f"INTENT: {owning_goal.description}")
            parts.append(f"GOAL_STATE: {compact_payload_for_llm(goal_ctx)}")
        parts.append(f"SIGNAL: {json.dumps(signal)}")
        summary = app.sessions.load_summary()
        if summary:
            parts.append(f"CONVERSATION_SUMMARY: {summary}")
        context = "\n".join(parts)
        logger.debug(
            f"JARVIS: Signal context scoped to goal [{owning_goal.id}] "
            f"({owning_goal.description[:60]})"
        )
    else:
        context = build_root_context(app, logger, signal=signal)

    if remind_completed and exit_data:
        context += (
            f"\nREMIND_COMPLETED: Reminder fired for pid={sig_pid}, but the task "
            f"finished before the LLM was reached.\n"
            f"EXIT_DATA: {compact_payload_for_llm(exit_data)}"
        )

    response = await ask_llm(app, logger, context, tag="root")
    await app._act_on_root_response(response)


async def on_confirmation_response(
    app: Any, logger: Logger, data: dict[str, Any]
) -> None:
    pending = app.confirmation.resolve(data)
    if pending is None:
        return

    logger.info(
        f"JARVIS: Confirmation resolved: id={pending.request_id}, "
        f"approved={len(pending.approved_tasks)}, "
        f"denied={len(pending.denied_tools)}"
    )

    if app.llm is None:
        logger.warning("Confirmation resolved but no LLM configured — ignoring")
        return

    if pending.denied_tools and not pending.approved_tasks:
        denied_list = ", ".join(pending.denied_tools)
        app.llm.switch_mode("root")
        context = build_root_context(app, logger)
        context += f"\nUSER_DENIAL: Action {denied_list} was denied by the user"
        response = await ask_llm(app, logger, context, tag="root-confirmation-denied")
        await app._act_on_root_response(response)
        return

    if pending.approved_tasks:
        result = await app.dispatch.send_tasks(pending.approved_tasks)

        app.llm.switch_mode("root")
        context = build_root_context(app, logger)

        if isinstance(result, dict) and "error" in result:
            context += f"\nDISPATCH_ERROR: {compact_payload_for_llm(result)}"
        else:
            context += f"\nDISPATCH_RESULT: {compact_payload_for_llm(result)}"

        if pending.denied_tools:
            denied_list = ", ".join(pending.denied_tools)
            context += f"\nUSER_DENIAL: Action {denied_list} was denied by the user"

        response = await ask_llm(app, logger, context, tag="root-confirmation-result")
        await app._act_on_root_response(response)
