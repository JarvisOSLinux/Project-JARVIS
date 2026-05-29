"""dmcp-backed registry operations (browse/install/tools and command runner)."""

from __future__ import annotations

import asyncio
import json
from logging import Logger
from typing import Any, Dict, List, Optional

from ..config import Config


async def run_dmcp(logger: Logger, *args: str) -> Optional[str]:
    """Run a dmcp command and return stdout, or None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            Config.DMCP_BINARY,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except FileNotFoundError:
        logger.warning("Dispatch: dmcp binary not found")
        return None
    except asyncio.TimeoutError:
        logger.warning(f"Dispatch: dmcp {args[0]} timed out")
        return None

    if proc.returncode != 0:
        logger.warning(
            f"Dispatch: dmcp {' '.join(args)} failed: {stderr.decode().strip()}"
        )
        return None

    return stdout.decode()


async def _local_installed_servers(logger: Logger) -> List[Dict[str, Any]]:
    """Return installed servers (with manifest keywords) via `dmcp list --json`."""
    import os

    raw = await run_dmcp(logger, "list", "--json")
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(entries, list):
        return []

    result = []
    for entry in entries:
        manifest_path = entry.get("manifest_path", "")
        keywords: List[str] = []
        description = ""
        if manifest_path and os.path.isfile(manifest_path):
            try:
                with open(manifest_path) as f:
                    mf = json.load(f)
                keywords = mf.get("keywords", [])
                description = mf.get("description", "")
            except Exception:
                pass
        result.append(
            {
                "id": entry.get("id", ""),
                "name": entry.get("name", entry.get("id", "")),
                "description": description,
                "keywords": keywords,
                "installed": True,
            }
        )
    return result


async def search_servers(logger: Logger, keywords: List[str]) -> Dict[str, Any]:
    """Search for MCP servers by keywords via `dmcp browse` + locally installed manifests."""
    logger.info(f"Dispatch: Searching MCP servers with keywords: {keywords}")

    # dmcp browse treats multiple -k values as a strict filter (often effectively AND).
    # For natural language intents that's too restrictive and frequently yields only a
    # single match. Prefer an OR-style union: browse once per keyword, then merge.
    merged_by_id: Dict[str, Dict[str, Any]] = {}

    cleaned: List[str] = []
    for kw in keywords:
        kw = str(kw).strip()
        if not kw:
            continue
        # Shell-ish intents often include flags like "--version"; those don't help
        # find a server and can confuse both parsing and relevance.
        if kw.startswith("-"):
            continue
        cleaned.append(kw)

    # If everything was filtered out, fall back to the original list so the caller
    # can still see an explicit error rather than silently returning nothing.
    query_terms = cleaned or [str(k).strip() for k in keywords if str(k).strip()]

    for kw in query_terms:
        cmd_args = ["browse", "--json"]
        if kw.startswith("-"):
            cmd_args.append(f"--keyword={kw}")
        else:
            cmd_args.extend(["-k", kw])

        raw = await run_dmcp(logger, *cmd_args)
        if raw is None:
            continue

        try:
            servers = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if not isinstance(servers, list):
            servers = servers.get("servers", []) if isinstance(servers, dict) else []

        for s in servers:
            if not isinstance(s, dict):
                continue
            sid = s.get("id") or s.get("server_id")
            if not sid:
                continue
            existing = merged_by_id.get(sid)
            if not existing:
                merged_by_id[sid] = s
                continue
            # Prefer installed=True if any query reports it installed.
            if s.get("installed") and not existing.get("installed"):
                merged_by_id[sid] = {**existing, **s, "installed": True}

    servers = list(merged_by_id.values())

    # Also search locally installed servers not covered by remote registries.
    local_servers = await _local_installed_servers(logger)
    registry_ids = {s.get("id") for s in servers}
    kw_lower = [k.lower() for k in keywords]

    for ls in local_servers:
        if ls["id"] in registry_ids:
            continue
        # Match against description and keywords only — not the server ID.
        # Server IDs encode implementation details (e.g. "-py" suffix) that
        # would cause false positives when users search by language intent.
        searchable = " ".join(
            [ls["name"], ls["description"]] + ls["keywords"]
        ).lower()
        if any(kw in searchable for kw in kw_lower):
            servers.append(ls)

    installed = [s for s in servers if s.get("installed")]
    available = [s for s in servers if not s.get("installed")]
    sorted_servers = installed + available

    logger.info(
        f"Dispatch: Found {len(installed)} installed, "
        f"{len(available)} available server(s) for keywords {keywords}"
    )

    return {"servers": sorted_servers}


async def install_server(logger: Logger, server_id: str) -> Dict[str, Any]:
    """Install an MCP server from registry via `dmcp install`."""
    logger.info(f"Dispatch: Installing MCP server '{server_id}'")
    raw = await run_dmcp(logger, "install", server_id)
    if raw is None:
        return {"error": f"Failed to install server '{server_id}'"}
    return {"installed": server_id, "output": raw.strip()}


async def uninstall_server(logger: Logger, server_id: str) -> Dict[str, Any]:
    """Uninstall an MCP server via `dmcp uninstall`."""
    logger.info(f"Dispatch: Uninstalling MCP server '{server_id}'")
    raw = await run_dmcp(logger, "uninstall", server_id)
    if raw is None:
        return {"error": f"Failed to uninstall server '{server_id}'"}
    return {"uninstalled": server_id, "output": raw.strip()}


async def list_server_tools(logger: Logger, server_id: str) -> Dict[str, Any]:
    """List tools available on an installed MCP server."""
    logger.info(f"Dispatch: Listing tools for server '{server_id}'")
    raw = await run_dmcp(logger, "tools", server_id, "--json")
    if raw is None:
        return {"error": f"Failed to list tools for '{server_id}'", "tools": []}

    try:
        tools = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "dmcp returned invalid JSON", "tools": []}

    if isinstance(tools, dict) and "tools" in tools:
        tools = tools["tools"]
    if not isinstance(tools, list):
        tools = []

    logger.info(f"Dispatch: Server '{server_id}' has {len(tools)} tool(s)")
    return {"server": server_id, "tools": tools}
