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
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_ARCHIVE_DIR = Config.JARVIS_DATA_DIR
_ARCHIVE_FILENAME = "goal_archive.jsonl"


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

    def add_goal(self, description: str) -> Goal:
        """Create a root-level goal from user input."""
        goal = Goal(description=description)
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
        """Attach dispatched task PIDs to a goal and mark it active."""
        goal = self._find_goal(goal_id)
        if goal:
            goal.task_pids.extend(pids)
            goal.status = GoalStatus.ACTIVE
            logger.info(f"GoalManager: Goal [{goal_id}] linked to PIDs {pids}")

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
