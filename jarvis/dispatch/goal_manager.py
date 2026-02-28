"""
GoalManager — tracks what the user actually wants.

Each user message can produce one or more goals. Goals persist until
fulfilled or explicitly dismissed. The dispatch signal window is ephemeral
(last 20 signals); goals are the persistent layer above it.

The LLM sees both the goal list and the signal window when it wakes up,
allowing it to match completed tasks to user intent.
"""

import time
import uuid
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from ..core.logger import get_logger

logger = get_logger(__name__)


class GoalStatus(Enum):
    PENDING = "pending"       # Parsed but not yet dispatched
    ACTIVE = "active"         # Tasks dispatched, waiting for results
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
        return ctx


class GoalManager:
    """Manages the lifecycle of user goals."""

    def __init__(self):
        self._goals: List[Goal] = []

    def add_goal(self, description: str) -> Goal:
        """Add a single goal from user input."""
        goal = Goal(description=description)
        self._goals.append(goal)
        logger.info(f"GoalManager: Added goal [{goal.id}]: {description}")
        return goal

    def add_goals(self, descriptions: List[str]) -> List[Goal]:
        """Add multiple goals at once."""
        return [self.add_goal(d) for d in descriptions]

    def link_tasks(self, goal_id: str, pids: List[int]):
        """Associate dispatch task PIDs with a goal."""
        goal = self._find_goal(goal_id)
        if goal:
            goal.task_pids.extend(pids)
            goal.status = GoalStatus.ACTIVE
            logger.info(f"GoalManager: Goal [{goal_id}] linked to PIDs {pids}")

    def complete_goal(self, goal_id: str, result: Optional[str] = None):
        """Mark a goal as completed."""
        goal = self._find_goal(goal_id)
        if goal:
            goal.status = GoalStatus.COMPLETED
            goal.result = result
            goal.completed_at = time.time()
            logger.info(f"GoalManager: Goal [{goal_id}] completed")

    def fail_goal(self, goal_id: str, reason: Optional[str] = None):
        """Mark a goal as failed."""
        goal = self._find_goal(goal_id)
        if goal:
            goal.status = GoalStatus.FAILED
            goal.result = reason
            goal.completed_at = time.time()
            logger.info(f"GoalManager: Goal [{goal_id}] failed: {reason}")

    def update_from_signal(self, signal: Dict[str, Any]):
        """
        Update goal status based on a dispatch signal.

        Args:
            signal: Dict with pid, type (INIT/EXIT/REMIND/KILL), and optional data.
        """
        pid = signal.get("pid")
        signal_type = signal.get("type", "").upper()

        # Find the goal that owns this PID
        goal = self._find_goal_by_pid(pid)
        if not goal:
            logger.debug(f"GoalManager: No goal found for PID {pid}")
            return

        if signal_type == "EXIT":
            # Check if all PIDs for this goal are done
            # (For now, mark complete on any EXIT — Jarvis/LLM decides if goal is truly done)
            logger.info(f"GoalManager: PID {pid} exited for goal [{goal.id}]")

    def get_active_goals(self) -> List[Goal]:
        """Get all non-completed, non-failed goals."""
        return [g for g in self._goals if g.status in (GoalStatus.PENDING, GoalStatus.ACTIVE)]

    def get_all_goals(self) -> List[Goal]:
        """Get all goals regardless of status."""
        return list(self._goals)

    def dismiss_completed(self) -> List[Goal]:
        """Remove and return completed goals."""
        completed = [g for g in self._goals if g.status == GoalStatus.COMPLETED]
        self._goals = [g for g in self._goals if g.status != GoalStatus.COMPLETED]
        return completed

    def get_context(self) -> List[Dict[str, Any]]:
        """Get goal state formatted for LLM context."""
        return [g.to_context() for g in self._goals if g.status != GoalStatus.COMPLETED]

    def clear(self):
        """Clear all goals."""
        self._goals.clear()

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
