"""dmcp-backed registry operations (browse/install/tools and command runner)."""

from __future__ import annotations

import ast
import asyncio
import json
import os
from logging import Logger
from pathlib import Path
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
        searchable = " ".join([ls["name"], ls["description"]] + ls["keywords"]).lower()
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
    """Install an MCP server without running its setup script (`dmcp install --no_setup`)."""
    logger.info(f"Dispatch: Installing MCP server '{server_id}' (no setup)")
    raw = await run_dmcp(logger, "install", "--no_setup", server_id)
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


def _extract_mcp_docstrings(source_file: Path) -> Dict[str, str]:
    """Return {tool_name: first_docstring_line} for @mcp.tool-decorated fns."""
    try:
        tree = ast.parse(source_file.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}

    result: Dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        is_tool = False
        explicit_name: Optional[str] = None
        for dec in node.decorator_list:
            # @tool, @mcp.tool, @server.tool, @app.tool
            bare = None
            call_func = None
            call_kwargs: Dict[str, Any] = {}
            if isinstance(dec, ast.Attribute) and dec.attr == "tool":
                is_tool = True
                bare = dec.attr
            elif isinstance(dec, ast.Name) and dec.id == "tool":
                is_tool = True
                bare = dec.id
            elif isinstance(dec, ast.Call):
                f = dec.func
                if (isinstance(f, ast.Attribute) and f.attr == "tool") or (
                    isinstance(f, ast.Name) and f.id == "tool"
                ):
                    is_tool = True
                    # Look for name= kwarg
                    for kw in dec.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                            explicit_name = str(kw.value.value)

        if not is_tool:
            continue

        docstring = ast.get_docstring(node)
        if not docstring:
            continue

        first_line = docstring.split("\n")[0].strip()
        name = explicit_name or node.name
        result[name] = first_line

    return result


def _find_python_source(manifest: Dict[str, Any]) -> Optional[Path]:
    """Locate the main Python source file referenced by a server manifest."""
    transports = manifest.get("transports") or []
    if not transports:
        return None

    primary = transports[0]
    if primary.get("type") != "stdio":
        return None

    install_dir = manifest.get("installDir") or manifest.get("install_dir")
    if not install_dir:
        return None
    base = Path(install_dir)
    if not base.is_dir():
        return None

    # Try each arg for a .py file
    for arg in primary.get("args") or []:
        p = Path(arg)
        if p.suffix != ".py":
            continue
        candidate = base / p if not p.is_absolute() else p
        if candidate.exists():
            return candidate

    # Fallback: well-known entry-point names
    for name in ("server.py", "main.py", "app.py", "__main__.py"):
        f = base / name
        if f.exists():
            return f

    # Last resort: single .py file in install dir
    py_files = list(base.glob("*.py"))
    if len(py_files) == 1:
        return py_files[0]

    return None


async def list_server_tools(logger: Logger, server_id: str) -> Dict[str, Any]:
    """List tools available on an installed MCP server."""
    logger.info(f"Dispatch: Listing tools for server '{server_id}'")
    try:
        proc = await asyncio.create_subprocess_exec(
            Config.DMCP_BINARY,
            "tools",
            server_id,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except FileNotFoundError:
        return {"error": "dmcp binary not found", "tools": []}
    except asyncio.TimeoutError:
        return {"error": "dmcp tools timed out", "tools": []}

    stderr_text = stderr.decode().strip()

    if proc.returncode != 0:
        logger.warning(f"Dispatch: dmcp tools {server_id} failed: {stderr_text}")
        return {
            "error": stderr_text or f"Failed to list tools for '{server_id}'",
            "tools": [],
        }

    raw = stdout.decode()
    try:
        tools = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "dmcp returned invalid JSON", "tools": []}

    if isinstance(tools, dict) and "tools" in tools:
        tools = tools["tools"]
    if not isinstance(tools, list):
        tools = []

    logger.info(f"Dispatch: Server '{server_id}' has {len(tools)} tool(s)")

    # Docstring fallback: if any tools lack descriptions, parse Python source.
    if any(not t.get("description") for t in tools if isinstance(t, dict)):
        base = _dmcp_base_dir()
        manifest_path = base / "installed" / server_id / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                src = _find_python_source(manifest)
                if src:
                    docstrings = _extract_mcp_docstrings(src)
                    if docstrings:
                        for tool in tools:
                            if not isinstance(tool, dict):
                                continue
                            if not tool.get("description"):
                                name = tool.get("name", "")
                                if name in docstrings:
                                    tool["description"] = docstrings[name]
                        logger.debug(
                            f"Dispatch: applied docstring fallback for {server_id}"
                            f" ({len(docstrings)} found)"
                        )
            except Exception as e:
                logger.debug(f"Dispatch: docstring fallback error for {server_id}: {e}")

    return {"server": server_id, "tools": tools}


def _dmcp_base_dir() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "mcp"


async def get_server_manifest(logger: Logger, server_id: str) -> Dict[str, Any]:
    """Return the manifest dict for a server, reading locally with no network call.

    Checks the installed manifest first; falls back to the registry clone.
    Returns an empty dict if neither exists.
    """
    base = _dmcp_base_dir()
    candidates = [
        base / "installed" / server_id / "manifest.json",
        base / "registry" / server_id / "manifest.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Dispatch: failed to read manifest at {path}: {e}")
    logger.debug(f"Dispatch: no local manifest found for '{server_id}'")
    return {}


async def run_server_setup(logger: Logger, server_id: str) -> Dict[str, Any]:
    """Run the setup script for an installed server via `dmcp setup <id>`."""
    logger.info(f"Dispatch: Running setup for '{server_id}'")
    try:
        proc = await asyncio.create_subprocess_exec(
            Config.DMCP_BINARY,
            "setup",
            server_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except FileNotFoundError:
        return {"error": "dmcp binary not found"}
    except asyncio.TimeoutError:
        return {"error": f"dmcp setup '{server_id}' timed out"}

    stderr_text = stderr.decode().strip()
    stdout_text = stdout.decode().strip()

    if proc.returncode != 0:
        logger.warning(f"Dispatch: dmcp setup '{server_id}' failed: {stderr_text}")
        return {"error": stderr_text or f"Setup failed for '{server_id}'"}

    logger.info(f"Dispatch: Setup succeeded for '{server_id}'")
    return {"ok": True, "output": stdout_text}
