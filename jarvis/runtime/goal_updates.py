"""Apply structured goal updates from parsed LLM responses."""

from __future__ import annotations

from typing import Any


def apply_goal_updates(app: Any, updates: Any) -> None:
    for update in updates:
        goal_id = update.get("id")
        status = update.get("status")
        if not goal_id or not status:
            continue

        if status == "completed":
            app.goals.complete_goal(goal_id, update.get("result"))
        elif status == "failed":
            app.goals.fail_goal(goal_id, update.get("result"))
        elif status == "active":
            app.goals.link_tasks(goal_id, update.get("pids", []))
