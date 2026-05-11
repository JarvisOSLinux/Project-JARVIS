"""High-level tool discovery helpers (embedding + keyword fallback + formatting)."""

from __future__ import annotations

import json
from logging import Logger
from typing import Any, Dict, List, Optional


async def discover_tools(
    adapter: Any,
    logger: Logger,
    tasks: List[Dict[str, Any]],
    embeddings: Optional[Any] = None,
) -> str:
    """
    Tool discovery entry point after a dispatch ``plan`` action.

    Runs whichever backend ``select_discovery_mode`` picked (embedding or
    keyword) and returns a formatted ``MATCHED_TOOLS`` / ``CANDIDATE_SERVERS``
    block for prompt injection. Empty string if nothing matched.
    """
    mode = await adapter.select_discovery_mode(embeddings)
    logger.info(f"Dispatch: discover_tools — {len(tasks)} sub-task(s), mode={mode}")

    all_results: List[Dict[str, Any]] = []

    if mode == "embedding":
        intents = [t.get("intent", "") for t in tasks]
        try:
            vectors = embeddings.embed_batch(intents)
            top_k = max(t.get("top_k", 5) for t in tasks)
            min_score = min(t.get("min_score", 0.3) for t in tasks)

            batch_result = await adapter.browse_vectors_batch(
                vectors=vectors,
                top_k=top_k,
                min_score=min_score,
            )

            result_sets = batch_result.get("results", [])
            if isinstance(result_sets, list):
                for i, result_set in enumerate(result_sets):
                    if isinstance(result_set, list) and result_set:
                        for r in result_set:
                            r["_source_task"] = intents[i]
                        all_results.extend(result_set)
                    else:
                        # Per-task fallback when embedding returns empty for this task.
                        all_results.extend(
                            await keyword_fallback(adapter, logger, tasks[i])
                        )
                # If embedding returned fewer result sets than tasks, cover the rest.
                for i in range(len(result_sets), len(tasks)):
                    all_results.extend(
                        await keyword_fallback(adapter, logger, tasks[i])
                    )

            # Global fallback: embedding search ran but returned nothing at all
            # (e.g. vector index is empty because no servers are installed yet).
            # Keyword search hits the full server catalog including uninstalled
            # servers, so this is the path that surfaces CANDIDATE_SERVERS.
            if not all_results:
                logger.info(
                    "Dispatch: Embedding index empty — falling back to keyword search"
                )
                for task in tasks:
                    all_results.extend(await keyword_fallback(adapter, logger, task))

        except Exception as e:
            logger.warning(
                f"Dispatch: Embedding discovery failed, falling back to keywords: {e}"
            )
            for task in tasks:
                all_results.extend(await keyword_fallback(adapter, logger, task))
    else:
        for task in tasks:
            all_results.extend(await keyword_fallback(adapter, logger, task))

    # Global fallback: embedding index was empty (result_sets=[]) so the
    # per-task fallback loop never ran.
    if not all_results and mode == "embedding":
        logger.info("Dispatch: Embedding index empty — falling back to keyword search")
        for task in tasks:
            all_results.extend(await keyword_fallback(adapter, logger, task))

    if not all_results:
        logger.info("Dispatch: discover_tools found no matching tools")
        return ""

    return format_available_tools(all_results)


