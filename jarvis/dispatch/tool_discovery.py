"""High-level tool discovery helpers (embedding-based + formatting)."""

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

    Always uses embedding search. When embeddings are unavailable or fail,
    falls back to listing all visible servers as CANDIDATE_SERVERS so the
    LLM can still choose, install, and dispatch.
    """
    all_results: List[Dict[str, Any]] = []

    if embeddings is None:
        logger.info(
            "Dispatch: discover_tools — embeddings unavailable, listing all servers"
        )
        all_results = await _all_servers_as_candidates(adapter, logger)
    else:
        logger.info(
            f"Dispatch: discover_tools — {len(tasks)} sub-task(s), embedding search"
        )
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

        except Exception as e:
            logger.warning(
                f"Dispatch: Embedding discovery failed, listing all servers: {e}"
            )
            all_results = await _all_servers_as_candidates(adapter, logger)

    if not all_results:
        logger.info("Dispatch: discover_tools found no matching tools")
        return ""

    return format_available_tools(all_results)


async def _all_servers_as_candidates(
    adapter: Any, logger: Logger
) -> List[Dict[str, Any]]:
    """Return all visible servers as candidate rows (graceful degradation fallback)."""
    result = await adapter.list_visible_servers()
    servers = result.get("servers", [])
    candidates = []
    for s in servers:
        server_id = s.get("id", s.get("server_id", ""))
        if not server_id:
            continue
        candidates.append(
            {
                "server_id": server_id,
                "server_name": s.get("name", server_id),
                "description": s.get("description", ""),
                "installed": bool(s.get("installed", False)),
                "score": 0,
                "_source": "list_all",
            }
        )
    return candidates


def format_available_tools(results: List[Dict[str, Any]]) -> str:
    """Render results into MATCHED_TOOLS and CANDIDATE_SERVERS blocks."""
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
        lines = ["CANDIDATE_SERVERS (not installed — use install + list_tools):"]
        for r in candidates:
            server_id = r.get("server_id", "unknown")
            line = f"  {server_id}"
            description = r.get("description", "")
            if description:
                line += f"\n    {description}"
            lines.append(line)
        sections.append("\n".join(lines))

    return "\n\n".join(sections)
