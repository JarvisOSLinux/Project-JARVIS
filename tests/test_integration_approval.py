"""
Goal management integration tests for JARVIS AI Assistant.

Tests the GoalManager lifecycle: adding goals, linking tasks, completing,
failing, deferring, reactivating, and signal-driven updates.
"""

import pytest
from jarvis.dispatch.goal_manager import GoalManager, Goal, GoalStatus
from tests.integration_utils import make_exit_signal, make_remind_signal


@pytest.mark.integration
class TestGoalCreation:
    """Test goal creation and initial state."""

    def test_add_single_goal(self):
        gm = GoalManager()
        goal = gm.add_goal("Install numpy")
        assert goal.description == "Install numpy"
        assert goal.status == GoalStatus.PENDING
        assert len(goal.id) > 0

    def test_add_multiple_goals(self):
        gm = GoalManager()
        goals = gm.add_goals(["Task A", "Task B", "Task C"])
        assert len(goals) == 3
        assert goals[0].description == "Task A"
        assert goals[2].description == "Task C"

    def test_goal_ids_are_unique(self):
        gm = GoalManager()
        g1 = gm.add_goal("A")
        g2 = gm.add_goal("B")
        assert g1.id != g2.id


@pytest.mark.integration
class TestGoalLifecycle:
    """Test goal lifecycle transitions."""

    def test_link_tasks_activates_goal(self):
        gm = GoalManager()
        goal = gm.add_goal("Run tests")
        gm.link_tasks(goal.id, [10, 20])

        assert goal.status == GoalStatus.ACTIVE
        assert goal.task_pids == [10, 20]

    def test_complete_goal(self):
        gm = GoalManager()
        goal = gm.add_goal("Build project")
        gm.complete_goal(goal.id, "Build succeeded")

        assert goal.status == GoalStatus.COMPLETED
        assert goal.result == "Build succeeded"
        assert goal.completed_at is not None

    def test_fail_goal(self):
        gm = GoalManager()
        goal = gm.add_goal("Deploy")
        gm.fail_goal(goal.id, "Network error")

        assert goal.status == GoalStatus.FAILED
        assert goal.result == "Network error"
        assert goal.completed_at is not None

    def test_defer_goal(self):
        gm = GoalManager()
        goal = gm.add_goal("Check later")
        gm.defer_goal(goal.id, timer_pid=42)

        assert goal.status == GoalStatus.DEFERRED
        assert goal.timer_pid == 42
        assert goal.defer_count == 1
        assert goal.deferred_at is not None

    def test_defer_goal_increments_count(self):
        gm = GoalManager()
        goal = gm.add_goal("Recurring check")
        gm.defer_goal(goal.id, 1)
        gm.reactivate_goal(goal.id)
        gm.defer_goal(goal.id, 2)

        assert goal.defer_count == 2

    def test_reactivate_deferred_goal(self):
        gm = GoalManager()
        goal = gm.add_goal("Deferred task")
        gm.defer_goal(goal.id, 50)
        gm.reactivate_goal(goal.id)

        assert goal.status == GoalStatus.PENDING
        assert goal.timer_pid is None

    def test_reactivate_non_deferred_is_noop(self):
        gm = GoalManager()
        goal = gm.add_goal("Active task")
        gm.link_tasks(goal.id, [1])
        gm.reactivate_goal(goal.id)

        # Should stay ACTIVE, not revert to PENDING
        assert goal.status == GoalStatus.ACTIVE


@pytest.mark.integration
class TestGoalQuerying:
    """Test goal querying and context generation."""

    def test_get_active_goals(self):
        gm = GoalManager()
        g1 = gm.add_goal("Pending")
        g2 = gm.add_goal("Active")
        gm.link_tasks(g2.id, [1])
        g3 = gm.add_goal("Completed")
        gm.complete_goal(g3.id)
        g4 = gm.add_goal("Deferred")
        gm.defer_goal(g4.id, 99)

        active = gm.get_active_goals()
        active_ids = {g.id for g in active}

        assert g1.id in active_ids  # PENDING
        assert g2.id in active_ids  # ACTIVE
        assert g3.id not in active_ids  # COMPLETED
        assert g4.id in active_ids  # DEFERRED counts as active

    def test_dismiss_completed(self):
        gm = GoalManager()
        g1 = gm.add_goal("Done")
        gm.complete_goal(g1.id)
        g2 = gm.add_goal("Not done")

        dismissed = gm.dismiss_completed()
        assert len(dismissed) == 1
        assert dismissed[0].id == g1.id
        assert len(gm.get_all_goals()) == 1

    def test_get_context_excludes_completed(self):
        gm = GoalManager()
        g1 = gm.add_goal("Task A")
        g2 = gm.add_goal("Task B")
        gm.complete_goal(g2.id)

        ctx = gm.get_context()
        assert len(ctx) == 1
        assert ctx[0]["id"] == g1.id

    def test_goal_to_context_includes_deferred_info(self):
        gm = GoalManager()
        goal = gm.add_goal("Check later")
        gm.defer_goal(goal.id, timer_pid=42)

        ctx = goal.to_context()
        assert ctx["status"] == "deferred"
        assert ctx["defer_count"] == 1
        assert ctx["timer_pid"] == 42

    def test_clear_goals(self):
        gm = GoalManager()
        gm.add_goal("A")
        gm.add_goal("B")
        gm.clear()
        assert gm.get_all_goals() == []


@pytest.mark.integration
class TestSignalDrivenUpdates:
    """Test GoalManager updates driven by dispatch signals."""

    def test_exit_signal_for_linked_task(self):
        gm = GoalManager()
        goal = gm.add_goal("Run command")
        gm.link_tasks(goal.id, [10])

        signal = make_exit_signal(10, output="done")
        gm.update_from_signal(signal)

        # Goal should still be ACTIVE (LLM decides when to mark complete)
        assert goal.status == GoalStatus.ACTIVE

    def test_remind_signal_reactivates_deferred_goal(self):
        gm = GoalManager()
        goal = gm.add_goal("Check later")
        gm.defer_goal(goal.id, timer_pid=50)

        signal = make_remind_signal(50, goal_id=goal.id)
        gm.update_from_signal(signal)

        assert goal.status == GoalStatus.PENDING

    def test_remind_signal_by_timer_pid(self):
        gm = GoalManager()
        goal = gm.add_goal("Timer task")
        gm.defer_goal(goal.id, timer_pid=77)

        signal = make_remind_signal(77)
        gm.update_from_signal(signal)

        assert goal.status == GoalStatus.PENDING

    def test_signal_for_unknown_pid(self):
        gm = GoalManager()
        gm.add_goal("Something")

        signal = make_exit_signal(999, output="unknown")
        gm.update_from_signal(signal)  # Should not crash

    def test_find_goal_by_timer_pid(self):
        gm = GoalManager()
        goal = gm.add_goal("Timed")
        gm.defer_goal(goal.id, timer_pid=88)

        found = gm.find_goal_by_timer_pid(88)
        assert found is not None
        assert found.id == goal.id

    def test_find_goal_by_timer_pid_not_found(self):
        gm = GoalManager()
        found = gm.find_goal_by_timer_pid(9999)
        assert found is None
