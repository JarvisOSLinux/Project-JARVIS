"""A jarvis.sock stand-in for trying the web page without an LLM or the Rust binaries.

Speaks just enough of the socket protocol for `jarvis web`: settings, sessions,
streamed replies and confirmations. Send "confirm" in the chat to get a pending
permission request, and "run" to watch a turn with a comment, the activity
line and action blocks (docs/gui-protocol.md); any other text is echoed back in
two chunks. `get_status` and `list_work` answer with sample data. The session
keeps what was said and the blocks, so a reload shows them as a daemon would.
Settings and providers are kept in memory (a sample key included, so a page can
be checked for showing it), and a change from a `web:` client becomes a block,
as the daemon does.

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
history: list[dict] = []
blocks: dict[str, dict] = {}
labels: dict[asyncio.StreamWriter, str] = {}

SETTINGS = [
    {
        "key": "CONFIRMATION_MODE",
        "label": "Confirmation mode",
        "section": "Safety",
        "kind": "select",
        "value": "smart",
        "options": [
            {"value": "smart", "label": "Smart: ask when a tool can change something"},
            {"value": "ask_all", "label": "Ask before every tool"},
            {"value": "allow_all", "label": "Allow all: never ask"},
        ],
    },
    {
        "key": "OUTPUT_MODE",
        "label": "Replies",
        "section": "Voice",
        "kind": "select",
        "value": "text",
        "options": [
            {"value": "text", "label": "Shown"},
            {"value": "voice", "label": "Shown and spoken"},
        ],
    },
    {
        "key": "VOICE_SPEAK_COMMENTS",
        "label": "Speak comments",
        "section": "Voice",
        "kind": "bool",
        "value": True,
    },
    {
        "key": "WAKE_CHIME_PATH",
        "label": "Wake sound",
        "section": "Voice",
        "kind": "sound",
        "value": "/usr/share/jarvis/chime.wav",
    },
    {
        "key": "RAG_TOP_K",
        "label": "Memories per search",
        "section": "Advanced",
        "kind": "int",
        "value": 5,
        "min": 1,
        "max": 50,
    },
]
PROVIDERS = [
    {
        "name": "nvidia",
        "type": "api",
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "url": "https://integrate.api.nvidia.com",
        "api_key": "nvapi-STUB-KEY-MUST-NOT-REACH-A-PHONE",
    },
    {"name": "local", "type": "llamacpp", "model": "Qwen/Qwen3-4B-GGUF:Q4_K_M"},
]


async def changed(writer: asyncio.StreamWriter, what: str) -> None:
    label = labels.get(writer, "")
    if label.startswith("web:"):
        block = f"remote:{time.time_ns()}"
        await action(
            "start", block, kind="remote", title=f"{what} (from {label[4:]}'s phone)"
        )
        await action("end", block, kind="remote", status="ok")


async def providers_changed(writer: asyncio.StreamWriter, what: str) -> None:
    await broadcast({"type": "provider_list", "providers": PROVIDERS})
    await changed(writer, what)


def remember(msg: dict) -> None:
    """Keep the session's messages and blocks, as session_switched returns them."""
    kind = msg.get("type")
    if kind == "response" and msg.get("done") and msg.get("content"):
        said = {
            "role": "assistant",
            "content": msg["content"],
            "timestamp": time.time(),
        }
        if msg.get("comment"):
            said["comment"] = True
        history.append(said)
    elif kind == "action" and msg.get("phase") == "start":
        block = {
            "role": "action",
            "status": "running",
            "output": "",
            "started_at": msg["at"],
            "timestamp": msg["at"],
            **{k: msg.get(k) for k in ("id", "kind", "title", "detail")},
        }
        blocks[msg["id"]] = block
        history.append(block)
    elif kind == "action" and msg.get("id") in blocks:
        block = blocks[msg["id"]]
        block["output"] = msg.get("output") or block["output"]
        if msg.get("phase") == "end":
            block.update(status=msg.get("status", "ok"), at=msg["at"])
            del blocks[msg["id"]]


