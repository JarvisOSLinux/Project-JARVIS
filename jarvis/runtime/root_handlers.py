"""ROOT-level event handlers (user input, dispatch signals, confirmations)."""

from __future__ import annotations

import asyncio
import json
import re
from logging import Logger
from typing import Any

from ..core.secret_scrubber import redact_secrets, redaction_notice
from ..core.voice_state import VoiceState
from ..dispatch import sign_in as sign_in_flow
from .io import broadcast_to_gui_clients, enrich_pending_with_goals, set_gui_state
from .llm_bridge import ask_llm
from .output_hooks import emit_activity
from .root_context import (
    build_root_context,
    compact_payload_for_llm,
    configure_call_hint,
    required_config_keys,
)
from .session_commands import handle_slash_command

_NO_LLM_MSG = (
    "No LLM provider configured. "
    "Add one with `/providers add` or through settings, then restart."
)


async def on_user_input(app: Any, logger: Logger, text: str) -> None:
    # Scrubbed before anything below can keep it: the goal tree is archived to
    # disk, contextor's memory is searched on every later prompt, and the model
    # may be hosted. Slash commands get the raw text -- they never reach memory
    # or the model, and are where a secret could legitimately be entered (#242).
    clean, redacted = redact_secrets(text)
    logger.info(f"JARVIS: User input: '{clean}'")

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
        app.output_manager.handle_response({"output": _NO_LLM_MSG})
        return

    if redacted:
        app.output_manager.handle_response({"output": redaction_notice(redacted)})
    text = clean

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


# Machine-readable failure codes: unambiguous enough to trust on their own.
_AUTH_ERROR_CODES = (
    "subscription_token_invalid",
    "invalid_api_key",
    "invalid_token",
    "token_invalid",
)

# Error-shaped phrasings. Bare "authentication" / "unauthorized" deliberately are
# NOT patterns: this runs over the serialized signal, tool output included, so a
# successful web search about OAuth would otherwise be read as an auth failure
# and its perfectly healthy server told to reconfigure itself.
_AUTH_ERROR_PHRASES = re.compile(
    r"(?:authentication|authorization|auth)[\s_-]+(?:failed|failure|error|required|denied)"
    r"|(?:401|403)\s*(?:unauthorized|forbidden)"
    r"|(?:unauthorized|forbidden)\s*[(\[]\s*(?:401|403)"
    r"|http[\s/]*(?:error[\s:]*)?(?:401|403)\b"
    r"|\"?status(?:[_ ]code)?\"?\s*[:=]\s*\"?(?:401|403)\b"
    r"|\b(?:invalid|expired|missing|bad)\s+(?:api[\s_-]?key|token|credential)",
    re.IGNORECASE,
)

# Only these can report a tool failure. REMIND says "still running" and KILL says
# "we terminated it" — neither is evidence a server needs reconfiguring, and both
# carry free text that can mention auth in passing.
_FAILURE_SIGNAL_TYPES = ("EXIT", "TIMEOUT")


def _contains_auth_error(text: str) -> bool:
    """True when the text looks like an authentication *failure*, not a mention."""
    lowered = text.lower()
    if any(code in lowered for code in _AUTH_ERROR_CODES):
        return True
    return _AUTH_ERROR_PHRASES.search(text) is not None


def _server_ids_for_signals(app: Any, signals: list[dict[str, Any]], owning_goal: Any):
    """Server ids behind the PIDs in these signals.

    The goal's pid -> server map attributes each PID to its own task, so one
    failing task names one server. The pid -> batch-fingerprint map (#205) is
    only a fallback, and only for a single-task fingerprint: it covers a whole
    batch, so decoding a multi-task one would name servers that never failed —
    including, on the confirmation-resume path, ones the user denied. When the
    PID is unmapped and the fingerprint is batch-wide, name nothing; a missing
    hint beats a hint pointing at the wrong server.
    """
    from .dispatch_flow import _FP_FIELD_SEP, _FP_TASK_SEP

    server_ids: set[str] = set()
    pid_servers = getattr(owning_goal, "dispatch_pid_server", {}) or {}
    pid_fps = getattr(owning_goal, "dispatch_pid_fps", {}) or {}
    for sig in signals:
        pid = sig.get("pid")
        server = pid_servers.get(pid)
        if server:
            server_ids.add(server)
            continue
        fingerprint = pid_fps.get(pid)
        if fingerprint and _FP_TASK_SEP not in fingerprint:
            server = fingerprint.split(_FP_FIELD_SEP)[0]
            if server:
                server_ids.add(server)
    return server_ids


