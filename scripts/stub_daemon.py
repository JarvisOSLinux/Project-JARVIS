"""A jarvis.sock stand-in for trying the web page without an LLM or the Rust binaries.

Speaks just enough of the socket protocol for `jarvis web`: settings, sessions,
streamed replies and confirmations. Send "confirm" in the chat to get a pending
permission request, and "run" to watch a turn with a comment, the activity
line and action blocks (docs/gui-protocol.md); any other text is echoed back in
two chunks. `get_status` and `list_work` answer with sample data.

    python scripts/stub_daemon.py <socket-path>
"""

import asyncio
import json
import os
import sys
import time

SESSION = {"id": "demo", "title": "Demo session", "entry_count": 0}
pending: list[dict] = []
writers: list[asyncio.StreamWriter] = []


async def broadcast(msg: dict) -> None:
    data = (json.dumps(msg) + "\n").encode()
    for w in writers:
        w.write(data)
        await w.drain()


async def reply(text: str) -> None:
    await broadcast({"type": "state", "state": "processing"})
    for part in (f"You said: {text}", "\n\n(stub daemon, no LLM behind this)"):
        await asyncio.sleep(0.4)
        await broadcast({"type": "response", "content": part, "done": False})
    await broadcast({"type": "response", "content": "", "done": True})
    await broadcast({"type": "state", "state": "idle"})


running: dict = {}


async def action(phase: str, action_id: str, **fields) -> None:
    await broadcast(
        {"type": "action", "phase": phase, "id": action_id, "at": time.time(), **fields}
    )


async def run_turn() -> None:
    """A turn like a real one: comment, activity, a search, a tool run, the answer."""
    await broadcast({"type": "state", "state": "processing"})
    await asyncio.sleep(0.6)
    await broadcast(
        {
            "type": "response",
            "content": "Let me find a tool that can run updates.",
            "done": True,
            "comment": True,
        }
    )
    await broadcast({"type": "activity", "text": "Searching for: run system updates…"})
    start = time.time()
    await action("start", "search:1", kind="search", title="Search: run system updates")
    await asyncio.sleep(0.8)
    await action(
        "end",
        "search:1",
        kind="search",
        status="ok",
        output="jarvis-shell-system-mcp (installed): System-level shell execution",
        started_at=start,
    )
    await broadcast({"type": "activity", "text": "Tasks dispatched; awaiting results…"})
    start = time.time()
    running["tool:7"] = start
    await action(
        "start",
        "tool:7",
        kind="tool",
        title="execute_command (jarvis-shell-system-mcp)",
        detail="pacman -Syu --noconfirm",
    )
    lines = []
    for i in range(1, 6):
        await asyncio.sleep(1.0)
        lines.append(f"( {i}/5) upgrading package-{i}")
        await action("update", "tool:7", output="\n".join(lines))
    running.pop("tool:7", None)
    await action(
        "end",
        "tool:7",
        kind="tool",
        status="ok",
        output="\n".join(lines + [":: Running post-transaction hooks..."]),
        started_at=start,
    )
    await broadcast({"type": "activity", "text": "LLM (root) is thinking…"})
    await asyncio.sleep(0.6)
    await broadcast(
        {"type": "response", "content": "Done: 5 packages were upgraded.", "done": True}
    )
    await broadcast({"type": "state", "state": "idle"})


def sample_work() -> dict:
    now = time.time()
    tasks = [
        {
            "pid": 7,
            "kind": "tool",
            "title": "execute_command (jarvis-shell-system-mcp)",
            "detail": "pacman -Syu --noconfirm",
            "running_for": round(now - since, 1),
            "output": "( 2/5) upgrading package-2",
            "request": "update my system",
        }
        for since in running.values()
    ]
    return {
        "requests": (
            [{"id": "a1", "text": "update my system", "since": now - 30}]
            if running
            else []
        ),
        "tasks": tasks,
        "thinking": 0,
        "confirmations": len(pending),
        "setups": 0,
    }


SAMPLE_STATUS = [
    {
        "title": "Language model",
        "checks": [{"name": "stub", "state": "ok", "detail": "in use (no LLM)"}],
    },
    {
        "title": "Engines",
        "checks": [
            {"name": "dispatch", "state": "off", "detail": "not in the stub"},
            {"name": "Search", "state": "warn", "detail": "keyword only"},
        ],
    },
]


async def handle(msg: dict, writer: asyncio.StreamWriter) -> None:
    kind = msg.get("type")
    if kind == "get_settings":
        out = {"type": "settings", "confirmation_mode": "smart"}
        await broadcast(out)
    elif kind == "list_sessions":
        await broadcast({"type": "session_list", "sessions": [SESSION]})
    elif kind == "switch_session":
        await broadcast(
            {"type": "session_switched", "session": SESSION, "messages": []}
        )
    elif kind == "list_confirmations":
        await broadcast({"type": "confirmation_list", "confirmations": pending})
    elif kind in ("approve_confirmation", "deny_confirmation"):
        pending[:] = [c for c in pending if c["id"] != msg.get("id")]
        await broadcast({"type": "confirmation_list", "confirmations": pending})
    elif kind == "list_clients":
        await broadcast({"type": "client_list", "clients": []})
    elif kind == "get_status":
        await broadcast({"type": "status", "full": False, "sections": SAMPLE_STATUS})
    elif kind == "list_work":
        await broadcast({"type": "work", "work": sample_work()})
    elif kind == "message":
        text = str(msg.get("content", ""))
        if text.strip().lower() == "confirm":
            pending.append(
                {
                    "id": f"c{int(time.time())}",
                    "created_at": time.time(),
                    "tool_names": ["delete_file"],
                    "tool_lines": ["delete_file path=/tmp/demo"],
                }
            )
            await broadcast({"type": "confirmation_request", "id": pending[-1]["id"]})
        elif text.strip().lower() == "run":
            asyncio.create_task(run_turn())
        else:
            asyncio.create_task(reply(text))


async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writers.append(writer)
    writer.write(b'{"type": "state", "state": "idle"}\n')
    try:
        while line := await reader.readline():
            try:
                await handle(json.loads(line), writer)
            except json.JSONDecodeError:
                continue
    finally:
        writers.remove(writer)
        writer.close()


async def main(path: str) -> None:
    if os.path.exists(path):
        os.unlink(path)
    server = await asyncio.start_unix_server(client, path)
    os.chmod(path, 0o600)
    print(f"stub daemon on {path}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1]))
    except KeyboardInterrupt:
        pass
