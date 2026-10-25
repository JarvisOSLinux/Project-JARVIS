"""
GoalManager — tracks what the user actually wants, in a tree.

Each user message produces a root Goal. The dispatch sub-chain can create
child Goals (sub-goals) under it as planning recurses. Leaf goals hold
task_pids linked to real MCP dispatch tasks; inner goals aggregate their
children's outputs.

Key concepts:
  description — the intent, set at creation, never changes
  strategy    — forward-looking mutable state written by the LLM
                ("plan is X, currently doing Y")
  output      — backward-looking final result written when the goal
                completes ("built the backend: Postgres on :5432, …")
                This bubbles up: a parent sees each child's output.

Completed and failed goals are archived to a JSONL file so the contextor
can reference past accomplishments and failures.
"""

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..config import Config
from ..core.logger import get_logger
from .boundary import verify_boundary

logger = get_logger(__name__)

_DEFAULT_ARCHIVE_DIR = Config.JARVIS_DATA_DIR
_ARCHIVE_FILENAME = "goal_archive.jsonl"


def _signal_output_text(signal: Dict[str, Any]) -> str:
    """Extract an EXIT signal's tool output as a nonce-independent string.

    The provenance boundary is keyed by a per-task CSPRNG nonce, so two EXITs
    carrying identical content still differ byte-for-byte at the wrapper level.
    Unwrap via the recorded nonce before comparing, otherwise every EXIT would
    look like "new content" and the progress reset would defeat the guard.
    """
    data = signal.get("data")
    if isinstance(data, dict):
        text = data.get("output") or data.get("error") or ""
        return text.strip() if isinstance(text, str) else ""

    body = data
    if not isinstance(body, str):
        body = signal.get("message")
    if not isinstance(body, str):
        body = signal.get("output")
    if not isinstance(body, str):
        return ""

    result = verify_boundary(body, signal.get("nonce"))
    inner = result.inner if result.inner is not None else body
    return inner.strip() if isinstance(inner, str) else ""


# dispatch stamps the call's status ahead of the provenance wrapper: "200" when
# the tool succeeded, "500" when it failed (dmcp exits non-zero on a
# tool-reported isError as well as on an RPC failure), so the status is read
# from dispatch's own report rather than sniffed out of the tool's payload.
# Anchored on purpose: a body carrying the UNVERIFIED marker no longer matches,
# and output that failed the provenance boundary is evidence of nothing.
_EXIT_OK_RE = re.compile(r"^(?:\[hash=[0-9a-fA-F]+\]\s*)?200\b")


def _signal_succeeded(signal: Dict[str, Any]) -> bool:
    """True when an EXIT reports that the tool call itself succeeded."""
    body = signal.get("data")
    if not isinstance(body, str):
        body = signal.get("message")
    if not isinstance(body, str):
        return False
    return _EXIT_OK_RE.match(body) is not None


class GoalStatus(Enum):
    PENDING = "pending"  # Parsed but not yet dispatched
    ACTIVE = "active"  # Tasks dispatched, waiting for results
    DEFERRED = "deferred"  # Parked with a timer — will reactivate on REMIND
    COMPLETED = "completed"  # Done, output written
    FAILED = "failed"  # Tasks failed or user cancelled


