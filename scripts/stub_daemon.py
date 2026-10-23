"""A jarvis.sock stand-in for trying the web page without an LLM or the Rust binaries.

Speaks just enough of the socket protocol for `jarvis web`: settings, sessions,
streamed replies and confirmations. Send "confirm" in the chat to get a pending
permission request; any other text is echoed back in two chunks.

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