async def keyword_fallback(
    adapter: Any, logger: Logger, task: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Keyword search fallback for a single sub-task."""
    keywords = task.get("keywords", [])
    if not keywords:
        intent = task.get("intent", "")
        keywords = [w for w in intent.lower().split() if len(w) > 3]

    if not keywords:
        return []

    result = await adapter.search_servers(keywords)
    servers = result.get("servers", [])

    source_task = task.get("intent", "")
    tool_results: List[Dict[str, Any]] = []

    for server in servers:
        server_id = server.get("id", server.get("server_id", ""))
        if not server_id:
            continue

        installed = bool(server.get("installed", False))
        server_name = server.get("name", server_id)
        server_desc = server.get("description", "")

        if installed:
            tools_result = await adapter.list_server_tools(server_id)
            tools = (
                tools_result.get("tools", []) if isinstance(tools_result, dict) else []
            )

            if not tools:
                # Binary is broken (e.g. dist/index.js missing) — reinstall
                # silently and retry once before giving up on this server.
                logger.info(
                    f"Dispatch: Server '{server_id}' installed but not working "
                    "— reinstalling"
                )
                try:
                    await adapter.install_server(server_id)
                    tools_result = await adapter.list_server_tools(server_id)
                    tools = (
                        tools_result.get("tools", [])
                        if isinstance(tools_result, dict)
                        else []
                    )
                except Exception as e:
                    logger.warning(
                        f"Dispatch: Reinstall of '{server_id}' failed: {e}"
                    )

            if tools:
                for tool in tools:
                    if not isinstance(tool, dict):
                        continue
                    tool_name = tool.get("name", "")
                    if not tool_name:
                        continue
                    tool_results.append(
                        {
                            "server_id": server_id,
                            "server_name": server_name,
                            "tool_name": tool_name,
                            "description": tool.get("description", ""),
                            "params": tool.get(
                                "inputSchema", tool.get("params", {})
                            ),
                            "installed": True,
                            "score": 0,
                            "_source": "keyword",
                            "_source_task": source_task,
                        }
                    )
                continue

            # Still broken after reinstall — skip silently.
            logger.warning(
                f"Dispatch: Server '{server_id}' still broken after reinstall "
                "— skipping"
            )
            continue

        tool_results.append(
            {
                "server_id": server_id,
                "server_name": server_name,
                "description": server_desc,
                "installed": installed,
                "score": 0,
                "_source": "keyword",
                "_source_task": source_task,
            }
        )

    return tool_results


def format_available_tools(results: List[Dict[str, Any]]) -> str:
    """Render results into MATCHED_TOOLS and CANDIDATE_SERVERS blocks.

    Servers that are installed but failed to load tools are shown in
    CANDIDATE_SERVERS alongside uninstalled ones — the LLM should use
    ``install`` for both cases. ``dmcp install`` always clears and rebuilds
    the install directory, so reinstalling a broken server is safe.
    """
    matched: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []

    seen_tools: set = set()
    seen_servers: set = set()

    for r in results:
        server_id = r.get("server_id", "")
        if not server_id:
            continue
        tool_name = r.get("tool_name", "")
        installed = bool(r.get("installed", True))

        is_dispatchable = bool(tool_name) and installed
        if is_dispatchable:
            key = (server_id, tool_name)
            if key in seen_tools:
                continue
            seen_tools.add(key)
            matched.append(r)
        else:
            if server_id in seen_servers:
                continue
            seen_servers.add(server_id)
            candidates.append(r)

    sections: List[str] = []

    if matched:
        lines = ["MATCHED_TOOLS:"]
        for r in matched:
            server_id = r.get("server_id", "unknown")
            tool_name = r.get("tool_name", "")
            line = f"  {server_id}/{tool_name}"
            score = r.get("score", 0)
            if score:
                line += f" (relevance: {score:.0%})"
            description = r.get("description", "")
            if description:
                line += f"\n    {description}"
            params = r.get("params", r.get("parameter_schema", ""))
            if params:
                params_str = (
                    json.dumps(params) if isinstance(params, dict) else str(params)
                )
                line += f"\n    params: {params_str}"
            lines.append(line)
        sections.append("\n".join(lines))

    if candidates:
        lines = ["CANDIDATE_SERVERS (not ready — use install to set up or reinstall):"]
        for r in candidates:
            server_id = r.get("server_id", "unknown")
            line = f"  {server_id}"
            description = r.get("description", "")
            if description:
                line += f"\n    {description}"
            lines.append(line)
        sections.append("\n".join(lines))

    return "\n\n".join(sections)
