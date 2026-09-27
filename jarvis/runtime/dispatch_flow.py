"""Dispatch-mode orchestration helpers."""

from __future__ import annotations

import json
import os
import re
import shlex
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

# Field separators for dispatch fingerprints — control chars that can't collide
# with server ids, tool names, or JSON payloads.
_FP_FIELD_SEP = "\x1f"
_FP_TASK_SEP = "\x1e"


def _task_fingerprint(task: dict[str, Any]) -> str:
    """Stable string over (server, tool, canonical params) for one task."""
    server = str(task.get("server", ""))
    tool = str(task.get("tool", ""))
    params = task.get("params", {})
    try:
        params_str = json.dumps(params, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        params_str = repr(params)
    return f"{server}{_FP_FIELD_SEP}{tool}{_FP_FIELD_SEP}{params_str}"


def _batch_fingerprint(tasks: list[dict[str, Any]]) -> str:
    """One fingerprint for a whole dispatch batch."""
    return _FP_TASK_SEP.join(_task_fingerprint(t) for t in tasks)


# Tools whose reply is a delivery receipt, never the work. send_input writes
# bytes into a job that keeps running, so the effect lands in that job's output;
# answering a wizard means dispatching the same (job, text) at each prompt
# ("y\n" to every "[Y/n]"), which is what the repeat guard would otherwise read
# as a stuck loop. Whether such a dispatch is going anywhere is decided from its
# EXIT status instead (goal_manager._note_dispatch_progress): a delivered answer
# is progress, a rejected one — the job is gone — still counts toward the limit.
_LIVE_INPUT_TOOLS = frozenset({"send_input"})


def _delivers_live_input(tasks: list[dict[str, Any]]) -> bool:
    """True when every task in the batch only feeds input to a running job."""
    return bool(tasks) and all(
        str(task.get("tool", "")).split(".")[-1].strip().lower() in _LIVE_INPUT_TOOLS
        for task in tasks
    )


def _tool_labels(tasks: list[dict[str, Any]]) -> list[str]:
    labels = []
    for task in tasks:
        label = f"`{task.get('server', '?')}.{task.get('tool', '?')}`"
        if label not in labels:
            labels.append(label)
    return labels


def _short_circuit_repeat(
    app: Any, logger: Logger, goal_id: str, tasks: list[dict[str, Any]]
) -> None:
    """Bail out of a no-progress dispatch loop with an informative respond (#205).

    Mirrors the ROOT respond path (root_actions.py) so the user sees a real
    answer, then fails and dismisses the goal so it stops re-driving.
    """
    labels = _tool_labels(tasks)
    label = ", ".join(labels)
    logger.warning(
        f"JARVIS: Dispatch repeat guard tripped for goal [{goal_id}] on {label}"
    )
    message = (
        f"I attempted to run {label} several times without making progress — "
        "the tool appears to be returning no usable content (e.g. an empty page "
        "snapshot). I've stopped retrying. You may want to try a different "
        "approach or tool."
    )
    app.output_manager.handle_response({"output": message})
    persist_assistant_turn(app, message)
    if hasattr(app, "mcp_dispatch_docs"):
        app.mcp_dispatch_docs.clear()
    app.goals.fail_goal(
        goal_id, reason=f"dispatch repeat guard: {label} made no progress"
    )
    app.goals.dismiss_failed()
    # Mirror the ROOT respond tail (root_actions.py): when history reset is on,
    # a normal respond clears the LLM conversation, so the guard's respond must
    # too — otherwise the bloated looping context that tripped the guard survives
    # into the next user turn, the opposite of what the config asks for (#205).
    if Config.RESET_HISTORY_AFTER_RESPONSE:
        app.llm.reset_history()


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
    """Kept for compatibility; ROOT now uses search_tools/get_server_docs/dispatch directly."""
    return f"Direct dispatch unavailable — use search_tools to find tools for: {intent}"


async def _run_dispatch_subchain_legacy(
    app: Any,
    logger: Logger,
    intent: str,
    max_chain_depth: int,
    goal_id: Optional[str] = None,
) -> str:
    """
    Legacy DISPATCH sub-chain. No longer reachable from ROOT since the new
    prompt generates search_tools → get_server_docs → dispatch instead of
    intent-only dispatch. Preserved for reference; will be deleted once the
    new flow is confirmed stable.

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
    # Track whether the plan step has been completed at least once.
    planned = False

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

        # ------------------------------------------------------------------
        # Guard: the LLM must plan before it can declare done or dispatch.
        # Some models skip straight to done at step 0 without attempting tool
        # discovery. Push back once so discover_tools gets a chance to run.
        # ------------------------------------------------------------------
        if action == "done" and not planned:
            logger.warning(
                "JARVIS: Dispatch LLM returned 'done' before planning — "
                "forcing plan step"
            )
            context = "\n".join(context_parts) + (
                "\nSYSTEM: You must output a 'plan' action first so tools can be "
                "located. Output a plan action now."
            )
            continue

        if action == "done":
            summary = parsed["summary"]
            logger.info(f"JARVIS: Dispatch sub-chain completed: {summary}")
            emit_activity(app, "Dispatch completed.", kind="dispatch")
            if goal:
                app.goals.complete_goal(goal.id, output=summary)
            return summary

        if action == "plan":
            planned = True
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
                # Collect configurableProperties before the server is first used
                await _collect_server_config(app, logger, server_id)
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


async def _collect_server_config(app: Any, logger: Logger, server_id: str) -> None:
    """After a successful install, prompt for configurableProperties if any are defined.

    Fetches the installed manifest, checks for configurableProperties, shows a
    Tkinter dialog to collect values, then persists them via dmcp config set.
    Safe to call on every install — skips silently if no props are defined or
    if Tkinter is unavailable.
    """
    manifest = await app.dispatch.get_server_manifest(server_id)
    if not manifest:
        return

    props = manifest.get("configurableProperties", [])
    if not props:
        return

    try:
        from ..ui.config_prompt import prompt_configurable_properties
    except ImportError:
        logger.warning("JARVIS: config_prompt unavailable (tkinter missing)")
        return

    emit_activity(app, "Server needs configuration — opening prompt…", kind="dispatch")
    collected = await prompt_configurable_properties(props)
    if not collected:
        logger.info(f"JARVIS: Config prompt skipped for {server_id}")
        return

    values = {k: v for k, v in collected.items() if v}
    if values:
        await app.dispatch.set_server_config(server_id, values)
        logger.info(
            f"JARVIS: Stored config for {server_id}: keys={list(values.keys())}"
        )


# Manifest-derived stateful flags, cached for the daemon's lifetime — the flag
# is a static property of an installed server and must not cost a manifest read
# per dispatch. Empty/failed lookups are NOT cached, so a server installed
# mid-session (or a transient read failure) is retried on the next dispatch
# instead of being pinned to the one-shot path until restart.
_STATEFUL_BY_SERVER: dict[str, bool] = {}


async def _stamp_stateful_tasks(
    app: Any, logger: Logger, tasks: list[dict[str, Any]]
) -> None:
    """Set stateful=True on tasks whose server manifest declares it (#206)."""
    for task in tasks:
        server_id = task.get("server")
        if not isinstance(server_id, str) or not server_id or "stateful" in task:
            continue
        if server_id not in _STATEFUL_BY_SERVER:
            try:
                manifest = await app.dispatch.get_server_manifest(server_id)
            except Exception as e:
                logger.warning(
                    f"JARVIS: stateful lookup failed for '{server_id}' ({e}); "
                    f"dispatching one-shot this time"
                )
                continue
            if not isinstance(manifest, dict) or not manifest or "error" in manifest:
                logger.debug(
                    f"JARVIS: no readable manifest for '{server_id}'; "
                    f"stateful undetermined, dispatching one-shot this time"
                )
                continue
            _STATEFUL_BY_SERVER[server_id] = bool(manifest.get("stateful"))
        if _STATEFUL_BY_SERVER[server_id]:
            task["stateful"] = True
            logger.debug(
                f"JARVIS: task {server_id}/{task.get('tool')} marked stateful "
                f"(manifest-derived)"
            )


def _check_constraints(tasks: list[dict[str, Any]]) -> str:
    """Return a refusal string if any task param matches a standing constraint, else ''.

    Path matching is deliberate narrow scope: absolute paths and ~/… only.
    Paths buried in shell one-liners, relative paths, and symlinks are not caught —
    the role-level permission and TLA gate are the backstop for those cases (#214).
    """
    from ..core.constraint_store import active_constraints
    from ..core.threat_level import _iter_strings

    constraints = active_constraints()
    if not constraints:
        return ""
    for task in tasks:
        params = task.get("params") or {}
        for s in _iter_strings(params):
            s = s.strip()
            if not s.startswith("/") and not s.startswith("~"):
                continue
            try:
                norm = os.path.abspath(os.path.expanduser(s))
            except Exception:
                continue
            for c in constraints:
                pat = c.get("pattern", "")
                if pat and (norm == pat or norm.startswith(pat.rstrip("/") + "/")):
                    return f'Path "{norm}" matches constraint #{c["id"]}: {c["text"]!r}'
    return ""


def _revoked_servers(app: Any, tasks: list[dict[str, Any]]) -> list[str]:
    """Servers in this batch the registry sweep marked revoked (#39).

    An absent or empty cache means nothing is known yet, not that everything is
    fine — it simply imposes no constraint, exactly as before the sweep existed.
    """
    cache = getattr(app.dispatch, "update_cache", None)
    if not isinstance(cache, dict) or not cache:
        return []

    revoked: list[str] = []
    for task in tasks:
        server_id = task.get("server")
        if not isinstance(server_id, str) or server_id in revoked:
            continue
        row = cache.get(server_id)
        if isinstance(row, dict) and row.get("trust_status") == "removed":
            revoked.append(server_id)
    return revoked


async def dispatch_send(
    app: Any,
    logger: Logger,
    tasks: list[dict[str, Any]],
    dispatch_context: Any = None,
    session_id: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> dict[str, Any]:
    """Low-level send to dispatch adapter, gated by TLA confirmation.

    fingerprint is the repeat-guard batch fingerprint the caller already counted
    (dispatch_execute_tasks). It is threaded onto the pending confirmation so the
    resume path can re-link the approved PIDs to it — without that, a
    confirmation-gated tool's EXIT can never reset the repeat window (#205).
    """
    if not app.dispatch.is_connected:
        emit_activity(app, "Dispatch is unavailable right now.", kind="dispatch")
        return {"error": "Dispatch not connected"}

    # Normalize common LLM schema mistakes:
    # - tool set to "server_id/tool_name" (fused) instead of separate fields
    # - server set to "local" (non-existent dmcp server id) while tool is fused
    for task in tasks:
        tool = task.get("tool")
        server = task.get("server")
        if isinstance(tool, str) and "/" in tool:
            fused_server, fused_tool = tool.split("/", 1)
            if (
                fused_server
                and fused_tool
                and (
                    not isinstance(server, str)
                    or not server
                    or server == "local"
                    or server != fused_server
                )
            ):
                task["server"] = fused_server
                task["tool"] = fused_tool

    # Normalize common "shell command" params for MCP servers that model expects
    # as {command: <exe>, args: [..]}. Many LLMs will put everything into
    # command="python --version"; convert that to command="python", args=["--version"].
    for task in tasks:
        if (
            task.get("tool") == "execute_command"
            and isinstance(task.get("params"), dict)
            and isinstance(task["params"].get("command"), str)
        ):
            params = task["params"]
            if not params.get("args") and " " in params["command"].strip():
                try:
                    parts = shlex.split(params["command"].strip())
                except ValueError:
                    parts = []
                if len(parts) >= 2:
                    params["command"] = parts[0]
                    params["args"] = parts[1:]

    # Standing constraints — path-prefix deny rules from constraints.json (#214).
    # Checked before revocation and before the confirmation gate so allow_all
    # cannot bypass them. The whole batch is refused on any match.
    constraint_violation = _check_constraints(tasks)
    if constraint_violation:
        logger.warning(
            f"JARVIS: Dispatch blocked by standing constraint: {constraint_violation}"
        )
        emit_activity(
            app, f"Blocked by constraint: {constraint_violation}", kind="dispatch"
        )
        return {
            "error": (
                f"Dispatch refused: a standing constraint blocks this action. "
                f"{constraint_violation} "
                "The constraint was set by the user and cannot be overridden here. "
                "Tell the user what was blocked and why, then ask what they want to do instead."
            )
        }

    # Registry revocation is a hard stop, decided here rather than asked of the
    # LLM (#39): a "removed" server is one whose vetting the registry withdrew,
    # and the LLM may still be holding its docs from earlier in the goal. The
    # whole batch is refused, not just the offending task — silently dropping
    # one task from a batch the LLM believes it dispatched is the worse failure.
    revoked = _revoked_servers(app, tasks)
    if revoked:
        names = ", ".join(revoked)
        logger.warning(f"JARVIS: Refusing dispatch to revoked server(s): {names}")
        emit_activity(app, f"Refused: {names} was revoked.", kind="dispatch")
        return {
            "error": (
                f"Refused to dispatch: {names} was REVOKED by the MCP registry and "
                "must not be used. Nothing in this batch was sent. Uninstall it with "
                "uninstall_server, tell the user it was revoked, and use search_tools "
                "to find a replacement."
            )
        }

    # Stamp stateful from the server manifest (#206) BEFORE the confirmation
    # gate so the flag also rides on approved_tasks held by a pending
    # confirmation. The LLM is deliberately not responsible for this: a
    # forgotten flag silently degrades a stateful server (browser, REPL) to
    # the one-shot lifecycle that loses its state between calls.
    await _stamp_stateful_tasks(app, logger, tasks)

    approved_tasks: list[dict[str, Any]] = []
    tools_needing_confirmation: list[dict[str, Any]] = []

    for task in tasks:
        tool_name = f"{task.get('server', '?')}.{task.get('tool', '?')}"
        tool_meta = await get_tool_metadata(app, logger, task)

        if app.confirmation.should_confirm(
            tool_meta, tool_name=task.get("tool"), params=task.get("params")
        ):
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
        session_id=session_id,
        fingerprint=fingerprint,
        chat_session_id=getattr(getattr(app, "sessions", None), "current_id", None),
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


# Manifest-derived per-tool threat declarations, cached for the daemon's
# lifetime alongside _STATEFUL_BY_SERVER (same rationale — a static property of
# an installed server that must not cost a manifest read per dispatch).
# `threat_level` and the legacy `confirmation_required` are REGISTRY-manifest
# fields; they are absent from the MCP `tools/list` output that
# `list_server_tools` returns, so without merging them here the TLA gate's
# `threat_level._declared()` only ever saw SAFE and every manifest declaration
# was inert — a dangerous tool whose bare NAME is not in the host floor
# (blender's execute_blender_code, the whole filesystem server) escaped
# confirmation. Empty/failed reads are NOT cached, so a server installed
# mid-session or a transient read failure is retried on the next dispatch.
_THREAT_DECL_BY_SERVER: dict[str, dict[str, Any]] = {}


async def _server_threat_decls(
    app: Any, logger: Logger, server_id: str
) -> Optional[dict[str, Any]]:
    """``{"tier": str, "tools": {name: {threat_level, confirmation_required}}}``.

    Read locally (``get_server_manifest`` — no network). The tier is the
    install-time ``trustStatus`` dmcp recorded from the registry entry;
    anything unrecognizable — absent (connect-installed servers, pre-field
    installs), blank, or non-string — reads as ``"unknown"``, and legacy
    vocabulary (``vetted``/``unreviewed``) deliberately passes through
    un-aliased so it lands in the unreviewed bucket until a reinstall
    refreshes it. Returns ``None`` when the manifest cannot be read (missing,
    unreadable, or carries an ``error``); the caller then floors the tool at
    ELEVATED. A readable manifest is cached even when it declares nothing, so
    a genuinely undeclared server is not re-read every dispatch.
    """
    cached = _THREAT_DECL_BY_SERVER.get(server_id)
    if cached is not None:
        return cached
    try:
        manifest = await app.dispatch.get_server_manifest(server_id)
    except Exception as e:
        logger.warning(
            f"JARVIS: threat-level manifest lookup failed for '{server_id}' ({e})"
        )
        return None
    if not isinstance(manifest, dict) or not manifest or "error" in manifest:
        return None
    raw_tier = manifest.get("trustStatus")
    tier = (
        raw_tier.strip().lower()
        if isinstance(raw_tier, str) and raw_tier.strip()
        else "unknown"
    )
    decls: dict[str, dict[str, Any]] = {}
    tools = manifest.get("tools")
    if isinstance(tools, list):
        for entry in tools:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            decl: dict[str, Any] = {}
            if "threat_level" in entry:
                decl["threat_level"] = entry["threat_level"]
            if "confirmation_required" in entry:
                decl["confirmation_required"] = entry["confirmation_required"]
            if decl:
                decls[name] = decl
    info = {"tier": tier, "tools": decls}
    _THREAT_DECL_BY_SERVER[server_id] = info
    return info


async def get_tool_metadata(
    app: Any, logger: Logger, task: dict[str, Any]
) -> dict[str, Any]:
    """Retrieve metadata for a task's tool, merged with its manifest threat level.

    The runtime ``tools/list`` view (name/description/inputSchema) is the base;
    the registry manifest's per-tool ``threat_level`` / ``confirmation_required``
    are merged on top so the TLA gate actually classifies from the author's
    declaration. For those two keys the MANIFEST is authoritative and
    overwrites the runtime value: ``tools/list`` cannot legitimately carry
    them (they are registry-manifest fields absent from protocol output), so a
    runtime value is server-controlled injection and must not shadow the
    reviewed declaration. The daemon also stamps ``registry_tier`` (the
    install-time trust tier) and ``registry_declared`` (this tool has a
    manifest declaration) unconditionally — server-scope provenance the
    server's own output can never supply — which feed classify()'s tier floor
    (#223): an unreviewed tool floors at ELEVATED.
    """
    server_id = task.get("server")
    tool_name = task.get("tool")
    if not server_id or not tool_name:
        return {}

    meta: dict[str, Any] = {}
    try:
        tools = await app.dispatch.list_server_tools(server_id)
        if isinstance(tools, dict) and "tools" in tools:
            for tool in tools["tools"]:
                if isinstance(tool, dict) and tool.get("name") == tool_name:
                    meta = dict(tool)
                    break
    except Exception as e:
        logger.debug(f"Could not fetch metadata for {server_id}.{tool_name}: {e}")

    # Merge the manifest's declared threat level onto the runtime metadata.
    # Both fallbacks are logged, not swallowed (contrast the constraint-store
    # fail-open): an unreadable manifest reopens the unknown-name gap for the
    # WHOLE server — the TLA gate loses every declaration it carried and drops
    # back to the host floor + payload scan, which is exactly what left
    # decorative declarations inert in the first place. A readable manifest that
    # does not declare this specific tool falls back the same way; with the
    # audit at 215/215 declared that is itself anomalous, so it warns too.
    info = await _server_threat_decls(app, logger, server_id)
    if info is None:
        logger.warning(
            f"JARVIS: no readable manifest for '{server_id}'; TLA cannot see its "
            f"declared threat levels — '{tool_name}' floors at ELEVATED "
            f"(unknown tier) on top of host floor + payload scan"
        )
        meta["registry_tier"] = "unknown"
        meta["registry_declared"] = False
        return meta
    meta["registry_tier"] = info["tier"]
    decl = info["tools"].get(tool_name)
    meta["registry_declared"] = bool(decl)
    if not decl:
        logger.warning(
            f"JARVIS: '{server_id}/{tool_name}' declares no threat_level in its "
            f"manifest — TLA floors it at ELEVATED on top of host floor + "
            f"payload scan"
        )
        return meta
    for key in ("threat_level", "confirmation_required"):
        if key in decl:
            meta[key] = decl[key]
    return meta


async def dispatch_execute_tasks(
    app: Any,
    logger: Logger,
    tasks: list[dict[str, Any]],
    depth: int,
) -> None:
    """Handle a dispatch action that already has concrete tasks (from root).

    The success path is silent: the tasks' EXIT signals (on_dispatch_signal /
    on_dispatch_signals) drive the next ROOT turn once real results exist.
    Asking here would only show an INIT-only window and duplicate that turn (#195).
    """
    if not app.dispatch.is_connected:
        app.output_manager.handle_response(
            {"output": "I can't execute tools right now — dispatch is not connected."}
        )
        return

    # Scope the dispatch to the active goal so its PIDs link back to it and every
    # signal turn is goal-scoped, instead of logging "No goal found for PID" and
    # falling back to full-root context (#196).
    active = app.goals.get_active_goals()
    goal_id = active[-1].id if active else None

    # Per-goal repeat/progress guard (#205). This choke point sees every ROOT
    # dispatch; count identical (server, tool, params) dispatches within the
    # goal and stop before re-sending a tool that keeps returning nothing.
    fingerprint = _batch_fingerprint(tasks)
    if goal_id:
        count = app.goals.record_dispatch(
            goal_id, fingerprint, delivers_input=_delivers_live_input(tasks)
        )
        if count >= Config.DISPATCH_REPEAT_LIMIT:
            _short_circuit_repeat(app, logger, goal_id, tasks)
            return

    result = await dispatch_send(
        app, logger, tasks, session_id=goal_id, fingerprint=fingerprint
    )

    if isinstance(result, dict) and result.get("awaiting_confirmation"):
        logger.info(
            f"JARVIS: Root dispatch paused for confirmation "
            f"id={result['confirmation_id']}"
        )
        return

    if isinstance(result, dict) and "error" in result:
        # A synchronous send error produces no EXIT signal — surface it now,
        # otherwise the goal would stall waiting for a wakeup that never comes.
        app.llm.switch_mode("root")
        context = build_root_context(app, logger)
        context += f"\nDISPATCH_ERROR: {compact_payload_for_llm(result)}"
        response = await ask_llm(app, logger, context, tag="root-dispatch-result")
        await app._act_on_root_response(response, depth + 1)
        return

    pids = _extract_pids_from_result(result)
    if pids and goal_id:
        app.goals.link_tasks(goal_id, pids)
        app.goals.link_dispatch_fingerprint(goal_id, fingerprint, pids)
        # Per-PID server attribution alongside the batch fingerprint, so an
        # auth-error EXIT names only the server behind the failing task.
        app.goals.link_dispatch_servers(goal_id, pids, tasks)
    emit_activity(app, "Tasks dispatched; awaiting results…", kind="dispatch")


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
