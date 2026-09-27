"""
Confirmation Manager — Threat Level Access (TLA) confirmation gate.

MCP server developers declare ``confirmation_required: true`` on tools that
need user approval before execution.  The ConfirmationManager reads that
metadata at dispatch time and gates execution accordingly.

**Non-blocking / event-driven design:**

The manager never ``await``s a user response.  Instead it:

1. Stashes pending tasks with their confirmation id.
2. Sends a notification (desktop / socket / CLI prompt) — fire-and-forget.
3. Returns immediately so the event loop stays free for signals, reminders,
   and new user input.
4. When the user responds, the response arrives as a
   ``CONFIRMATION_RESPONSE`` event through the EventMerger.
5. Jarvis calls ``resolve()`` which returns the stashed tasks and approval
   status, and Jarvis resumes the dispatch.

Three delivery channels, chosen automatically by availability:

1. **Desktop notification** — ``notify-send`` (Linux) with Allow / Deny
   actions.  Skipped when ``notification_silent`` is True.
2. **Socket** — structured JSON on the JARVIS output socket so external
   apps can render their own confirmation UI.
3. **CLI / TTY** — ``[y/N]`` stdin prompt (runs in executor, response
   injected back via EventMerger).

The global ``CONFIRMATION_MODE`` (config) controls overall behaviour:

* ``allow_all``  — skip confirmation, always approve
* ``smart``      — only ask when the tool sets ``confirmation_required``
* ``ask_all``    — ask for every tool call regardless of metadata

**Persistence (#224):**

When constructed with a ``store_path`` the pending list is mirrored to that
JSON file on every mutation (atomic tmp + ``os.replace``) and restored at
construction, so a confirmation survives a daemon restart with its
``created_at`` and ``session_id`` intact and stays resolvable through ``jarvis
confirm``.  Without a ``store_path`` the manager is memory-only and touches no
filesystem at all.

**Offline decision queue:**

Because the store is a plain file, ``jarvis confirm`` can read it with the
daemon down.  Decisions made there are appended to
``$JARVIS_DATA_DIR/confirmation_decisions.json`` as the same socket-protocol
messages a live client would send, and the daemon replays them at startup
(``runtime/lifecycle.apply_queued_confirmation_decisions``).  The queue file's
protection is the same same-user filesystem boundary the local socket model
relies on — see ``decision_queue_path()``.

**Resolved history (#235):**

``resolve()`` is the one place an entry leaves ``_pending``, so it is also
where a record of the outcome is appended to a bounded ring at
``$JARVIS_DATA_DIR/confirmations_history.json``.  That makes "what did I
approve earlier?" answerable after the entry itself is gone, in every review
surface at once rather than only in whichever UI happened to be open.  The ring
is capped (``HISTORY_LIMIT``) because this is a review aid, not an audit log —
see ``_append_history()``.
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..config import Config
from .logger import get_logger
from .threat_level import ThreatLevel, classify

logger = get_logger(__name__)

# No auto-deny by default (#185): a confirmation the user hasn't answered stays
# pending rather than being denied on a timer. A positive CONFIRMATION_TIMEOUT
# restores an explicit bounded lifetime for unattended/headless setups.
DEFAULT_TIMEOUT = 0

# Longest rendered command line shown in a confirmation prompt before eliding.
_MAX_CMD_LEN = 200


def _truncate(text: str, limit: int = _MAX_CMD_LEN) -> str:
    """Collapse whitespace and elide to ``limit`` chars with an ellipsis."""
    text = " ".join(str(text).split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _short_value(value: Any, limit: int = 60) -> str:
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    else:
        rendered = str(value)
    return _truncate(rendered, limit)


def describe_tool_call(detail: Dict[str, Any]) -> str:
    """Render a one-line, human-readable command for a tool call.

    The confirmation gate is the human-in-the-loop boundary; a boundary that
    can't see *what* it is approving is not a boundary (#186). This turns a
    task's params into the actual command — ``pacman -Syu --noconfirm`` — rather
    than just the tool identity. Falls back to ``key=value`` for arbitrary tools.
    """
    task = detail.get("task") or {}
    params = detail.get("params")
    if not isinstance(params, dict):
        tp = task.get("params")
        params = tp if isinstance(tp, dict) else {}

    if params:
        command = params.get("command")
        if isinstance(command, str) and command:
            args = params.get("args")
            if isinstance(args, list):
                command = " ".join([command] + [str(a) for a in args])
            extras = []
            if params.get("cwd"):
                extras.append(f"cwd={params['cwd']}")
            if params.get("timeout"):
                extras.append(f"timeout={params['timeout']}s")
            suffix = f"  [{', '.join(extras)}]" if extras else ""
            return _truncate(command) + suffix

        script = params.get("script")
        if isinstance(script, str) and script:
            return _truncate(script.replace("\n", " ; "))

        kv = ", ".join(f"{k}={_short_value(v)}" for k, v in params.items())
        return _truncate(kv)

    # No params: the tool name itself is the most specific thing we can show.
    return str(task.get("tool") or detail.get("tool_name") or "(no parameters)")


def confirmation_line(detail: Dict[str, Any]) -> str:
    """``<tool_name>: <rendered command>`` for one confirmation item (#186)."""
    tool_name = detail.get("tool_name", "?")
    return f"{tool_name}: {describe_tool_call(detail)}"


@dataclass
class PendingConfirmation:
    """A set of tasks awaiting user approval."""

    request_id: str
    tasks: List[Dict[str, Any]]
    denied_tools: List[str] = field(default_factory=list)
    approved_tasks: List[Dict[str, Any]] = field(default_factory=list)
    # Ordered detail for the tools that need confirmation, so a per-task
    # response (approved_indices) can approve a subset and deny the rest (#187).
    confirm_details: List[Dict[str, Any]] = field(default_factory=list)
    # Dispatch sub-chain context to resume after confirmation.
    dispatch_context: Optional[Dict[str, Any]] = None
    # The owning goal's id (dispatch session_id) so the resume path can scope the
    # dispatch to the goal and link the returned PIDs back to it (#190).
    session_id: Optional[str] = None
    # The CHAT session this was raised in. Distinct from session_id above, which
    # is a goal id — Goal carries no back-reference to the conversation, so
    # without this field there is no route from a confirmation to the chat that
    # produced it, and a review surface cannot scope to "this session" (#235).
    chat_session_id: Optional[str] = None
    # The full-batch dispatch fingerprint the repeat guard counted for this batch
    # (dispatch_flow._batch_fingerprint). Carried so the resume path can re-link
    # the approved PIDs to it and a later EXIT can reset the repeat window — the
    # direct path does this at dispatch time, but a confirmed batch is sent later
    # from on_confirmation_response and would otherwise never reset (#205).
    fingerprint: Optional[str] = None
    # Retained so a later list_pending() query can describe what's waiting --
    # the original request only computes these locally otherwise.
    tool_names: List[str] = field(default_factory=list)
    # Human-readable "tool: command" lines shown in every channel (#186).
    tool_lines: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


# ----------------------------------------------------------------------
# On-disk locations (#224 store, and the offline decision queue built on it)
# ----------------------------------------------------------------------

STORE_FILENAME = "confirmations.json"
DECISION_QUEUE_FILENAME = "confirmation_decisions.json"
HISTORY_FILENAME = "confirmations_history.json"

# Resolved entries kept for review. A cap rather than unbounded growth because
# this answers "what did I approve earlier?", which is a recent-memory question
# — it is not the audit log, and nothing depends on an entry still being here.
HISTORY_LIMIT = 200


def store_path() -> Path:
    """Where the daemon mirrors its pending list."""
    return Path(Config.JARVIS_DATA_DIR) / STORE_FILENAME


def history_path() -> Path:
    """Where resolved confirmations are ringed for after-the-fact review."""
    return Path(Config.JARVIS_DATA_DIR) / HISTORY_FILENAME


def decision_queue_path() -> Path:
    """Where ``jarvis confirm`` parks decisions made while the daemon is off.

    Security: this file carries the same authority as a message on the input
    socket, and it is protected the same way — the same-user filesystem
    boundary under ``JARVIS_DATA_DIR``. The daemon trusts a queued decision
    exactly as far as it trusts a local same-user socket client, no further.
    On Windows the analogue is the file's ACL, inherited from the data
    directory (best-effort, as with the socket hardening there).

    No explicit chmod is needed on POSIX: ``_atomic_write_json`` writes through
    ``mkstemp`` (0600) and ``os.replace`` keeps the tmp file's mode.
    """
    return Path(Config.JARVIS_DATA_DIR) / DECISION_QUEUE_FILENAME


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write ``payload`` to ``path`` via tmp + ``os.replace``.

    A crash mid-write leaves the previous file intact rather than a half one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_store(path: Path) -> Dict[str, PendingConfirmation]:
    """Parse a persisted pending list. Raises on an unreadable/corrupt file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("store is not a list of confirmation records")
    known = {f.name for f in fields(PendingConfirmation)}
    restored: Dict[str, PendingConfirmation] = {}
    for record in data:
        if not isinstance(record, dict) or not record.get("request_id"):
            continue
        pending = PendingConfirmation(**{k: v for k, v in record.items() if k in known})
        restored[pending.request_id] = pending
    return restored


def _summarize(p: PendingConfirmation) -> Dict[str, Any]:
    """The per-entry shape every review surface (CLI, GUI socket) renders."""
    return {
        "id": p.request_id,
        "tool_names": p.tool_names,
        "tool_lines": p.tool_lines,
        "created_at": p.created_at,
        "session_id": p.session_id,
        "chat_session_id": p.chat_session_id,
    }


def load_pending_summaries(path: Path) -> List[Dict[str, Any]]:
    """``list_pending()`` straight off the store, with no manager involved.

    ``jarvis confirm`` uses this when the daemon is unreachable: same records,
    same summary shape, no notification channels and no restore-time logging
    in the CLI's face.
    """
    if not path.exists():
        return []
    try:
        return [_summarize(p) for p in _read_store(path).values()]
    except Exception as e:
        logger.warning("Could not read pending confirmations from %s: %s", path, e)
        return []


def load_history(path: Path) -> List[Dict[str, Any]]:
    """Resolved confirmations, newest first; ``[]`` when absent or unreadable.

    Read by the same review surfaces that call ``load_pending_summaries`` —
    including with no daemon running, since this is a plain file too.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("history is not a list of resolved records")
        return [r for r in data if isinstance(r, dict) and r.get("id")]
    except Exception as e:
        logger.warning("Could not read confirmation history from %s: %s", path, e)
        return []


def _decision_key(decision: Dict[str, Any]) -> str:
    """One decision per confirmation id; ``approve-all`` keys on its type."""
    return str(decision.get("id") or decision.get("type", ""))


def _quarantine(path: Path) -> Path:
    """Park an unparseable file next to itself as ``.bad`` and report where.

    Deleting it would destroy the only record of what the user decided, so a
    corrupt queue is kept for inspection instead.
    """
    bad = path.with_name(path.name + ".bad")
    path.replace(bad)
    return bad


def load_decision_queue(path: Path, quarantine: bool = False) -> List[Dict[str, Any]]:
    """Read offline decisions in the order they were made; ``[]`` when absent.

    ``quarantine`` is for callers that are about to replace the file (the
    daemon consuming the queue, the CLI appending to it): a corrupt file is
    renamed to ``<name>.bad`` first so it is never silently overwritten.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("decision queue is not a list")
        return [d for d in data if isinstance(d, dict) and d.get("type")]
    except Exception as e:
        kept = ""
        if quarantine:
            try:
                kept = f"; kept as {_quarantine(path)}"
            except OSError:
                pass
        logger.warning("Unreadable confirmation decision queue %s: %s%s", path, e, kept)
        return []


def queue_decision(decision: Dict[str, Any], path: Path) -> bool:
    """Append one socket-protocol decision message; returns True if it replaced.

    Last writer wins per confirmation id: changing your mind about ``abc123``
    rewrites that entry rather than queueing a second, contradictory one.
    """
    existing = load_decision_queue(path, quarantine=True)
    key = _decision_key(decision)
    queued = [d for d in existing if _decision_key(d) != key]
    replaced = len(queued) != len(existing)
    queued.append(decision)
    _atomic_write_json(path, queued)
    return replaced


def clear_decision_queue(path: Path) -> None:
    """Drop the queue file once every decision in it has been applied."""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Could not remove applied decision queue %s: %s", path, e)


class ConfirmationManager:
    """Non-blocking, event-driven tool confirmation gate."""

    def __init__(self, store_path: Optional[str] = None) -> None:
        # Pending confirmations keyed by request id.
        self._pending: Dict[str, PendingConfirmation] = {}
        # None = memory-only: no restore, no writes, no filesystem contact.
        self._store_path: Optional[Path] = (
            Path(store_path).expanduser() if store_path else None
        )
        # The resolved ring sits beside the store, so a memory-only manager has
        # no history file either — one switch decides all filesystem contact.
        self._history_path: Optional[Path] = (
            self._store_path.with_name(HISTORY_FILENAME)
            if self._store_path is not None
            else None
        )
        # External output callback (set by Jarvis to broadcast via socket).
        self._output_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._has_socket_clients: Optional[Callable[[], bool]] = None
        # Injector for confirmation responses into the event loop.
        self._inject_confirmation: Optional[Callable[[Dict[str, Any]], None]] = None
        # Timeout tasks keyed by request id (so we can cancel on response).
        self._timeout_tasks: Dict[str, asyncio.Task] = {}
        # TUI callback — async callable that pushes ConfirmModal and returns bool.
        self._tui_callback: Optional[Callable[[str, List[str]], Any]] = None
        self._restore()

    # ------------------------------------------------------------------
    # Persistence (#224)
    # ------------------------------------------------------------------

    def _restore(self) -> None:
        """Load the persisted pending list at startup.

        Restored entries are review-queue items, not live requests: no channel
        notification is re-fired (the moment for a toast has passed) and no
        timeout task is armed for them regardless of ``CONFIRMATION_TIMEOUT``,
        since a clock started before the restart would fire on an arbitrary
        remainder. They simply appear in ``list_pending()`` for CLI/GUI review
        with their original ``created_at`` and ``session_id``.

        Fails open: an absent, unreadable, or corrupt store logs a warning and
        starts empty rather than blocking startup. Nothing is lost that could
        run unapproved — the gated tasks were never dispatched.
        """
        if self._store_path is None or not self._store_path.exists():
            return

        try:
            restored = _read_store(self._store_path)
        except Exception as e:
            logger.warning(
                "Could not read pending confirmations from %s: %s; starting empty",
                self._store_path,
                e,
            )
            return

        self._pending = restored
        if restored:
            logger.info(
                "Restored %d pending confirmation(s) from %s",
                len(restored),
                self._store_path,
            )

    def _persist(self) -> None:
        """Mirror ``_pending`` to the store after every mutation.

        Written atomically so a crash mid-write never leaves a half-file. A
        write failure is logged, not raised: the in-memory list is still
        correct and the dispatch gate must not fall over because a disk is
        full.

        Every ``PendingConfirmation`` field is plain data (the runtime-only
        timeout tasks live in ``_timeout_tasks``, never here), but tool params
        arrive from an LLM-parsed payload, so an exotic value falls back to its
        string form rather than aborting the write — a resolve whose write
        aborted would leave the store claiming a confirmation that no longer
        exists.
        """
        if self._store_path is None:
            return
        try:
            _atomic_write_json(
                self._store_path, [asdict(p) for p in self._pending.values()]
            )
        except Exception as e:
            logger.warning(
                "Could not persist pending confirmations to %s: %s",
                self._store_path,
                e,
            )

    def _append_history(
        self,
        pending: PendingConfirmation,
        outcome: str,
        approved_indices: Optional[List[int]] = None,
    ) -> None:
        """Record a resolved confirmation in the bounded ring (#235).

        Called from ``resolve()``, the single point an entry leaves
        ``_pending``, so every channel and the auto-deny timer land here
        without each having to remember to.

        Best-effort like ``_persist()``: a failed write is logged, never
        raised. Losing a history line is a degraded review view; letting it
        propagate would fail a resolve whose approval has already been applied.
        """
        if self._history_path is None:
            return
        record = _summarize(pending)
        record["outcome"] = outcome
        record["resolved_at"] = time.time()
        if approved_indices is not None:
            record["approved_indices"] = sorted(approved_indices)
        try:
            existing = load_history(self._history_path)
            # Newest first, so the cap drops the oldest.
            _atomic_write_json(self._history_path, [record, *existing][:HISTORY_LIMIT])
        except Exception as e:
            logger.warning(
                "Could not record resolved confirmation to %s: %s",
                self._history_path,
                e,
            )

    def list_history(self) -> List[Dict[str, Any]]:
        """Resolved confirmations, newest first — empty when memory-only."""
        if self._history_path is None:
            return []
        return load_history(self._history_path)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def set_output_callback(
        self,
        callback: Callable[[Dict[str, Any]], None],
        has_clients: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Register the output socket broadcast callback."""
        self._output_callback = callback
        self._has_socket_clients = has_clients

    def set_tui_callback(
        self,
        callback: Callable[[str, List[Dict[str, Any]]], Any],
    ) -> None:
        """Register the TUI confirmation callback.

        The callback receives (request_id, tools_detail) — the list of
        ``{tool_name, task, params, ...}`` dicts — and must return a bool
        (True = allow, False = deny).  It is awaited, so it can use Textual's
        ``push_screen_wait``.
        """
        self._tui_callback = callback

    def set_event_injector(
        self,
        injector: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Register the EventMerger's inject_confirmation_response callable.

        This is how desktop notification and CLI channels push their
        responses back into the event loop without blocking.
        """
        self._inject_confirmation = injector

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_confirm(
        self,
        tool_metadata: Dict[str, Any],
        tool_name: Optional[str] = None,
        params: Any = None,
    ) -> bool:
        """Decide whether a tool invocation needs confirmation.

        In ``smart`` mode the *host* sets a minimum threat level per tool (see
        ``threat_level.classify``): host-classified dangerous tools (e.g.
        command execution, which can escalate via sudo) are always confirmed
        regardless of their manifest, and a manifest may only raise the level,
        never lower it below the host floor. The ``params`` are also scanned for
        dangerous payloads (``rm -rf``, ``| sh`` …) so a host-safe tool handed a
        destructive argument is confirmed too. ``allow_all`` / ``ask_all``
        override as before.
        """
        mode = Config.CONFIRMATION_MODE

        if mode == "allow_all":
            return False
        if mode == "ask_all":
            return True

        # "smart" — confirm at or above ELEVATED. classify() folds in the host
        # floor, the tool's own confirmation_required / threat_level, AND a scan
        # of the params for dangerous payloads (a safe tool + a destructive arg).
        return classify(tool_name, tool_metadata, params) >= ThreatLevel.ELEVATED

    async def request_confirmation(
        self,
        request_id: str,
        tasks: List[Dict[str, Any]],
        tools_needing_confirmation: List[Dict[str, Any]],
        approved_tasks: List[Dict[str, Any]],
        denied_tools: List[str],
        dispatch_context: Optional[Dict[str, Any]] = None,
        notification_silent: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
        session_id: Optional[str] = None,
        fingerprint: Optional[str] = None,
        chat_session_id: Optional[str] = None,
    ) -> None:
        """Send confirmation notification and return immediately.

        Does NOT block.  The response will arrive later as a
        ``CONFIRMATION_RESPONSE`` event through the EventMerger.

        Args:
            request_id: Unique id for this confirmation batch.
            tasks: The original full task list.
            tools_needing_confirmation: List of ``{tool_name, tool, params, meta}``
                dicts for tools that need confirmation.
            approved_tasks: Tasks already approved (no confirmation needed).
            denied_tools: Tools already denied (for partial batches).
            dispatch_context: Opaque context to resume dispatch after response.
            notification_silent: Suppress desktop notification.
            timeout: Seconds before auto-deny. 0 (the default) means never
                auto-deny — the confirmation stays pending until answered (#185).
            session_id: Owning goal id, stored so the resume path can rescope.
            fingerprint: Repeat-guard batch fingerprint, stored so the resume
                path can re-link approved PIDs and let a later EXIT reset the
                repeat window (#205).
            chat_session_id: The conversation this was raised in, for review
                surfaces that scope to one session (#235). Purely descriptive —
                the resume path never reads it.
        """
        # Build a human-readable summary of tools needing confirmation. The
        # summary now carries the actual command (#186), not just the tool name,
        # so every channel shows WHAT will run.
        tool_names = [t["tool_name"] for t in tools_needing_confirmation]
        tool_lines = [confirmation_line(t) for t in tools_needing_confirmation]

        pending = PendingConfirmation(
            request_id=request_id,
            tasks=tasks,
            approved_tasks=list(approved_tasks),
            denied_tools=list(denied_tools),
            dispatch_context=dispatch_context,
            session_id=session_id,
            chat_session_id=chat_session_id,
            fingerprint=fingerprint,
            tool_names=tool_names,
            tool_lines=tool_lines,
            confirm_details=list(tools_needing_confirmation),
        )
        self._pending[request_id] = pending
        # Persist before notifying: a crash between the two must leave the
        # request reviewable, not lost.
        self._persist()

        logger.info(
            "Confirmation requested (non-blocking): id=%s, tools=%s",
            request_id,
            "; ".join(tool_lines),
        )

        # Fire notification (non-blocking) on the best available channel.
        await self._send_notification(
            request_id=request_id,
            tool_names=tool_names,
            tool_lines=tool_lines,
            tools_detail=tools_needing_confirmation,
            notification_silent=notification_silent,
            timeout=timeout,
        )

        # Start timeout only if one is configured. The default (0) leaves
        # the confirmation pending indefinitely -- it's tracked in
        # list_pending() and reviewable via CLI/socket at any time, so
        # nothing needs to expire on a clock. A positive value restores the
        # old auto-deny behavior for unattended/headless setups.
        if timeout and timeout > 0:
            self._timeout_tasks[request_id] = asyncio.create_task(
                self._auto_deny_after(request_id, timeout)
            )

    def list_pending(self) -> List[Dict[str, Any]]:
        """Summaries of currently pending confirmations, for CLI/socket review.

        ``session_id`` is the owning goal's id (#190 carries it into
        ``PendingConfirmation`` at request time) — callers resolve it to a
        goal description via ``GoalManager`` (#192), this manager has no
        goal-tree access of its own.
        """
        return [_summarize(p) for p in self._pending.values()]

    def resolve(self, response: Dict[str, Any]) -> Optional[PendingConfirmation]:
        """Process a confirmation response and return the pending data.

        Called by Jarvis when a ``CONFIRMATION_RESPONSE`` event arrives.

        Two response shapes are accepted:

        * ``approved_indices``: a list of indices into the confirmation items
          (``confirm_details``) that the user approved. The rest are denied.
          This is the per-task path (#187) — approve some, deny others.
        * ``approved`` (bool): the all-or-nothing path used by channels that
          can't express a per-task choice (desktop single item, plain socket
          approve/deny).

        Returns the ``PendingConfirmation`` with approval status applied,
        or None if the request id is unknown (expired / already resolved).
        """
        req_id = response.get("id")

        if not req_id or req_id not in self._pending:
            logger.warning(f"Confirmation response for unknown id: {req_id}")
            return None

        pending = self._pending.pop(req_id)
        self._persist()

        # Cancel the timeout task.
        timeout_task = self._timeout_tasks.pop(req_id, None)
        if timeout_task and not timeout_task.done():
            timeout_task.cancel()

        approved_indices = response.get("approved_indices")
        if approved_indices is not None:
            # Per-task: approve the selected confirmation items, deny the rest.
            # approved_tasks already holds the tools that needed no confirmation.
            approved_set = {int(i) for i in approved_indices}
            for i, detail in enumerate(pending.confirm_details):
                task = detail.get("task")
                if i in approved_set:
                    if task is not None:
                        pending.approved_tasks.append(task)
                else:
                    tool_name = detail.get("tool_name") or (
                        f"{(task or {}).get('server', '?')}."
                        f"{(task or {}).get('tool', '?')}"
                    )
                    if tool_name not in pending.denied_tools:
                        pending.denied_tools.append(tool_name)
            logger.info(
                "Confirmation partially resolved: id=%s, approved=%d/%d",
                req_id,
                len(approved_set),
                len(pending.confirm_details),
            )
            # "partial" only when the subset really is one — an approved_indices
            # response that happens to cover everything (or nothing) reads the
            # same to a user as a plain approve/deny, so record it that way.
            if not approved_set:
                outcome = "denied"
            elif len(approved_set) >= len(pending.confirm_details):
                outcome = "approved"
            else:
                outcome = "partial"
            self._append_history(pending, outcome, sorted(approved_set))
            return pending

        approved = response.get("approved", False)
        if approved:
            # Move all tools_needing_confirmation into approved.
            # (The tasks that needed confirmation are everything in
            # pending.tasks minus what was already in approved_tasks.)
            pending.approved_tasks = list(pending.tasks)
            pending.denied_tools = []
            logger.info(f"Confirmation approved: id={req_id}")
            self._append_history(pending, "approved")
        else:
            # All tools that needed confirmation are denied.
            # approved_tasks stays as-is (tools that didn't need confirmation).
            for task in pending.tasks:
                tool_name = f"{task.get('server', '?')}.{task.get('tool', '?')}"
                if task not in pending.approved_tasks:
                    if tool_name not in pending.denied_tools:
                        pending.denied_tools.append(tool_name)
            logger.info(f"Confirmation denied: id={req_id}")
            self._append_history(pending, "denied")

        return pending

    def has_pending(self, request_id: str) -> bool:
        """Check if a confirmation request is still pending."""
        return request_id in self._pending

    # ------------------------------------------------------------------
    # Notification channels (all non-blocking)
    # ------------------------------------------------------------------

    async def _send_notification(
        self,
        request_id: str,
        tool_names: List[str],
        tool_lines: List[str],
        tools_detail: List[Dict[str, Any]],
        notification_silent: bool,
        timeout: float,
    ) -> None:
        """Fire notification on the best available channel."""

        # 1. TUI modal — highest priority when Textual is running.
        if self._tui_callback is not None:
            asyncio.create_task(self._notify_tui(request_id, tools_detail))
            return

        # 2. Desktop notification (unless silent).
        if not notification_silent and self._has_desktop_notifications():
            asyncio.create_task(self._notify_desktop(request_id, tool_lines, timeout))
            return

        # 2. Socket — if clients are connected.
        if (
            self._output_callback
            and self._has_socket_clients
            and self._has_socket_clients()
        ):
            self._notify_socket(request_id, tool_names, tools_detail, timeout)
            return

        # 3. CLI / TTY fallback.
        if self._has_tty():
            asyncio.create_task(self._notify_cli(request_id, tool_lines, timeout))
            return

        # No live channel available right now -- that's fine. The request
        # stays in _pending, reviewable anytime via list_pending() (CLI or
        # socket), rather than being auto-denied just because nobody
        # happened to be watching at this exact moment.
        logger.info(
            f"No live confirmation channel available for id={request_id}; "
            "left pending for CLI/socket review"
        )

    # -- TUI (ConfirmModal) --------------------------------------------

    async def _notify_tui(
        self, request_id: str, tools_detail: List[Dict[str, Any]]
    ) -> None:
        try:
            approved = await self._tui_callback(request_id, tools_detail)
        except Exception as e:
            logger.warning(f"TUI confirmation callback failed: {e}")
            approved = False

        if self._inject_confirmation:
            self._inject_confirmation(
                {
                    "type": "confirmation_response",
                    "id": request_id,
                    "approved": bool(approved),
                }
            )

    # -- Desktop (notify-send) -----------------------------------------

    @staticmethod
    def _has_desktop_notifications() -> bool:
        from ..platform import current as platform

        return platform.has_desktop_notifications()

    async def _notify_desktop(
        self,
        request_id: str,
        tool_lines: List[str],
        timeout: float,
    ) -> None:
        """Show a desktop notification via the platform layer.

        A single item gets one Allow/Deny notification. A batch gets one Allow/
        Deny notification per task, aggregated into a per-task decision (#187) —
        notify-send has only two actions, so per-task granularity comes from
        asking about each task in turn rather than one dialog. An explicit choice
        resolves; a dismissed/expired notification (action None) is never a deny
        (#185) — it leaves the confirmation pending on any channel.
        """
        from ..platform import current as platform

        timeout_ms = int(timeout * 1000)

        try:
            if len(tool_lines) <= 1:
                # Show the actual command, not just the tool name (#186).
                body = tool_lines[0] if tool_lines else "(no details)"
                action = await platform.send_desktop_notification(
                    title="JARVIS — Confirmation Required",
                    body=body,
                    timeout_ms=timeout_ms,
                )
                if action == "allow":
                    self._inject_result(request_id, approved=True)
                elif action == "deny":
                    self._inject_result(request_id, approved=False)
                else:
                    logger.info(
                        "Desktop confirmation dismissed/expired for id=%s; "
                        "left pending (not denied)",
                        request_id,
                    )
                return

            approved_indices: List[int] = []
            total = len(tool_lines)
            for i, line in enumerate(tool_lines):
                action = await platform.send_desktop_notification(
                    title=f"JARVIS — Confirm ({i + 1}/{total})",
                    body=line,
                    timeout_ms=timeout_ms,
                )
                if action == "allow":
                    approved_indices.append(i)
                elif action == "deny":
                    continue
                else:
                    # Dismissed/expired mid-batch: don't resolve the whole batch
                    # on a partial answer — leave it pending (#185).
                    logger.info(
                        "Desktop confirmation dismissed at item %d for id=%s; "
                        "left pending (not denied)",
                        i,
                        request_id,
                    )
                    return

            if self._inject_confirmation:
                self._inject_confirmation(
                    {
                        "type": "confirmation_response",
                        "id": request_id,
                        "approved_indices": approved_indices,
                    }
                )

        except Exception as e:
            # A failed notification means the user never saw the prompt, so
            # denying would kill legitimate work they never got to weigh in on.
            # Leave it pending for CLI/socket review instead (#185).
            logger.warning(
                "Desktop notification failed for id=%s: %s; left pending", request_id, e
            )

    def _inject_result(self, request_id: str, approved: bool) -> None:
        if self._inject_confirmation:
            self._inject_confirmation(
                {
                    "type": "confirmation_response",
                    "id": request_id,
                    "approved": approved,
                }
            )

    # -- Socket --------------------------------------------------------

    def _notify_socket(
        self,
        request_id: str,
        tool_names: List[str],
        tools_detail: List[Dict[str, Any]],
        timeout: float,
    ) -> None:
        """Send confirmation request over the output socket.  Non-blocking.
        The response will come back on the input socket as a
        ``confirmation_response`` JSON line.
        """
        message = {
            "type": "confirmation_request",
            "id": request_id,
            "tools": tool_names,
            "details": [
                {
                    "index": i,
                    "tool": t["tool_name"],
                    "params": t.get("params", {}),
                    # Rendered command so a socket GUI can show what runs (#186).
                    "command": describe_tool_call(t),
                }
                for i, t in enumerate(tools_detail)
            ],
            "timeout": timeout,
        }
        try:
            self._output_callback(message)
        except Exception as e:
            logger.warning(f"Failed to send confirmation via socket: {e}")

    # -- CLI / TTY -----------------------------------------------------

    @staticmethod
    def _has_tty() -> bool:
        return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()

    async def _notify_cli(
        self,
        request_id: str,
        tool_lines: List[str],
        timeout: float,
    ) -> None:
        """Prompt the user on stdin.  Runs in an executor so it doesn't
        block the event loop.  Injects the response when done.

        A batch is confirmed per task (#187): each command shows what it runs
        (#186) and takes its own y/N. Returns approved_indices. A non-answer
        (EOF, or an explicit-timeout expiry) leaves the confirmation pending
        rather than denying it (#185).
        """

        def _prompt() -> Optional[List[int]]:
            try:
                if len(tool_lines) <= 1:
                    line = tool_lines[0] if tool_lines else "(no details)"
                    answer = input(
                        "\n[JARVIS] Confirmation required — the following will run:\n"
                        f"    {line}\n  Approve? [y/N]: "
                    )
                    return [0] if answer.strip().lower() in ("y", "yes") else []

                print("\n[JARVIS] Confirmation required — approve each task [y/N]:")
                approved: List[int] = []
                for i, line in enumerate(tool_lines):
                    answer = input(
                        f"  [{i + 1}/{len(tool_lines)}] {line}\n      [y/N]: "
                    )
                    if answer.strip().lower() in ("y", "yes"):
                        approved.append(i)
                return approved
            except EOFError:
                return None

        loop = asyncio.get_event_loop()
        try:
            if timeout and timeout > 0:
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, _prompt), timeout=timeout
                )
            else:
                # Default: no auto-deny — wait for an actual answer (#185).
                result = await loop.run_in_executor(None, _prompt)
        except asyncio.TimeoutError:
            logger.info(
                "CLI confirmation timed out for id=%s; left pending", request_id
            )
            return

        if result is None:
            logger.info("CLI confirmation got EOF for id=%s; left pending", request_id)
            return

        if self._inject_confirmation:
            self._inject_confirmation(
                {
                    "type": "confirmation_response",
                    "id": request_id,
                    "approved_indices": result,
                }
            )

    # -- Timeout -------------------------------------------------------

    async def _auto_deny_after(self, request_id: str, timeout: float) -> None:
        """Auto-deny a pending confirmation after timeout seconds."""
        await asyncio.sleep(timeout)
        if request_id in self._pending:
            logger.info(f"Confirmation timed out, auto-denying: id={request_id}")
            if self._inject_confirmation:
                self._inject_confirmation(
                    {
                        "type": "confirmation_response",
                        "id": request_id,
                        "approved": False,
                    }
                )
