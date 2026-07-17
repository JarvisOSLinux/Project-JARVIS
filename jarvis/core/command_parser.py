"""
Task parser for JARVIS hierarchical prompt system.

Validates and parses LLM responses into structured actions.

ROOT mode actions:  respond, dispatch (route),
                    store, recall, search_memory, list_memory (memory)
DISPATCH mode actions: plan, search, list_tools, install, dispatch (tasks),
                       wait, kill, defer, done
"""

from typing import Any, Dict

from .logger import get_logger

logger = get_logger(__name__)

VALID_ACTIONS = {
    # Root — core
    "respond",
    "dispatch",
    # Root — tool discovery (multi-step: search → docs → dispatch)
    "search_tools",
    "get_server_docs",
    "install_server",
    "uninstall_server",
    "configure_server",
    # Root — memory (direct operations, no sub-chain)
    "store",
    "recall",
    "search_memory",
    "list_memory",
    # Root — read-only task/goal introspection (#191)
    "status",
    # Dispatch subsystem
    "plan",
    "search",
    "list_tools",
    "install",
    "wait",
    "kill",
    "defer",
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
        if action == "search_tools":
            cap = result.get("capability", "")
            preview = (cap[:80] + "...") if len(cap) > 80 else cap
            return f", capability='{preview}'"
        if action == "get_server_docs":
            return f", server_id={result.get('server_id')}"
        if action == "install_server":
            return f", server_id={result.get('server_id')}"
        if action == "configure_server":
            keys = list(result.get("config", {}).keys())
            return f", server_id={result.get('server_id')}, keys={keys}"
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
        if action == "status":
            return f", goal_id={result.get('goal_id')}"
        return ""


# ------------------------------------------------------------------
# ROOT mode actions
# ------------------------------------------------------------------


@_parser("respond")
def _parse_respond(response: Dict[str, Any]) -> Dict[str, Any]:
    # LLM sometimes uses "content" instead of "output" — tolerate both.
    output = response.get("output") or response.get("content") or ""
    return {
        "action": "respond",
        "output": str(output),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("search_tools")
def _parse_search_tools(response: Dict[str, Any]) -> Dict[str, Any]:
    # LLM sometimes wraps fields in a "params" sub-object (confusing dispatch format)
    # or uses "query" instead of "capability" — tolerate both.
    params = response.get("params") if isinstance(response.get("params"), dict) else {}
    capability = (
        response.get("capability")
        or params.get("capability")
        or params.get("query")
        or ""
    )
    if not capability:
        return {"error": "search_tools requires 'capability'", "raw": response}
    return {
        "action": "search_tools",
        "capability": str(capability),
        "top_k": int(response.get("top_k", 5)),
        "min_score": float(response.get("min_score", 0.25)),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("get_server_docs")
def _parse_get_server_docs(response: Dict[str, Any]) -> Dict[str, Any]:
    server_id = response.get("server_id", "")
    if not server_id:
        return {"error": "get_server_docs requires 'server_id'", "raw": response}
    return {
        "action": "get_server_docs",
        "server_id": str(server_id),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("install_server")
def _parse_install_server(response: Dict[str, Any]) -> Dict[str, Any]:
    server_id = response.get("server_id", "")
    if not server_id:
        return {"error": "install_server requires 'server_id'", "raw": response}
    return {
        "action": "install_server",
        "server_id": str(server_id),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("uninstall_server")
def _parse_uninstall_server(response: Dict[str, Any]) -> Dict[str, Any]:
    server_id = response.get("server_id", "")
    if not server_id:
        return {"error": "uninstall_server requires 'server_id'", "raw": response}
    return {
        "action": "uninstall_server",
        "server_id": str(server_id),
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("configure_server")
def _parse_configure_server(response: Dict[str, Any]) -> Dict[str, Any]:
    server_id = response.get("server_id", "")
    config = response.get("config", {})
    if not server_id:
        return {"error": "configure_server requires 'server_id'", "raw": response}
    if not isinstance(config, dict) or not config:
        return {
            "error": "configure_server requires a non-empty 'config' dict",
            "raw": response,
        }
    return {
        "action": "configure_server",
        "server_id": str(server_id),
        "config": {str(k): str(v) for k, v in config.items()},
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("status")
def _parse_status(response: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only introspection of live/held tasks (#191).

    ``goal_id`` is optional — omit it to see every active goal; supply it
    (from GOAL_STATE/ACTIVE_GOALS context) to scope the read to one goal's
    own task_pids.
    """
    goal_id = response.get("goal_id")
    return {
        "action": "status",
        "goal_id": str(goal_id) if goal_id else None,
        "goal_updates": response.get("goal_updates", []),
    }


@_parser("dispatch")
def _parse_dispatch(response: Dict[str, Any]) -> Dict[str, Any]:
    """Parse dispatch — either a ROOT routing intent or DISPATCH-mode tasks."""
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
# DISPATCH subsystem actions
# ------------------------------------------------------------------


@_parser("plan")
def _parse_plan(response: Dict[str, Any]) -> Dict[str, Any]:
    """Parse plan action — LLM breaks intent into sub-tasks for tool discovery."""
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


@_parser("done")
def _parse_done(response: Dict[str, Any]) -> Dict[str, Any]:
    """Subsystem signals completion and returns a summary to root."""
    summary = response.get("summary", "")
    return {
        "action": "done",
        "summary": str(summary),
    }


# ------------------------------------------------------------------
# ROOT-level memory actions (direct operations, no sub-chain)
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
    """Parse search_memory — semantic search with natural language query."""
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
