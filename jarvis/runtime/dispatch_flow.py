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
from ..core.threat_level import ThreatLevel, classify
from . import action_events
from .llm_bridge import ask_llm
from .output_hooks import emit_activity, persist_assistant_turn
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


async def run_dispatch_subchain(
    app: Any,
    logger: Logger,
    intent: str,
    max_chain_depth: int,
    goal_id: Optional[str] = None,
) -> str:
    """Kept for compatibility; ROOT now uses search_tools/get_server_docs/dispatch directly."""
    return f"Direct dispatch unavailable — use search_tools to find tools for: {intent}"


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

    _no_timeouts(tasks)

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

    # FORBIDDEN means the tool never runs (mcp-registry#88): refused here, ahead
    # of should_confirm, because that is the layer allow_all skips. Every task is
    # classified before any is sent, so the whole batch is refused, not part of it.
    metas: list[dict[str, Any]] = []
    for task in tasks:
        tool_meta = await get_tool_metadata(app, logger, task)
        metas.append(tool_meta)
        if (
            classify(task.get("tool"), tool_meta, task.get("params"))
            >= ThreatLevel.FORBIDDEN
        ):
            blocked = f"{task.get('server', '?')}.{task.get('tool', '?')}"
            logger.warning(f"JARVIS: Dispatch blocked, FORBIDDEN tool: {blocked}")
            emit_activity(app, f"Blocked, forbidden: {blocked}", kind="dispatch")
            return {
                "error": (
                    f"Dispatch refused: {blocked} is classed FORBIDDEN and never "
                    "runs, whatever the user confirms or the confirmation mode. "
                    "Nothing in this batch was sent. Do not retry it or look for "
                    "another route to the same effect. Tell the user what was "
                    "blocked and why, then ask what they want to do instead."
                )
            }

    for task, tool_meta in zip(tasks, metas):
        tool_name = f"{task.get('server', '?')}.{task.get('tool', '?')}"

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
    )

    tool_names = [t["tool_name"] for t in tools_needing_confirmation]
    asked = [
        (t["task"], action_events.tool_detail(t["params"]))
        for t in tools_needing_confirmation
    ]
    action_events.start(
        app,
        "confirm",
        "Confirm " + ", ".join(action_events.tool_title(task) for task, _ in asked),
        (
            asked[0][1]
            if len(asked) == 1
            else "\n".join(f"{action_events.tool_title(t)}: {d}" for t, d in asked)
        ),
        action_id=f"confirm:{request_id}",
    )
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
    if await _serve_job_reads(app, logger, tasks, depth):
        return

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
    note_dispatched_tools(app, pids, tasks)
    if pids and goal_id:
        app.goals.link_tasks(goal_id, pids)
        app.goals.link_dispatch_fingerprint(goal_id, fingerprint, pids)
        # Per-PID server attribution alongside the batch fingerprint, so an
        # auth-error EXIT names only the server behind the failing task.
        app.goals.link_dispatch_servers(goal_id, pids, tasks)
    emit_activity(app, "Tasks dispatched; awaiting results…", kind="dispatch")


# --- reading a running job --------------------------------------------------
#
# The model kept calling read_output on a run_job that was still running: each
# read's EXIT woke it, so it read again, about fifteen times for one system
# update, and every read of the root shell server started it again under
# pkexec, a password prompt each (2026-10-23). dispatch already holds the
# job's live output (run_job streams it to the task's stderr; REMIND carries
# it), so a read of a running job is answered from there: once per reminder,
# then JARVIS tells the user the job is running and waits for the next
# REMIND or the EXIT instead of asking the model again.

# Characters of a running job's output shown for a read.
JOB_READ_TAIL = 4000


def _jobs(app: Any) -> dict:
    """run_job name -> {"pid", "server"} for jobs JARVIS started."""
    jobs = getattr(app, "_running_jobs", None)
    if jobs is None:
        jobs = app._running_jobs = {}
    return jobs


def _running_job(app: Any, task: Any) -> Optional[dict]:
    """The running job a task reads or starts again, if JARVIS started it.

    Starting a job that is still running is a read of it too: on reminders
    the model re-dispatched `run_job demo` while demo ran, a confirmation for
    the user each time, refused by the server ("already exists").
    """
    if not isinstance(task, dict) or task.get("tool") not in ("read_output", "run_job"):
        return None
    job = _jobs(app).get((task.get("params") or {}).get("job"))
    if job is None or job["server"] != task.get("server"):
        return None
    finished = getattr(app, "_signal_ledger", (set(), {}))[0]
    return None if job["pid"] in finished else job