async def _failure_hints(
    app: Any,
    logger: Logger,
    signals: list[dict[str, Any]],
    owning_goal: Any,
    signal_text: str,
) -> str:
    """What the model needs to fix a failed call: SIGN_IN_NEEDED or CONFIG_HINT.

    CONFIG_HINT was re-homed into the EXIT-signal path: the dispatch result no
    longer flows back through dispatch_flow (#195 made the confirmed/dispatched
    path go silent and let EXIT signals drive the next ROOT turn), so without it
    a bad API key returns the same error every cycle, the repeat window never
    resets, and the #205 guard kills the goal instead of letting the LLM
    self-heal by calling configure_server with the right key names.

    A server that works in the user's account gets SIGN_IN_NEEDED instead (#229)
    — never both, since configure_server is the wrong fix for keys the account
    fills. That covers dmcp refusing the call (a ``credential_required`` line)
    and the provider rejecting the token (an auth error from a revoked or
    expired grant). The line is only a trigger (see ``dispatch.sign_in``): the
    server is the one the failing PID belongs to, and the provider must be one
    that server's installed manifest declares.
    """
    failures = [s for s in signals if s.get("type") in _FAILURE_SIGNAL_TYPES]
    if not failures:
        return ""
    claims = sign_in_flow.find_credential_required(failures)
    auth_failed = _contains_auth_error(signal_text)
    if not claims and not auth_failed:
        return ""

    hint = ""
    for sid in sorted(_server_ids_for_signals(app, failures, owning_goal)):
        try:
            manifest = await app.dispatch.get_server_manifest(sid)
        except Exception as e:  # a hint is best-effort; never break the turn
            logger.debug(f"Could not fetch manifest for {sid}: {e}")
            continue

        declared = sign_in_flow.declared_credentials(manifest)
        if declared:
            reasons = {
                c["provider"]: str(c.get("reason", ""))
                for c in claims
                if c.get("server") == sid and c.get("provider") in declared
            }
            if not reasons and auth_failed:
                reasons = {provider: "rejected" for provider in declared}
            for provider in sorted(reasons):
                hint += "\n" + sign_in_flow.sign_in_hint(
                    sid, manifest, provider, reasons[provider]
                )
            continue

        if not auth_failed:
            continue
        props = (manifest or {}).get("configurableProperties")
        required_keys = required_config_keys(props)
        if not required_keys:
            continue
        hint += (
            f"\nCONFIG_HINT: {sid} requires configuration."
            f"\n  Required key(s): {', '.join(required_keys)}\n"
            + "\n".join(configure_call_hint(sid, props))
        )
    return hint


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

    signal_text = json.dumps(signal)
    hint_signals: list[dict[str, Any]] = [signal]
    if exit_data is not None:
        signal_text += compact_payload_for_llm(exit_data)
        # A merged REMIND+EXIT arrives typed REMIND but carries the real EXIT
        # (event_merger.py). Hint off the outcome, not off the wrapper.
        if isinstance(exit_data, dict):
            hint_signals = [exit_data]
    context += await _failure_hints(app, logger, hint_signals, owning_goal, signal_text)

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

    context += await _failure_hints(
        app, logger, signals, owning_goal, json.dumps(signals)
    )

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
                    "confirmations": enrich_pending_with_goals(app),
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
                if app.goals.get_goal(pending.session_id) is None:
                    # A confirmation persisted across a restart outlives its
                    # goal — goals are archived at shutdown (#146). The tasks
                    # are already dispatched; every link_* below is a no-op for
                    # a missing goal, so say so once and carry on (#224).
                    logger.info(
                        "Confirmation %s resumed without goal %s "
                        "(archived across a restart); dispatched unlinked",
                        pending.request_id,
                        pending.session_id,
                    )
                app.goals.link_tasks(pending.session_id, pids)
                # Per-PID server attribution over the APPROVED subset — the only
                # tasks that actually ran. Keeping this separate from the
                # fingerprint is what makes a denied server structurally unable
                # to show up in a later CONFIG_HINT.
                app.goals.link_dispatch_servers(
                    pending.session_id, pids, pending.approved_tasks
                )
                # Re-establish the pid -> fingerprint mapping the direct path sets
                # at dispatch time (dispatch_flow.py). Without it the repeat guard
                # can never see a confirmation-gated tool's EXIT as progress, so a
                # genuinely advancing gated tool trips the guard (#205). Reuse the
                # exact fingerprint record_dispatch counted (the full original
                # batch), not one recomputed from the approved subset.
                if pending.fingerprint:
                    app.goals.link_dispatch_fingerprint(
                        pending.session_id, pending.fingerprint, pids
                    )

        send_error = isinstance(result, dict) and "error" in result
        # On the clean happy path (tasks accepted, nothing denied) go silent —
        # the tasks' EXIT signals drive the next ROOT turn once real results
        # exist. Only drive a turn here for what the signals won't carry: a
        # synchronous send error, or denied tools the user should hear about (#195).
        if not send_error and not pending.denied_tools:
            emit_activity(
                app, "Running approved tools; awaiting results…", kind="dispatch"
            )
            return

        context = build_root_context(app, logger)
        if send_error:
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
