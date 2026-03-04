"""
Task parser for JARVIS dispatch system.

Validates and parses LLM responses into structured dispatch actions.
The LLM responds with JSON describing what action to take:
respond, search, list_tools, install, dispatch, wait, kill, or defer.

This parser is intentionally simple — it validates structure, not semantics.
Jarvis decides what to do with the parsed result.
"""

from typing import Dict, Any
from .logger import get_logger

logger = get_logger(__name__)

VALID_ACTIONS = {
    "dispatch", "respond", "wait", "kill", "defer",
    "search", "list_tools", "install",
}

_PARSERS = {}


def _parser(action_name):
    """Register a static parse method for an action type."""
    def decorator(fn):
        _PARSERS[action_name] = fn
        return fn
    return decorator


class TaskParser:
    """Parses and validates LLM responses in dispatch format."""

    @staticmethod
    def parse(response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse an LLM response into a structured action.

        Args:
            response: Parsed JSON dict from LLM.

        Returns:
            Validated action dict, or error dict if invalid.
        """
        action = response.get("action")

        if action not in VALID_ACTIONS:
            logger.warning(f"TaskParser: Unknown action '{action}'")
            return {"error": f"Unknown action: {action}", "raw": response}

        parser_fn = _PARSERS.get(action)
        if not parser_fn:
            logger.warning(f"TaskParser: No parser registered for action '{action}'")
            return {"error": f"No parser for action: {action}", "raw": response}

        result = parser_fn(response)

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
        if action == "search":
            return f", keywords={result.get('keywords', [])}"
        if action == "list_tools":
            return f", server_id={result.get('server_id')}"
        if action == "install":
            return f", server_id={result.get('server_id')}"
        return ""


# ------------------------------------------------------------------
# Action parsers
# ------------------------------------------------------------------

@_parser("dispatch")
def _parse_dispatch(response: Dict[str, Any]) -> Dict[str, Any]:
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


@_parser("respond")
def _parse_respond(response: Dict[str, Any]) -> Dict[str, Any]:
    output = response.get("output", "")
    return {
        "action": "respond",
        "output": str(output),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("wait")
def _parse_wait(response: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action": "wait",
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("kill")
def _parse_kill(response: Dict[str, Any]) -> Dict[str, Any]:
    pids = response.get("pids", [])
    if not isinstance(pids, list) or not pids:
        return {"error": "Kill action requires a non-empty 'pids' list", "raw": response}

    return {
        "action": "kill",
        "pids": [int(p) for p in pids],
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("defer")
def _parse_defer(response: Dict[str, Any]) -> Dict[str, Any]:
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


# ------------------------------------------------------------------
# Discovery actions (search → list_tools → install → dispatch)
# ------------------------------------------------------------------

@_parser("search")
def _parse_search(response: Dict[str, Any]) -> Dict[str, Any]:
    keywords = response.get("keywords", [])
    if not isinstance(keywords, list) or not keywords:
        return {"error": "Search action requires a non-empty 'keywords' list", "raw": response}

    return {
        "action": "search",
        "keywords": [str(k) for k in keywords],
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("list_tools")
def _parse_list_tools(response: Dict[str, Any]) -> Dict[str, Any]:
    server_id = response.get("server_id")
    if not server_id:
        return {"error": "list_tools action requires 'server_id'", "raw": response}

    return {
        "action": "list_tools",
        "server_id": str(server_id),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("install")
def _parse_install(response: Dict[str, Any]) -> Dict[str, Any]:
    server_id = response.get("server_id")
    if not server_id:
        return {"error": "Install action requires 'server_id'", "raw": response}

    return {
        "action": "install",
        "server_id": str(server_id),
        "goal_updates": response.get("goal_updates", []),
    }