async def _serve_job_reads(app: Any, logger: Logger, tasks: list, depth: int) -> bool:
    """Answer reads of running jobs without dispatching them; True when handled.

    A read beside the run_job that starts the same job is dropped (its output
    comes with the first reminder); the rest of that batch goes on as usual.
    """
    started = {
        (t.get("params") or {}).get("job")
        for t in tasks
        if isinstance(t, dict)
        and t.get("tool") == "run_job"
        and _running_job(app, t) is None
    }
    if started:
        kept = [
            t
            for t in tasks
            if not (
                isinstance(t, dict)
                and t.get("tool") == "read_output"
                and (t.get("params") or {}).get("job") in started
            )
        ]
        if len(kept) != len(tasks):
            logger.info("JARVIS: dropped a read of a job started in the same batch")
            tasks[:] = kept
        return False
    jobs = [_running_job(app, t) for t in tasks]
    if not tasks or any(job is None for job in jobs):
        return False

    newest = getattr(app, "_signal_ledger", (set(), {}))[1]
    names = [t["params"]["job"] for t in tasks]
    fresh = False
    for job in jobs:
        reminder = newest.get(job["pid"])
        if "read_at" not in job or job["read_at"] is not reminder:
            fresh = True
        job["read_at"] = reminder
    if not fresh:
        _wait_for_jobs(app, logger, names, jobs)
        return True

    try:
        listed = await app.dispatch.get_task_status(tail=JOB_READ_TAIL)
    except Exception:
        listed = []
    by_pid = {t.get("pid"): t for t in listed if isinstance(t, dict)}
    blocks = []
    for name, job in zip(names, jobs):
        tail = (by_pid.get(job["pid"]) or {}).get("tail")
        output = action_events.signal_output({"data": tail})[1] if tail else ""
        action = action_events.start(app, "tool", f"Output of job {name}")
        action_events.end(app, action, "ok", output or "(no output yet)")
        blocks.append(f"{name}:\n{output or '(no output yet)'}")
    logger.info(f"JARVIS: served read_output of running job(s) {names} from dispatch")
    context = build_root_context(app, logger)
    context += (
        "\nJOB_OUTPUT: "
        + ", ".join(names)
        + " still running; latest output, from dispatch:\n"
        + "\n".join(blocks)
        + "\nThey are still running: each REMIND brings their latest output and "
        "the EXIT their result, so do not read or start them again now. If the output asks "
        "a question, answer it with send_input; otherwise tell the user, briefly, "
        "what is happening (respond) and wait."
    )
    response = await ask_llm(app, logger, context, tag="root-job-output", mode="root")
    await app._act_on_root_response(response, depth + 1)
    return True


def _wait_for_jobs(app: Any, logger: Logger, names: list, jobs: list) -> None:
    """Read again before any news: tell the user once, then wait quietly."""
    logger.info(f"JARVIS: no news from job(s) {names} since the last read; waiting")
    if all(job.get("told") for job in jobs):
        emit_activity(app, f"{', '.join(names)} still running…", kind="dispatch")
        return
    for job in jobs:
        job["told"] = True
    text = (
        f"{', '.join(names)} {'is' if len(names) == 1 else 'are'} still running. "
        "I'll tell you when it needs you or when it's done."
    )
    app.output_manager.handle_response({"output": text})
    persist_assistant_turn(app, text)


_SHELL_TOOLS = ("execute_command", "execute_script", "run_job")
# Longest wait before the model hears about a command that had a timeout.
TIMEOUT_REMINDER_CAP = 60


def _no_timeouts(tasks: list) -> None:
    """Turn a shell command's timeout into a reminder, and fix run_job's command.

    A timeout kills a command wherever it is: a system update cut off
    mid-install can leave the machine unbootable, and three jobs waiting on
    pacman's "[Y/n]" died at it (2026-10-23). The shell servers run with no
    timeout of their own, so the one the model sets (it did in most runs,
    told not to) becomes a reminder no later than that: the model hears back
    and keeps waiting, answers, or kills the job itself.
    """
    for task in tasks:
        params = task.get("params")
        if task.get("tool") not in _SHELL_TOOLS or not isinstance(params, dict):
            continue
        timeout = params.pop("timeout", None)
        if timeout and not task.get("remind_after"):
            try:
                seconds = int(float(timeout))
            except (TypeError, ValueError):
                seconds = 0
            if seconds > 0:
                task["remind_after"] = min(seconds, TIMEOUT_REMINDER_CAP)
        # run_job runs one shell command string; models pass execute_command's
        # {command, args} split, and run_job would run the bare program.
        args = params.get("args")
        if (
            task.get("tool") == "run_job"
            and isinstance(params.get("command"), str)
            and isinstance(args, list)
        ):
            quoted = [shlex.quote(str(a)) for a in args]
            params["command"] = " ".join([params["command"], *quoted])
            del params["args"]


def note_dispatched_tools(app: Any, pids: list, tasks: list) -> None:
    """Remember, and show, which server and tool each dispatched PID runs.

    Paired like GoalManager.link_dispatch_servers: this batch's INITs are the
    newest, so the trailing len(tasks) PIDs belong to these tasks in order,
    and an ambiguous pairing records nothing. Kept on the app rather than a
    goal, since a sign-in can finish after its goal is closed.
    """
    if not pids or not tasks or len(pids) < len(tasks):
        return
    action_events.tools_started(app, pids, tasks)
    from . import stop

    stop.forget(app, pids)
    known = getattr(app, "_pid_tools", None)
    if known is None:
        known = app._pid_tools = {}
    for pid, task in zip(pids[-len(tasks) :], tasks):
        if isinstance(task, dict) and task.get("server") and task.get("tool"):
            known[pid] = (task["server"], task["tool"])
            job = (task.get("params") or {}).get("job")
            if task["tool"] == "run_job" and isinstance(job, str) and job:
                _jobs(app)[job] = {"pid": pid, "server": task["server"]}


async def do_kill(app: Any, logger: Logger, pids: Any) -> None:
    if not app.dispatch.is_connected:
        return
    result = await app.dispatch.kill_tasks(pids)
    if "error" in result:
        logger.error(f"JARVIS: Kill error: {result['error']}")
    else:
        logger.info(f"JARVIS: Killed PID(s): {pids}")
        action_events.stopped(app, pids if isinstance(pids, list) else [pids])


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