async def broadcast(msg: dict) -> None:
    remember(msg)
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
    history.append(
        {
            "role": "assistant",
            "content": f"You said: {text}\n\n(stub daemon, no LLM behind this)",
            "timestamp": time.time(),
        }
    )
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
    if kind == "hello":
        labels[writer] = str(msg.get("label", ""))
    elif kind == "get_settings":
        writer.write(
            (json.dumps({"type": "settings", "settings": SETTINGS}) + "\n").encode()
        )
        await writer.drain()
    elif kind == "set_setting":
        entry = next((e for e in SETTINGS if e["key"] == msg.get("key")), None)
        if entry is None:
            error = {
                "type": "config_error",
                "key": msg.get("key"),
                "message": "Unknown setting",
            }
            writer.write((json.dumps(error) + "\n").encode())
            await writer.drain()
            return
        entry["value"] = msg.get("value")
        await broadcast(
            {"type": "config_updated", "key": entry["key"], "value": entry["value"]}
        )
        await changed(writer, f"{entry['label']} set to {entry['value']}")
    elif kind == "list_providers":
        writer.write(
            (
                json.dumps({"type": "provider_list", "providers": PROVIDERS}) + "\n"
            ).encode()
        )
        await writer.drain()
    elif kind == "add_provider":
        if not msg.get("model"):
            error = {"type": "provider_error", "message": "A model is required."}
            writer.write((json.dumps(error) + "\n").encode())
            await writer.drain()
            return
        entry = {
            "name": msg.get("name") or f"{msg.get('ptype')}-{len(PROVIDERS) + 1}",
            "type": msg.get("ptype"),
            "model": msg.get("model"),
        }
        for field in ("url", "api_key", "temperature"):
            if msg.get(field) not in (None, ""):
                entry[field] = msg[field]
        PROVIDERS.append(entry)
        await providers_changed(
            writer, f"Provider {entry['type']}/{entry['model']} added"
        )
    elif kind == "edit_provider":
        entry = next((p for p in PROVIDERS if p["name"] == msg.get("name")), None)
        fields = msg.get("fields") or {}
        if entry is not None:
            for field, value in fields.items():
                entry["api_key" if field == "key" else field] = value
        await providers_changed(writer, f"Provider {msg.get('name')} changed")
    elif kind == "remove_provider":
        PROVIDERS[:] = [p for p in PROVIDERS if p["name"] != msg.get("name")]
        await providers_changed(writer, f"Provider {msg.get('name')} removed")
    elif kind == "move_provider":
        entry = next((p for p in PROVIDERS if p["name"] == msg.get("name")), None)
        if entry is not None:
            PROVIDERS.remove(entry)
            PROVIDERS.insert(max(0, int(msg.get("position", 1)) - 1), entry)
        await providers_changed(
            writer,
            f"Provider {msg.get('name')} moved to position {msg.get('position')}",
        )
    elif kind in ("start_listening", "stop_listening"):
        on = kind == "start_listening"
        await broadcast({"type": "state", "state": "listening" if on else "idle"})
        await changed(writer, f"Voice listening turned {'on' if on else 'off'}")
    elif kind == "approve_all_confirmations":
        shown = msg.get("ids")
        pending[:] = [
            c for c in pending if isinstance(shown, list) and c["id"] not in shown
        ]
        await broadcast({"type": "confirmation_list", "confirmations": pending})
    elif kind == "list_sessions":
        await broadcast({"type": "session_list", "sessions": [SESSION]})
    elif kind == "switch_session":
        await broadcast(
            {"type": "session_switched", "session": SESSION, "messages": history}
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
    elif kind in ("stop", "stop_all"):
        ended = kind == "stop_all" and bool(running)
        if ended:
            running.clear()
        text = (
            "Stopped, and ended: execute_command (jarvis-shell-system-mcp)."
            if ended
            else "Stopped."
        )
        await broadcast({"type": "ack", "for": kind, "message": text})
        await broadcast({"type": "state", "state": "idle"})
    elif kind == "stop_task":
        since = running.pop(f"tool:{msg.get('pid')}", None)
        stopped = since is not None
        if stopped:
            await action(
                "end", "tool:7", kind="tool", status="stopped", started_at=since
            )
        text = (
            "Stopped execute_command (jarvis-shell-system-mcp)."
            if stopped
            else f"No task {msg.get('pid')} is running (F4 shows what is)."
        )
        await broadcast({"type": "ack", "for": kind, "message": text})
    elif kind == "message":
        text = str(msg.get("content", ""))
        history.append({"role": "user", "content": text, "timestamp": time.time()})
        said = (
            json.dumps({"type": "user_message", "content": text, "at": time.time()})
            + "\n"
        ).encode()
        for w in writers:
            if w is not writer:
                w.write(said)
                await w.drain()
        if text.strip().lower() == "confirm":
            pending.append(
                {
                    "id": f"c{time.time_ns()}",
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
        labels.pop(writer, None)
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
