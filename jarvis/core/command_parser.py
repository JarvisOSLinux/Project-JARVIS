"""
Task parser for JARVIS unified prompt system.

Validates and parses LLM responses into structured actions.

All actions are now ROOT-level — the unified prompt handles tool discovery,
installation, and execution directly without a separate dispatch sub-chain.

Actions:
  respond, store, recall, search_memory, list_memory, rename_session
  find_tools, list_tools, install, dispatch, wait, kill, defer
  plan, search, done  (legacy dispatch-mode, kept for fallback)
"""

from typing import Any, Dict

from .logger import get_logger

logger = get_logger(__name__)

VALID_ACTIONS = {
    # Root — core
    "respond",
    "dispatch",
    # Root — memory
    "store",
    "recall",
    "search_memory",
    "list_memory",
    # Root — session
    "rename_session",
    # Root — unified tool actions
    "find_tools",
    "list_tools",
    "install",
    "wait",
    "kill",
    "defer",
    # Legacy dispatch subsystem (kept for fallback / sub-chain rollback)
    "plan",
    "search",
    "done",
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
        action = response.get("action")

        if action not in VALID_ACTIONS:
            logger.warning(f"TaskParser: Unknown action '{action}'")
            logger.debug(f"TaskParser: Raw response for unknown action: {response}")
            return {"error": f"Unknown action: {action}", "raw": response}

        parser_fn = _PARSERS.get(action)
        if not parser_fn:
            logger.warning(f"TaskParser: No parser registered for action '{action}'")
            logger.debug(f"TaskParser: Raw response without parser: {response}")
            return {"error": f"No parser for action: {action}", "raw": response}

        result = parser_fn(response)

        if "error" in result:
            logger.warning(
                f"TaskParser: Validation failed for action='{action}': {result['error']}"
            )
            logger.debug(f"TaskParser: Validation raw payload: {response}")
        else:
            summary = TaskParser._summarize(result)
            logger.info(f"TaskParser: Parsed action='{action}'{summary}")
        return result

    @staticmethod
    def _summarize(result: Dict[str, Any]) -> str:
        action = result.get("action", "")
        if action == "dispatch":
            tasks = result.get("tasks")
            if tasks:
                parts = [f"{t.get('server')}/{t.get('tool')}" for t in tasks]
                return f", tasks=[{', '.join(parts)}]"
            intent = result.get("intent", "")
            preview = (intent[:80] + "...") if len(intent) > 80 else intent
            return f", intent='{preview}'"
        if action == "find_tools":
            intent = result.get("intent", "")
            preview = (intent[:80] + "...") if len(intent) > 80 else intent
            return f", intent='{preview}'"
        if action == "respond":
            output = result.get("output", "")
            preview = (output[:80] + "...") if len(output) > 80 else output
            return f", output='{preview}'"
        if action == "done":
            summary = result.get("summary", "")
            preview = (summary[:80] + "...") if len(summary) > 80 else summary
            return f", summary='{preview}'"
        if action == "kill":
            return f", pids={result.get('pids', [])}"
        if action == "defer":
            return (
                f", goal_id={result.get('goal_id')}, duration={result.get('duration')}s"
            )
        if action == "plan":
            tasks = result.get("tasks", [])
            intents = [t.get("intent", "")[:40] for t in tasks]
            return f", tasks={intents}"
        if action == "search":
            return f", keywords={result.get('keywords', [])}"
        if action == "list_tools":
            return f", server_id={result.get('server_id')}"
        if action == "install":
            return f", server_id={result.get('server_id')}"
        if action == "store":
            return f", theme={result.get('theme')}"
        if action == "recall":
            return f", theme={result.get('theme')}"
        if action == "search_memory":
            query = result.get("query", "")[:40]
            offset = result.get("offset", 0)
            return f", query='{query}', top_k={result.get('top_k', 5)}, offset={offset}"
        return ""


# ------------------------------------------------------------------
# ROOT mode actions
# ------------------------------------------------------------------


@_parser("respond")
def _parse_respond(response: Dict[str, Any]) -> Dict[str, Any]:
    output = response.get("output", "")
    return {
        "action": "respond",
        "output": str(output),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("find_tools")
def _parse_find_tools(response: Dict[str, Any]) -> Dict[str, Any]:
    """Parse find_tools — root LLM searches for tools by natural language intent."""
    intent = response.get("intent", "")
    if not intent:
        return {"error": "find_tools action requires 'intent'", "raw": response}
    return {
        "action": "find_tools",
        "intent": str(intent),
    }


@_parser("dispatch")
def _parse_dispatch(response: Dict[str, Any]) -> Dict[str, Any]:
    """Parse dispatch — either concrete tasks or legacy intent routing."""
    tasks = response.get("tasks")
    intent = response.get("intent")

    if intent and not tasks:
        return {
            "action": "dispatch",
            "intent": str(intent),
            "goal_updates": response.get("goal_updates", []),
        }

    if not isinstance(tasks, list) or not tasks:
        return {
            "error": "Dispatch action requires 'intent' (routing) or non-empty 'tasks' (execution)",
            "raw": response,
        }

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

        validated_tasks.append(
            {
                "server": server,
                "tool": tool,
                "params": task.get("params", {}),
                "remind_after": task.get("remind_after"),
            }
        )

    if not validated_tasks:
        return {"error": "No valid tasks in dispatch action", "raw": response}

    return {
        "action": "dispatch",
        "tasks": validated_tasks,
        "goal_updates": response.get("goal_updates", []),
    }


# ------------------------------------------------------------------
# Tool actions (unified root)
# ------------------------------------------------------------------


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


@_parser("wait")
def _parse_wait(response: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action": "wait",
        "pids": response.get("pids"),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("kill")
def _parse_kill(response: Dict[str, Any]) -> Dict[str, Any]:
    pids = response.get("pids", [])
    if not isinstance(pids, list) or not pids:
        return {
            "error": "Kill action requires a non-empty 'pids' list",
            "raw": response,
        }
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
        return {
            "error": "Defer action requires a positive 'duration' (seconds)",
            "raw": response,
        }
    return {
        "action": "defer",
        "goal_id": str(goal_id),
        "duration": int(duration),
        "reason": response.get("reason", ""),
        "goal_updates": response.get("goal_updates", []),
    }


# ------------------------------------------------------------------
# Legacy dispatch subsystem actions (kept for sub-chain fallback)
# ------------------------------------------------------------------


@_parser("plan")
def _parse_plan(response: Dict[str, Any]) -> Dict[str, Any]:
    tasks = response.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        return {
            "error": "Plan action requires a non-empty 'tasks' list",
            "raw": response,
        }

    validated_tasks = []
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            logger.warning(f"TaskParser: Plan task {i} is not a dict, skipping")
            continue

        intent = task.get("intent", "")
        if not intent:
            logger.warning(f"TaskParser: Plan task {i} missing 'intent', skipping")
            continue

        validated_tasks.append(
            {
                "intent": str(intent),
                "keywords": [str(k) for k in task.get("keywords", [])],
                "top_k": int(task.get("top_k", 5)),
                "min_score": float(task.get("min_score", 0.3)),
            }
        )

    if not validated_tasks:
        return {"error": "No valid sub-tasks in plan action", "raw": response}

    return {
        "action": "plan",
        "tasks": validated_tasks,
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("search")
def _parse_search(response: Dict[str, Any]) -> Dict[str, Any]:
    keywords = response.get("keywords", [])
    if not isinstance(keywords, list) or not keywords:
        return {
            "error": "Search action requires a non-empty 'keywords' list",
            "raw": response,
        }
    return {
        "action": "search",
        "keywords": [str(k) for k in keywords],
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("done")
def _parse_done(response: Dict[str, Any]) -> Dict[str, Any]:
    summary = response.get("summary", "")
    return {
        "action": "done",
        "summary": str(summary),
    }


# ------------------------------------------------------------------
# Memory actions
# ------------------------------------------------------------------


@_parser("store")
def _parse_store(response: Dict[str, Any]) -> Dict[str, Any]:
    theme = response.get("theme")
    content = response.get("content")
    if not theme or not content:
        return {"error": "Store action requires 'theme' and 'content'", "raw": response}
    return {
        "action": "store",
        "theme": str(theme),
        "content": str(content),
        "scope": response.get("scope", "session"),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("recall")
def _parse_recall(response: Dict[str, Any]) -> Dict[str, Any]:
    theme = response.get("theme")
    if not theme:
        return {"error": "Recall action requires 'theme'", "raw": response}
    return {
        "action": "recall",
        "theme": str(theme),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("search_memory")
def _parse_search_memory(response: Dict[str, Any]) -> Dict[str, Any]:
    query = response.get("query", "")
    if not query:
        return {"error": "search_memory requires a 'query' string", "raw": response}
    return {
        "action": "search_memory",
        "query": str(query),
        "top_k": int(response.get("top_k", 5)),
        "offset": int(response.get("offset", 0)),
        "min_score": float(response.get("min_score", 0.3)),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("list_memory")
def _parse_list_memory(response: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action": "list_memory",
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("rename_session")
def _parse_rename_session(response: Dict[str, Any]) -> Dict[str, Any]:
    title = response.get("title", "").strip()
    if not title:
        return {"error": "rename_session requires 'title'", "raw": response}
    return {
        "action": "rename_session",
        "title": title,
        "goal_updates": response.get("goal_updates", []),
    }
