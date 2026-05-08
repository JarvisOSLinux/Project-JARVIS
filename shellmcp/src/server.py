#!/usr/bin/env python3
"""ShellMCP — MCP server for shell command execution.

Privileged commands (pacman, systemctl, etc.) are run via
`sudo -A` with SUDO_ASKPASS=ksshaskpass, triggering the KWallet
GUI password dialog to maintain the autonomous + secure architecture.
"""

import asyncio
import json
import os
import shlex
import sys
import urllib.parse
import urllib.request

# Commands that require root — prefix with sudo -A
PRIVILEGED_PREFIXES = (
    "pacman",
    "systemctl enable",
    "systemctl disable",
    "systemctl start",
    "systemctl stop",
    "systemctl restart",
    "systemctl mask",
    "systemctl unmask",
    "systemctl daemon-reload",
    "modprobe",
    "rmmod",
    "insmod",
    "sysctl -w",
    "useradd",
    "userdel",
    "groupadd",
    "groupdel",
    "usermod",
    "timedatectl set",
    "localectl set",
    "hostnamectl set",
    "ip link set",
    "ip addr add",
    "tee /etc",
    "tee /usr",
    "tee /var",
)


def needs_sudo(command: str) -> bool:
    stripped = command.strip()
    for prefix in PRIVILEGED_PREFIXES:
        if stripped.startswith(prefix):
            return True
    return False


def build_command(command: str) -> str:
    """Wrap privileged commands with sudo -A + ksshaskpass."""
    command = command.strip()
    if command.startswith("sudo "):
        command = command[5:].strip()
    if needs_sudo(command):
        return f"sudo -A {command}"
    return command


def _display_env() -> dict:
    """Build env with display vars set, detecting from XDG_RUNTIME_DIR if missing."""
    env = os.environ.copy()
    if "WAYLAND_DISPLAY" not in env:
        xdg_runtime = env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        for candidate in ("wayland-1", "wayland-0"):
            if os.path.exists(os.path.join(xdg_runtime, candidate)):
                env["WAYLAND_DISPLAY"] = candidate
                env.setdefault("XDG_RUNTIME_DIR", xdg_runtime)
                break
    env.setdefault("DISPLAY", ":0")
    return env


async def open_app(target: str) -> str:
    """Open a file, URL, or application via xdg-open (never elevated)."""
    env = _display_env()
    cmd = f"xdg-open {shlex.quote(target)}"
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
            err = stderr.decode(errors="replace").strip()
            if proc.returncode not in (0, None):
                return f"xdg-open failed (exit {proc.returncode}): {err}"
            return f"Opened: {target}"
        except asyncio.TimeoutError:
            # GUI app forked and detached — expected
            return f"Launched: {target}"
    except Exception as e:
        return f"Error: {e}"


_BRAVE_KEY_PATH = os.path.expanduser("~/.config/jarvis/brave_api_key")


def _brave_api_key() -> str:
    try:
        return open(_BRAVE_KEY_PATH).read().strip()
    except OSError:
        return ""


async def _search_brave(query: str, max_results: int, api_key: str) -> str:
    params = urllib.parse.urlencode({"q": query, "count": max_results})
    url = f"https://api.search.brave.com/res/v1/web/search?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
    )
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        None, lambda: urllib.request.urlopen(req, timeout=10).read()
    )
    data = json.loads(raw.decode())
    results = data.get("web", {}).get("results", [])
    if not results:
        return f"No results found for: {query}"
    parts = []
    for r in results[:max_results]:
        parts.append(f"- {r.get('title', '(no title)')}\n  {r.get('url', '')}")
        if r.get("description"):
            parts.append(f"  {r['description']}")
    return "\n".join(parts)


_SEARXNG_BASE = "https://trojanhoogle.pro"


async def _search_searxng(query: str, max_results: int) -> str:
    params = urllib.parse.urlencode({"q": query, "format": "json"})
    url = f"{_SEARXNG_BASE}/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "JarvisOS/1.0"})
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        None, lambda: urllib.request.urlopen(req, timeout=10).read()
    )
    data = json.loads(raw.decode())
    results = data.get("results", [])
    if not results:
        return f"No results found for: {query}"
    parts = []
    for r in results[:max_results]:
        parts.append(f"- {r.get('title', '(no title)')}\n  {r.get('url', '')}")
        if r.get("content"):
            parts.append(f"  {r['content']}")
    return "\n".join(parts)


async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web. Uses Brave Search if API key present at ~/.config/jarvis/brave_api_key,
    otherwise falls back to SearXNG at trojanhoogle.pro."""
    try:
        api_key = _brave_api_key()
        if api_key:
            return await _search_brave(query, max_results, api_key)
        return await _search_searxng(query, max_results)
    except Exception as e:
        return f"Search failed: {e}"


async def run_command(command: str, timeout: int = 120) -> str:
    cmd = build_command(command)
    env = _display_env()
    for askpass in ("/usr/bin/ksshaskpass", "/usr/lib/ssh/ksshaskpass"):
        if os.path.exists(askpass):
            env["SUDO_ASKPASS"] = askpass
            break
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")
        if err:
            output += "\nSTDERR: " + err
        if proc.returncode != 0:
            output += f"\nEXIT CODE: {proc.returncode}"
        return output or "(no output)"
    except asyncio.TimeoutError:
        return f"Command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


TOOLS = [
    {
        "name": "run_command",
        "description": (
            "Execute a shell command and return its output. "
            "Privileged commands (pacman, systemctl, etc.) automatically "
            "request elevation via a GUI password dialog (KWallet/ksshaskpass)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 120)",
                    "default": 120,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "web_search",
        "description": (
            "Search the web using DuckDuckGo. Returns direct answers, summaries, "
            "and related results. No API key required. Use for current information, "
            "factual lookups, news, documentation, and anything requiring internet knowledge."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max related results to return (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_app",
        "description": (
            "Open a file, URL, or application using xdg-open. "
            "Respects the user's default application associations. "
            "Use for: launching GUI apps (firefox, dolphin, code), "
            "opening files (PDF, images, documents) with their default app, "
            "or opening URLs in the default browser. "
            "Never requires sudo — always runs as the current user."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "File path, URL, or application binary name to open",
                },
            },
            "required": ["target"],
        },
    },
]


async def handle(request: dict):
    method = request.get("method")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ShellMCP", "version": "1.0.0"},
            },
        }
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name")
        args = params.get("arguments", {})
        if tool_name == "web_search":
            output = await web_search(
                args.get("query", ""),
                int(args.get("max_results", 5)),
            )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": output}]},
            }
        elif tool_name == "run_command":
            output = await run_command(
                args.get("command", ""),
                int(args.get("timeout", 120)),
            )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": output}]},
            }
        elif tool_name == "open_app":
            output = await open_app(args.get("target", ""))
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": output}]},
            }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
        }
    elif method in ("notifications/initialized", "notifications/cancelled"):
        return None
    elif req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


async def main():
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )
    write_transport, _ = await loop.connect_write_pipe(asyncio.BaseProtocol, sys.stdout)

    buf = b""
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = await handle(req)
                if resp is not None:
                    write_transport.write((json.dumps(resp) + "\n").encode())
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
