#!/usr/bin/env python3
"""ShellMCP — MCP server for shell command execution.

Privileged commands (pacman, systemctl, etc.) are run via
`sudo -A` with SUDO_ASKPASS=ksshaskpass, triggering the KWallet
GUI password dialog to maintain the autonomous + secure architecture.
"""

import asyncio
import json
import os
import sys

# Commands that require root — prefix with sudo -A
PRIVILEGED_PREFIXES = (
    "pacman", "systemctl enable", "systemctl disable",
    "systemctl start", "systemctl stop", "systemctl restart",
    "systemctl mask", "systemctl unmask", "systemctl daemon-reload",
    "modprobe", "rmmod", "insmod", "sysctl -w",
    "useradd", "userdel", "groupadd", "groupdel", "usermod",
    "timedatectl set", "localectl set", "hostnamectl set",
    "ip link set", "ip addr add",
    "tee /etc", "tee /usr", "tee /var",
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


async def run_command(command: str, timeout: int = 120) -> str:
    cmd = build_command(command)
    env = os.environ.copy()
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
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 120)", "default": 120},
            },
            "required": ["command"],
        },
    }
]


async def handle(request: dict):
    method = request.get("method")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
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
        if params.get("name") == "run_command":
            args = params.get("arguments", {})
            output = await run_command(
                args.get("command", ""),
                int(args.get("timeout", 120)),
            )
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": output}]},
            }
        return {
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {params.get('name')}"},
        }
    elif method in ("notifications/initialized", "notifications/cancelled"):
        return None
    elif req_id is not None:
        return {
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


async def main():
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
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
