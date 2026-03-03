"""
Task parser for JARVIS dispatch system.

Validates and parses LLM responses into structured dispatch actions.
The LLM responds with JSON describing what action to take:
dispatch (send tasks), respond (talk to user), wait, or kill.

This parser is intentionally simple — it validates structure, not semantics.
Jarvis decides what to do with the parsed result.
"""

from typing import Dict, Any, Optional, List
from .logger import get_logger

logger = get_logger(__name__)

# Valid LLM action types
VALID_ACTIONS = {"dispatch", "respond", "wait", "kill", "defer"}


class TaskParser:
    """Parses and validates LLM responses in dispatch format."""

    @staticmethod
    def parse(response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse an LLM response into a structured action.

        Expected formats:

            {"action": "dispatch", "tasks": [...], "goal_updates": [...]}
            {"action": "respond", "output": "...", "goal_updates": [...]}
            {"action": "wait", "goal_updates": [...]}
            {"action": "kill", "pids": [...], "goal_updates": [...]}

        Args:
            response: Parsed JSON dict from LLM.

        Returns:
            Validated action dict, or error dict if invalid.
        """
        action = response.get("action")

        if action not in VALID_ACTIONS:
            logger.warning(f"TaskParser: Unknown action '{action}'")
            return {"error": f"Unknown action: {action}", "raw": response}

        if action == "dispatch":
            result = TaskParser._parse_dispatch(response)
        elif action == "respond":
            result = TaskParser._parse_respond(response)
        elif action == "wait":
            result = TaskParser._parse_wait(response)
        elif action == "kill":
            result = TaskParser._parse_kill(response)
        elif action == "defer":
            result = TaskParser._parse_defer(response)

        if "error" in result:
            logger.warning(f"TaskParser: Validation failed for action='{action}': {result['error']}")
        else:
            summary = TaskParser._summarize(result)
            logger.info(f"TaskParser: Parsed action='{action}'{summary}")
        return result

    @staticmethod
    def _summarize(result: Dict[str, Any]) -> str:
        """One-line summary of a parsed result for log readability."""
        action = result.get("action", "")
        if action == "dispatch":
            tasks = result.get("tasks", [])
            parts = [f"{t.get('server')}/{t.get('tool')}" for t in tasks]
            return f", tasks=[{', '.join(parts)}]"
        if action == "respond":
            output = result.get("output", "")
            preview = (output[:80] + "...") if len(output) > 80 else output
            return f", output='{preview}'"
        if action == "kill":
            return f", pids={result.get('pids', [])}"
        if action == "defer":
            return f", goal_id={result.get('goal_id')}, duration={result.get('duration')}s"
        return ""

    @staticmethod
    def _parse_dispatch(response: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a dispatch action."""
        tasks = response.get("tasks", [])
        if not isinstance(tasks, list) or not tasks:
            return {"error": "Dispatch action requires a non-empty 'tasks' list", "raw": response}

        validated_tasks = []
        for i, task in enumerate(tasks):
            if not isinstance(task, dict):
                logger.warning(f"TaskParser: Task {i} is not a dict, skipping")
                continue

            server = task.get("server")
            tool = task.get("tool")
            if not server or not tool:
                logger.warning(f"TaskParser: Task {i} missing server or tool, skipping")
                continue

            validated_tasks.append({
                "server": server,
                "tool": tool,
                "params": task.get("params", {}),
                "remind_after": task.get("remind_after"),
            })

        if not validated_tasks:
            return {"error": "No valid tasks in dispatch action", "raw": response}

        return {
            "action": "dispatch",
            "tasks": validated_tasks,
            "goal_updates": response.get("goal_updates", []),
        }

    @staticmethod
    def _parse_respond(response: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a respond action."""
        output = response.get("output", "")
        return {
            "action": "respond",
            "output": str(output),
            "goal_updates": response.get("goal_updates", []),
        }

    @staticmethod
    def _parse_wait(response: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a wait action."""
        return {
            "action": "wait",
            "goal_updates": response.get("goal_updates", []),
        }

    @staticmethod
    def _parse_kill(response: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a kill action."""
        pids = response.get("pids", [])
        if not isinstance(pids, list) or not pids:
            return {"error": "Kill action requires a non-empty 'pids' list", "raw": response}

        return {
            "action": "kill",
            "pids": [int(p) for p in pids],
            "goal_updates": response.get("goal_updates", []),
        }

    @staticmethod
    def _parse_defer(response: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a defer action."""
        goal_id = response.get("goal_id")
        duration = response.get("duration")

        if not goal_id:
            return {"error": "Defer action requires 'goal_id'", "raw": response}
        if not duration or not isinstance(duration, (int, float)) or duration <= 0:
            return {"error": "Defer action requires a positive 'duration' (seconds)", "raw": response}

        return {
            "action": "defer",
            "goal_id": str(goal_id),
            "duration": int(duration),
            "reason": response.get("reason", ""),
            "goal_updates": response.get("goal_updates", []),
        }
