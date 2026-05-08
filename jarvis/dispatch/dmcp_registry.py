"""dmcp-backed registry operations (install/tools/list and command runner)."""

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


async def list_visible_servers(logger: Logger) -> Dict[str, Any]:
    """Return all visible MCP servers via `dmcp browse --json` with no keyword filter."""
    raw = await run_dmcp(logger, "browse", "--json")
    if raw is None:
        return {"error": "dmcp browse failed", "servers": []}
    try:
        servers = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "dmcp returned invalid JSON", "servers": []}
    if not isinstance(servers, list):
        servers = servers.get("servers", []) if isinstance(servers, dict) else []
    installed = [s for s in servers if s.get("installed")]
    available = [s for s in servers if not s.get("installed")]
    return {"servers": installed + available}


async def install_server(logger: Logger, server_id: str) -> Dict[str, Any]:
    """Install an MCP server from registry via `dmcp install`."""
    logger.info(f"Dispatch: Installing MCP server '{server_id}'")
    raw = await run_dmcp(logger, "install", server_id)
    if raw is None:
        return {"error": f"Failed to install server '{server_id}'"}
    return {"installed": server_id, "output": raw.strip()}


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
