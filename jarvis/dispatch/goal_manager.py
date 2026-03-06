"""
GoalManager — tracks what the user actually wants.

Each user message can produce one or more goals. Goals persist until
fulfilled or explicitly dismissed. The dispatch signal window is ephemeral
(last 20 signals); goals are the persistent layer above it.

Completed and failed goals are archived to a JSONL file so the contextor
can reference past accomplishments and failures.
"""

import json
import os
import time
import uuid
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from ..core.logger import get_logger
from ..config import Config

logger = get_logger(__name__)

_DEFAULT_ARCHIVE_DIR = Config.JARVIS_DATA_DIR
_ARCHIVE_FILENAME = "goal_archive.jsonl"


class GoalStatus(Enum):
    PENDING = "pending"       # Parsed but not yet dispatched
    ACTIVE = "active"         # Tasks dispatched, waiting for results
    DEFERRED = "deferred"     # Parked with a timer — will reactivate on REMIND
    COMPLETED = "completed"   # All tasks done, goal fulfilled
    FAILED = "failed"         # Tasks failed or user cancelled


@dataclass
class Goal:
    """A single user goal with lifecycle tracking."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    description: str = ""
    status: GoalStatus = GoalStatus.PENDING
    task_pids: List[int] = field(default_factory=list)
    result: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    timer_pid: Optional[int] = None
    defer_count: int = 0
    deferred_at: Optional[float] = None

    def to_context(self) -> Dict[str, Any]:
        """Serialize for LLM context."""
        ctx = {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
        }
        if self.task_pids:
            ctx["task_pids"] = self.task_pids
        if self.result:
            ctx["result"] = self.result
        if self.status == GoalStatus.DEFERRED:
            ctx["defer_count"] = self.defer_count
            if self.timer_pid is not None:
                ctx["timer_pid"] = self.timer_pid
        return ctx

    def to_archive(self) -> Dict[str, Any]:
        """Serialize for disk archive (JSONL)."""
        d = asdict(self)
        d["status"] = self.status.value
        return d


class GoalManager:
    """Manages the lifecycle of user goals with on-disk archiving."""

    def __init__(self, archive_dir: str | None = None):
        self._goals: List[Goal] = []

        self._archive_dir = archive_dir or _DEFAULT_ARCHIVE_DIR
        self._archive_path = os.path.join(self._archive_dir, _ARCHIVE_FILENAME)

        os.makedirs(self._archive_dir, exist_ok=True)

    def add_goal(self, description: str) -> Goal:
        goal = Goal(description=description)
        self._goals.append(goal)
        logger.info(f"GoalManager: Added goal [{goal.id}]: {description}")
        return goal

    def add_goals(self, descriptions: List[str]) -> List[Goal]:
        return [self.add_goal(d) for d in descriptions]

    def link_tasks(self, goal_id: str, pids: List[int]):
        goal = self._find_goal(goal_id)
        if goal:
            goal.task_pids.extend(pids)
            goal.status = GoalStatus.ACTIVE
            logger.info(f"GoalManager: Goal [{goal_id}] linked to PIDs {pids}")

    def complete_goal(self, goal_id: str, result: Optional[str] = None):
        goal = self._find_goal(goal_id)
        if goal:
            goal.status = GoalStatus.COMPLETED
            goal.result = result
            goal.completed_at = time.time()
            logger.info(f"GoalManager: Goal [{goal_id}] completed")

    def fail_goal(self, goal_id: str, reason: Optional[str] = None):
        goal = self._find_goal(goal_id)
        if goal:
            goal.status = GoalStatus.FAILED
            goal.result = reason
            goal.completed_at = time.time()
            logger.info(f"GoalManager: Goal [{goal_id}] failed: {reason}")

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

    def find_goal_by_timer_pid(self, pid: int) -> Optional['Goal']:
        for goal in self._goals:
            if goal.timer_pid == pid:
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

        goal = self._find_goal_by_pid(pid)
        if not goal:
            logger.debug(f"GoalManager: No goal found for PID {pid}")
            return

        if signal_type == "EXIT":
            logger.info(f"GoalManager: PID {pid} exited for goal [{goal.id}]")

    def get_active_goals(self) -> List[Goal]:
        return [g for g in self._goals if g.status in (
            GoalStatus.PENDING, GoalStatus.ACTIVE, GoalStatus.DEFERRED
        )]

    def get_all_goals(self) -> List[Goal]:
        return list(self._goals)

    def dismiss_completed(self) -> List[Goal]:
        """Remove completed goals from active list and archive them to disk."""
        completed = [g for g in self._goals if g.status == GoalStatus.COMPLETED]
        self._goals = [g for g in self._goals if g.status != GoalStatus.COMPLETED]
        if completed:
            logger.info(f"GoalManager: Dismissing {len(completed)} completed goal(s): {[g.id for g in completed]}")
            self._archive_goals(completed)
        return completed

    def dismiss_failed(self) -> List[Goal]:
        """Remove failed goals from active list and archive them to disk."""
        failed = [g for g in self._goals if g.status == GoalStatus.FAILED]
        self._goals = [g for g in self._goals if g.status != GoalStatus.FAILED]
        if failed:
            logger.info(f"GoalManager: Dismissing {len(failed)} failed goal(s): {[g.id for g in failed]}")
            self._archive_goals(failed)
        return failed

    def get_context(self) -> List[Dict[str, Any]]:
        active = [g.to_context() for g in self._goals if g.status != GoalStatus.COMPLETED]
        limit = getattr(Config, "MAX_GOALS_IN_CONTEXT", 20)
        return active[-limit:] if len(active) > limit else active

    def clear(self):
        self._goals.clear()

    # ------------------------------------------------------------------
    # Archive
    # ------------------------------------------------------------------

    def _archive_goals(self, goals: List[Goal]):
        """Append goals to the JSONL archive file."""
        try:
            with open(self._archive_path, "a", encoding="utf-8") as f:
                for goal in goals:
                    f.write(json.dumps(goal.to_archive()) + "\n")
            logger.debug(f"GoalManager: Archived {len(goals)} goal(s) to {self._archive_path}")
        except OSError as e:
            logger.warning(f"GoalManager: Failed to archive goals: {e}")

    def load_archive(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Load recent archived goals from disk.
        Returns the last ``limit`` entries (newest last).
        """
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

    def search_archive(self, keywords: List[str], limit: int = 20) -> List[Dict[str, Any]]:
        """
        Search archived goals by keyword match on description and result.
        Returns up to ``limit`` matching entries.
        """
        all_entries = self.load_archive(limit=500)
        keywords_lower = [k.lower() for k in keywords]
        matches = []
        for entry in all_entries:
            text = (entry.get("description", "") + " " + (entry.get("result") or "")).lower()
            if any(kw in text for kw in keywords_lower):
                matches.append(entry)
                if len(matches) >= limit:
                    break
        return matches

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _find_goal(self, goal_id: str) -> Optional[Goal]:
        for goal in self._goals:
            if goal.id == goal_id:
                return goal
        return None

    def _find_goal_by_pid(self, pid: int) -> Optional[Goal]:
        for goal in self._goals:
            if pid in goal.task_pids:
                return goal
        return None
