"""ROOT-level event handlers (user input, dispatch signals, confirmations)."""

from __future__ import annotations

from logging import Logger
from typing import Any


async def on_user_input(app: Any, logger: Logger, text: str) -> None:
    logger.info(f"JARVIS: User input: '{text}'")

    # Slash-commands are session-control shortcuts, not LLM input.
    if text.startswith("/"):
        handled = app._handle_slash_command(text)
        if handled:
            return

    # Ensure we have a session to log against.  First-ever input
    # lazily creates one so history is always session-scoped.
    app.sessions.ensure_session()

    app.goals.add_goal(text)

    # Auto-store every user prompt for long-term recall.
    # No LLM decision — every prompt gets persisted + embedded.
    if app.contextor:
        app.contextor.auto_store_prompt(
            text,
            session_id=app.sessions.current_id,
        )

    app.llm.switch_mode("root")
    context = app._build_root_context(new_input=text)
    app._activity("Thinking about your request…", kind="llm")

    response = await app._ask_llm(context, tag="root")
    await app._act_on_root_response(response)


async def on_dispatch_signal(app: Any, logger: Logger, signal: dict[str, Any]) -> None:
    sig_type = signal.get("type")
    sig_pid = signal.get("pid")
    logger.info(f"JARVIS: Dispatch signal: type={sig_type}, pid={sig_pid}")
    if sig_type:
        app._activity(f"Dispatch signal: {sig_type} (pid {sig_pid})", kind="dispatch")

    app.goals.update_from_signal(signal)

    app.llm.switch_mode("root")
    context = app._build_root_context(signal=signal)

    response = await app._ask_llm(context, tag="root")
    await app._act_on_root_response(response)


async def on_confirmation_response(
    app: Any, logger: Logger, data: dict[str, Any]
) -> None:
    """Handle a CONFIRMATION_RESPONSE event from the event loop.

    Resolves the pending confirmation, then either dispatches the
    approved tasks or feeds USER_DENIAL back to ROOT so the LLM
    keeps communicating with the user.
    """
    pending = app.confirmation.resolve(data)
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
        app.llm.switch_mode("root")
        context = app._build_root_context()
        context += f"\nUSER_DENIAL: Action {denied_list} was denied by the user"
        response = await app._ask_llm(context, tag="root-confirmation-denied")
        await app._act_on_root_response(response)
        return

    # Some or all approved — dispatch the approved tasks.
    if pending.approved_tasks:
        result = await app.dispatch.send_tasks(pending.approved_tasks)

        app.llm.switch_mode("root")
        context = app._build_root_context()

        if isinstance(result, dict) and "error" in result:
            context += f"\nDISPATCH_ERROR: {app._compact_payload_for_llm(result)}"
        else:
            context += f"\nDISPATCH_RESULT: {app._compact_payload_for_llm(result)}"

        # Include partial denial if some tools were denied.
        if pending.denied_tools:
            denied_list = ", ".join(pending.denied_tools)
            context += f"\nUSER_DENIAL: Action {denied_list} was denied by the user"

        response = await app._ask_llm(context, tag="root-confirmation-result")
        await app._act_on_root_response(response)
