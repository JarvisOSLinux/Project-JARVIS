"""ROOT-level event handlers (user input, dispatch signals, confirmations)."""

from __future__ import annotations

import asyncio
import json
from logging import Logger
from typing import Any

from ..core.voice_state import VoiceState
from .io import broadcast_to_gui_clients, set_gui_state
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

    # Single funnel for every input source (voice, GUI/CLI socket, stdin) --
    # the GUI "message" handler already broadcasts this for its own path,
    # but voice-injected input had no PROCESSING signal at all before this.
    # When goals are already in flight, meta tells clients this input adds
    # a concurrent goal rather than starting from idle (#142).
    goals = getattr(app, "goals", None)
    already_active = goals.get_active_goals() if goals is not None else []
    if already_active:
        await set_gui_state(
            app,
            VoiceState.PROCESSING,
            {"concurrent_goals": len(already_active) + 1},
        )
    else:
        await set_gui_state(app, VoiceState.PROCESSING)

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

    context = build_root_context(app, logger, new_input=text)
    emit_activity(app, "Thinking about your request…", kind="llm")

    response = await ask_llm(app, logger, context, tag="root", mode="root")
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

    response = await ask_llm(app, logger, context, tag="root", mode="root")
    await app._act_on_root_response(response)


async def on_dispatch_signals(
    app: Any, logger: Logger, signals: list[dict[str, Any]]
) -> None:
    """Handle a merged fire_wake=false batch as ONE ROOT turn (#189).

    dispatch delivers these together because they belong to one settled session,
    so ROOT sees every outcome at once and answers once — no per-signal turns
    guessing about siblings they weren't handed.
    """
    if not signals:
        return

    pids = [s.get("pid") for s in signals]
    logger.info(
        f"JARVIS: Dispatch batch: {len(signals)} signal(s), pids={pids}, "
        f"types={[s.get('type') for s in signals]}"
    )
    emit_activity(app, f"Dispatch batch: {len(signals)} result(s)", kind="dispatch")

    if app.llm is None:
        logger.warning("Dispatch batch received but no LLM configured — ignoring")
        return

    for sig in signals:
        app.goals.update_from_signal(sig)

    # A fire_wake=false batch is one session, so all PIDs share a goal; scope the
    # context to it. Fall back to full root context if none owns the PIDs.
    owning_goal = None
    for pid in pids:
        if pid is not None:
            owning_goal = app.goals.find_goal_by_task_pid(pid)
            if owning_goal:
                break

    if owning_goal:
        goal_ctx = app.goals.get_goal_context(owning_goal.id)
        parts = []
        if goal_ctx:
            parts.append(f"INTENT: {owning_goal.description}")
            parts.append(f"GOAL_STATE: {compact_payload_for_llm(goal_ctx)}")
        parts.append(f"SIGNALS: {json.dumps(signals)}")
        summary = app.sessions.load_summary()
        if summary:
            parts.append(f"CONVERSATION_SUMMARY: {summary}")
        context = "\n".join(parts)
    else:
        context = build_root_context(app, logger)
        context += f"\nSIGNALS: {json.dumps(signals)}"

    response = await ask_llm(app, logger, context, tag="root", mode="root")
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

    # This is the one true point where _pending actually changes, regardless
    # of which channel resolved it (CLI, GUI, TUI, desktop notification, or
    # an opted-back-in timeout) -- so it's the right place to keep every
    # connected GUI client's pending-confirmations view in sync.
    if getattr(app, "_gui_clients", None):
        asyncio.create_task(
            broadcast_to_gui_clients(
                app,
                {
                    "type": "confirmation_list",
                    "confirmations": app.confirmation.list_pending(),
                },
            )
        )

    if app.llm is None:
        logger.warning("Confirmation resolved but no LLM configured — ignoring")
        return

    if pending.denied_tools and not pending.approved_tasks:
        denied_list = ", ".join(pending.denied_tools)
        context = build_root_context(app, logger)
        context += f"\nUSER_DENIAL: Action {denied_list} was denied by the user"
        response = await ask_llm(
            app, logger, context, tag="root-confirmation-denied", mode="root"
        )
        await app._act_on_root_response(response)
        return

    if pending.approved_tasks:
        # Scope the dispatch to the owning goal and link the returned PIDs back
        # to it, exactly as the direct path does (dispatch_flow.py:361-368).
        # Without this, tasks that went through a confirmation are detached from
        # their goal and every signal they produce logs "No goal found for PID"
        # and falls back to full root context (#190).
        result = await app.dispatch.send_tasks(
            pending.approved_tasks, session_id=pending.session_id
        )

        if pending.session_id:
            from .dispatch_flow import _extract_pids_from_result

            pids = _extract_pids_from_result(result)
            if pids:
                app.goals.link_tasks(pending.session_id, pids)

        context = build_root_context(app, logger)

        if isinstance(result, dict) and "error" in result:
            context += f"\nDISPATCH_ERROR: {compact_payload_for_llm(result)}"
        else:
            context += f"\nDISPATCH_RESULT: {compact_payload_for_llm(result)}"

        if pending.denied_tools:
            denied_list = ", ".join(pending.denied_tools)
            context += f"\nUSER_DENIAL: Action {denied_list} was denied by the user"

        response = await ask_llm(
            app, logger, context, tag="root-confirmation-result", mode="root"
        )
        await app._act_on_root_response(response)