@dataclass
class Goal:
    """A single goal node in the goal tree."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    description: str = ""  # the intent — set at creation, immutable
    status: GoalStatus = GoalStatus.PENDING
    strategy: str = ""  # mutable forward-looking plan written by LLM
    output: Optional[str] = None  # final result written when done, bubbles to parent
    result: Optional[str] = None  # legacy alias kept for archive compatibility
    parent_id: Optional[str] = None  # None for root goals
    child_goal_ids: List[str] = field(default_factory=list)
    task_pids: List[int] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    timer_pid: Optional[int] = None
    defer_count: int = 0
    deferred_at: Optional[float] = None
    # Dispatch repeat/progress guard (#205). recent_dispatches is a bounded
    # rolling window of dispatch fingerprints; the two dicts let an EXIT that
    # carries new content age the window out (progress), so legitimate
    # poll-until-ready flows never trip. None of these reach to_context — they
    # are guard bookkeeping, not LLM-facing state.
    recent_dispatches: List[str] = field(default_factory=list)
    dispatch_pid_fps: Dict[int, str] = field(default_factory=dict)
    dispatch_last_output: Dict[str, str] = field(default_factory=dict)
    # Fingerprints that only deliver input to a running job: their reply is a
    # receipt, so "same output again" is their success shape, not a stall. A
    # list rather than a set — Goal is archived through asdict + json.dumps.
    dispatch_input_fps: List[str] = field(default_factory=list)
    # Per-PID server attribution, also not LLM-facing. dispatch_pid_fps maps a
    # PID to the whole *batch* fingerprint, which cannot say which task within
    # that batch the PID is; this can, so a per-signal hint names only the
    # server that actually failed.
    dispatch_pid_server: Dict[int, str] = field(default_factory=dict)
    # The chat session the request was made in, so /stop acts on that
    # session's requests only (#270). Not LLM-facing.
    chat_session: Optional[str] = None

    def to_context(self) -> Dict[str, Any]:
        """Flat serialization for LLM context (no children — use get_goal_context)."""
        ctx: Dict[str, Any] = {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
        }
        if self.strategy:
            ctx["strategy"] = self.strategy
        if self.output:
            ctx["output"] = self.output
        if self.task_pids:
            ctx["task_pids"] = self.task_pids
        if self.status == GoalStatus.DEFERRED:
            ctx["defer_count"] = self.defer_count
            if self.timer_pid is not None:
                ctx["timer_pid"] = self.timer_pid
        return ctx

    def to_archive(self) -> Dict[str, Any]:
        """Full serialization for disk archive (JSONL)."""
        d = asdict(self)
        d["status"] = self.status.value
        return d


class GoalManager:
    """Manages a tree of user goals with on-disk archiving."""

    def __init__(self, archive_dir: str | None = None):
        self._goals: List[Goal] = []  # flat storage; tree structure via id links

        self._archive_dir = archive_dir or _DEFAULT_ARCHIVE_DIR
        self._archive_path = os.path.join(self._archive_dir, _ARCHIVE_FILENAME)
        os.makedirs(self._archive_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Goal creation
    # ------------------------------------------------------------------

    def add_goal(self, description: str, chat_session: Optional[str] = None) -> Goal:
        """Create a root-level goal from user input."""
        goal = Goal(description=description, chat_session=chat_session)
        self._goals.append(goal)
        logger.info(f"GoalManager: Added root goal [{goal.id}]: {description}")
        return goal

    def add_goals(self, descriptions: List[str]) -> List["Goal"]:
        return [self.add_goal(d) for d in descriptions]

    def add_subgoal(self, parent_id: str, description: str) -> Goal:
        """Create a child goal under an existing goal."""
        goal = Goal(description=description, parent_id=parent_id)
        self._goals.append(goal)
        parent = self._find_goal(parent_id)
        if parent:
            parent.child_goal_ids.append(goal.id)
            goal.chat_session = parent.chat_session
            if parent.status == GoalStatus.PENDING:
                parent.status = GoalStatus.ACTIVE
        logger.info(
            f"GoalManager: Added subgoal [{goal.id}] under [{parent_id}]: {description}"
        )
        return goal

    # ------------------------------------------------------------------
    # Lifecycle mutations
    # ------------------------------------------------------------------

    def link_tasks(self, goal_id: str, pids: List[int]):
        """Attach dispatched task PIDs to a goal and mark it active.

        Dedupes: _extract_pids_from_result re-parses INIT lines out of
        dispatch's rolling signal window, which still holds earlier tasks'
        INITs, so each call re-offers prior PIDs. Extending blindly grew
        task_pids as [2] -> [2,2,3] -> [2,2,3,2,3,4]; that list is emitted into
        every goal-scoped LLM context, so keep only PIDs not already linked.
        """
        goal = self._find_goal(goal_id)
        if goal:
            new_pids = [p for p in pids if p not in goal.task_pids]
            goal.task_pids.extend(new_pids)
            goal.status = GoalStatus.ACTIVE
            logger.info(f"GoalManager: Goal [{goal_id}] linked to PIDs {new_pids}")

    def record_dispatch(
        self, goal_id: str, fingerprint: str, delivers_input: bool = False
    ) -> int:
        """Append a dispatch fingerprint to the goal's rolling window and return
        how many times it occurs within that window (#205).

        Window-count, not consecutive-equality: a repeating cycle like
        navigate, snapshot, navigate, snapshot, navigate must still trip on
        navigate reaching the limit even though no two are adjacent.

        ``delivers_input`` marks a dispatch that only feeds a running job, so a
        later EXIT can judge it on delivery rather than on changed output (see
        _note_dispatch_progress).
        """
        goal = self._find_goal(goal_id)
        if not goal:
            return 0
        if delivers_input and fingerprint not in goal.dispatch_input_fps:
            goal.dispatch_input_fps.append(fingerprint)
        goal.recent_dispatches.append(fingerprint)
        window = getattr(Config, "DISPATCH_REPEAT_WINDOW", 12)
        if window > 0 and len(goal.recent_dispatches) > window:
            del goal.recent_dispatches[:-window]
        return goal.recent_dispatches.count(fingerprint)

    def link_dispatch_fingerprint(
        self, goal_id: str, fingerprint: str, pids: List[int]
    ):
        """Map dispatched PIDs to the fingerprint that produced them so a later
        EXIT can tell whether that dispatch made progress (#205)."""
        goal = self._find_goal(goal_id)
        if not goal:
            return
        for pid in pids:
            goal.dispatch_pid_fps[pid] = fingerprint

    def link_dispatch_servers(
        self, goal_id: str, pids: List[int], tasks: List[Dict[str, Any]]
    ):
        """Map each dispatched PID to the server of *its own* task.

        The batch fingerprint deliberately covers the whole batch (the repeat
        guard counts batches), so it cannot attribute one PID to one server.
        Without this map, anything decoding the fingerprint for a single
        failing PID names every server in the batch.

        _extract_pids_from_result re-parses INIT lines out of dispatch's
        rolling signal window, so it can re-offer PIDs from earlier dispatches;
        this batch's INITs are the most recent, so pair the trailing len(tasks)
        PIDs with tasks in dispatch order. If dispatch returned fewer PIDs than
        tasks the pairing is ambiguous — record nothing rather than
        mis-attribute a failure to a server that never ran it.
        """
        goal = self._find_goal(goal_id)
        if not goal or not tasks or len(pids) < len(tasks):
            return
        for pid, task in zip(pids[-len(tasks) :], tasks):
            server = task.get("server") if isinstance(task, dict) else None
            if isinstance(server, str) and server:
                goal.dispatch_pid_server[pid] = server

    def update_strategy(self, goal_id: str, strategy: str):
        """LLM updates its forward-looking plan for this goal."""
        goal = self._find_goal(goal_id)
        if goal:
            goal.strategy = strategy
            logger.debug(f"GoalManager: Goal [{goal_id}] strategy updated")

    def complete_goal(self, goal_id: str, output: Optional[str] = None):
        """Mark a goal done and store its final output."""
        goal = self._find_goal(goal_id)
        if goal:
            goal.status = GoalStatus.COMPLETED
            goal.output = output
            goal.result = output  # keep legacy field in sync
            goal.completed_at = time.time()
            logger.info(f"GoalManager: Goal [{goal_id}] completed")
            if goal.parent_id:
                self._log_parent_progress(goal.parent_id)

    def fail_goal(self, goal_id: str, reason: Optional[str] = None):
        """Mark a goal failed and store the reason as its output."""
        goal = self._find_goal(goal_id)
        if goal:
            goal.status = GoalStatus.FAILED
            goal.output = reason
            goal.result = reason
            goal.completed_at = time.time()
            logger.info(f"GoalManager: Goal [{goal_id}] failed: {reason}")
            if goal.parent_id:
                self._log_parent_progress(goal.parent_id)

    def defer_goal(self, goal_id: str, timer_pid: int):
        goal = self._find_goal(goal_id)
        if goal:
            goal.status = GoalStatus.DEFERRED
            goal.timer_pid = timer_pid
            goal.defer_count += 1
            goal.deferred_at = time.time()
            logger.info(
                f"GoalManager: Goal [{goal_id}] deferred "
                f"(timer PID {timer_pid}, defer #{goal.defer_count})"
            )

    def reactivate_goal(self, goal_id: str):
        goal = self._find_goal(goal_id)
        if goal and goal.status == GoalStatus.DEFERRED:
            goal.status = GoalStatus.PENDING
            goal.timer_pid = None
            goal.deferred_at = None
            logger.info(f"GoalManager: Goal [{goal_id}] reactivated from deferral")

    # ------------------------------------------------------------------
    # Context queries
    # ------------------------------------------------------------------

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """Public accessor for a goal by ID."""
        return self._find_goal(goal_id)

    def get_goal_context(self, goal_id: str) -> Optional[Dict[str, Any]]:
        """
        Return a scoped context slice for one goal: its own fields plus
        a summary of each immediate child (description, status, output).

        Used when a signal arrives for a task owned by this goal — the LLM
        gets exactly the context it needs, not the entire goal forest.
        """
        goal = self._find_goal(goal_id)
        if not goal:
            return None
        ctx = goal.to_context()
        if goal.child_goal_ids:
            ctx["children"] = [self._child_summary(cid) for cid in goal.child_goal_ids]
        return ctx

    def get_context(self) -> List[Dict[str, Any]]:
        """
        Return context for all active *root* goals.

        Used for explicit user queries like "what are you working on?".
        Subgoals are intentionally omitted here — they appear inside their
        parent's get_goal_context() slice.
        """
        active_roots = [
            g
            for g in self._goals
            if g.parent_id is None and g.status != GoalStatus.COMPLETED
        ]
        limit = getattr(Config, "MAX_GOALS_IN_CONTEXT", 20)
        roots = active_roots[-limit:]
        return [g.to_context() for g in roots]

    def get_active_goals(self) -> List[Goal]:
        return [
            g
            for g in self._goals
            if g.status in (GoalStatus.PENDING, GoalStatus.ACTIVE, GoalStatus.DEFERRED)
        ]

    def get_root_goals(self) -> List[Goal]:
        """Return only top-level goals (no parent)."""
        return [g for g in self._goals if g.parent_id is None]

    def get_all_goals(self) -> List[Goal]:
        return list(self._goals)

    def status(self) -> List[Goal]:
        return list(self._goals)

    # ------------------------------------------------------------------
    # Signal-driven updates
    # ------------------------------------------------------------------

    def find_goal_by_timer_pid(self, pid: int) -> Optional[Goal]:
        for goal in self._goals:
            if goal.timer_pid == pid:
                return goal
        return None

    def find_goal_by_task_pid(self, pid: int) -> Optional[Goal]:
        """Find whichever goal (at any tree depth) owns this task PID."""
        for goal in self._goals:
            if pid in goal.task_pids:
                return goal
        return None

    def update_from_signal(self, signal: Dict[str, Any]):
        logger.info(
            f"GoalManager: Processing signal type={signal.get('type')}, "
            f"pid={signal.get('pid')}, data={signal.get('data', '')}"
        )
        pid = signal.get("pid")
        signal_type = signal.get("type", "").upper()

        if signal_type == "REMIND":
            metadata = signal.get("metadata", {})
            goal_id = metadata.get("goal_id") if isinstance(metadata, dict) else None
            if goal_id:
                goal = self._find_goal(goal_id)
                if goal and goal.status == GoalStatus.DEFERRED:
                    self.reactivate_goal(goal_id)
                    return
            timer_goal = self.find_goal_by_timer_pid(pid)
            if timer_goal and timer_goal.status == GoalStatus.DEFERRED:
                self.reactivate_goal(timer_goal.id)
                return

        goal = self.find_goal_by_task_pid(pid)
        if not goal:
            logger.debug(f"GoalManager: No goal found for PID {pid}")
            return

        if signal_type == "EXIT":
            logger.info(f"GoalManager: PID {pid} exited for goal [{goal.id}]")
            self._note_dispatch_progress(goal, signal)

    def _note_dispatch_progress(self, goal: "Goal", signal: Dict[str, Any]):
        """Reset the repeat window when an EXIT shows this dispatch made
        forward progress (#205).

        Progress = the tool returned non-empty output that differs from the
        previously recorded output of the same fingerprint. Every EXIT's output
        is recorded as the new baseline — including an empty one — so the FIRST
        poll returning empty (the normal case: you poll precisely because the
        job/page isn't ready yet) still seeds a baseline that the next non-empty
        poll can differ from. With DISPATCH_REPEAT_LIMIT-1 EXITs preceding a
        trip, dropping empty EXITs on the floor left a poll-until-ready whose
        first result was empty (or a repeat) tripping while content was actually
        changing. A stuck loop (empty or identical output every cycle) still
        leaves the window intact and trips on the count.

        A dispatch that only delivers input to a running job is judged on
        delivery instead, because changed output is evidence it structurally
        cannot produce: its reply is a receipt for bytes whose effect appears in
        the job's own output, and the answers a wizard wants are routinely
        identical ("y\\n" to every "[Y/n]"). A delivery dispatch's EXIT is
        therefore progress when it succeeded — the running job consumed the
        answer — and nothing when it failed, so input aimed at a job that is
        gone still trips the guard at the limit.
        """
        pid = signal.get("pid")
        fingerprint = goal.dispatch_pid_fps.get(pid)
        if fingerprint is None:
            return
        output = _signal_output_text(signal)
        prior = goal.dispatch_last_output.get(fingerprint)
        goal.dispatch_last_output[fingerprint] = output
        delivered = fingerprint in goal.dispatch_input_fps and _signal_succeeded(signal)
        if delivered or (output and prior is not None and output != prior):
            goal.recent_dispatches.clear()
            logger.debug(
                f"GoalManager: Goal [{goal.id}] dispatch progressed; "
                "repeat window reset"
            )

    # ------------------------------------------------------------------
    # Archiving
    # ------------------------------------------------------------------

    def dismiss_completed(self) -> List[Goal]:
        completed = [g for g in self._goals if g.status == GoalStatus.COMPLETED]
        self._goals = [g for g in self._goals if g.status != GoalStatus.COMPLETED]
        if completed:
            logger.info(
                f"GoalManager: Dismissing {len(completed)} completed goal(s): "
                f"{[g.id for g in completed]}"
            )
            self._archive_goals(completed)
        return completed

    def dismiss_failed(self) -> List[Goal]:
        failed = [g for g in self._goals if g.status == GoalStatus.FAILED]
        self._goals = [g for g in self._goals if g.status != GoalStatus.FAILED]
        if failed:
            logger.info(
                f"GoalManager: Dismissing {len(failed)} failed goal(s): "
                f"{[g.id for g in failed]}"
            )
            self._archive_goals(failed)
        return failed

    def clear(self):
        self._goals.clear()

    def archive_all(self) -> List[Goal]:
        """Archive every goal currently in memory, regardless of status, and
        clear in-memory state. Unlike dismiss_completed/dismiss_failed (which
        only ever touch terminal goals), this also covers PENDING/ACTIVE/
        DEFERRED goals -- used on daemon shutdown (#146) so in-flight work
        isn't silently lost with zero on-disk trace.
        """
        goals = list(self._goals)
        if goals:
            self._archive_goals(goals)
        self._goals.clear()
        return goals

    def _archive_goals(self, goals: List[Goal]):
        try:
            with open(self._archive_path, "a", encoding="utf-8") as f:
                for goal in goals:
                    f.write(json.dumps(goal.to_archive()) + "\n")
            logger.debug(
                f"GoalManager: Archived {len(goals)} goal(s) to {self._archive_path}"
            )
        except OSError as e:
            logger.warning(f"GoalManager: Failed to archive goals: {e}")

    def load_archive(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not os.path.exists(self._archive_path):
            return []
        entries: List[Dict[str, Any]] = []
        try:
            with open(self._archive_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError as e:
            logger.warning(f"GoalManager: Failed to read archive: {e}")
            return []
        return entries[-limit:]

    def search_archive(
        self, keywords: List[str], limit: int = 20
    ) -> List[Dict[str, Any]]:
        all_entries = self.load_archive(limit=500)
        keywords_lower = [k.lower() for k in keywords]
        matches = []
        for entry in all_entries:
            text = (
                entry.get("description", "")
                + " "
                + (entry.get("output") or entry.get("result") or "")
            ).lower()
            if any(kw in text for kw in keywords_lower):
                matches.append(entry)
                if len(matches) >= limit:
                    break
        return matches

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_goal(self, goal_id: str) -> Optional[Goal]:
        for goal in self._goals:
            if goal.id == goal_id:
                return goal
        return None

    def _find_goal_by_pid(self, pid: int) -> Optional[Goal]:
        """Legacy alias — use find_goal_by_task_pid from outside."""
        return self.find_goal_by_task_pid(pid)

    def _child_summary(self, goal_id: str) -> Dict[str, Any]:
        """Compact summary of a child goal for parent context."""
        goal = self._find_goal(goal_id)
        if not goal:
            return {"id": goal_id, "status": "unknown"}
        summary: Dict[str, Any] = {
            "id": goal.id,
            "description": goal.description,
            "status": goal.status.value,
        }
        if goal.output:
            summary["output"] = goal.output
        if goal.strategy:
            summary["strategy"] = goal.strategy
        return summary

    def _log_parent_progress(self, parent_id: str):
        """Log when all children of a goal have resolved."""
        parent = self._find_goal(parent_id)
        if not parent:
            return
        children = [self._find_goal(cid) for cid in parent.child_goal_ids]
        all_resolved = all(
            c and c.status in (GoalStatus.COMPLETED, GoalStatus.FAILED)
            for c in children
        )
        if all_resolved and children:
            outputs = [
                f"{c.description}: {c.output or '(no output)'}" for c in children if c
            ]
            logger.info(
                f"GoalManager: All {len(children)} child goal(s) of [{parent_id}] resolved. "
                f"Outputs: {outputs}"
            )
