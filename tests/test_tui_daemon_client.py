"""The TUI's daemon client treats a missing daemon as a state, not an error.

A fake GUI socket server stands in for the daemon; nothing here starts a real
one. Unix sockets only, like the rest of the IPC tests.
"""

import asyncio
import json
import subprocess
import sys

import pytest

from jarvis.config import Config
from jarvis.tui import daemon_client
from jarvis.tui.daemon_client import DaemonClient, find_gui_endpoint, start_daemon

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix-socket fixture")


@pytest.fixture
def sock_path(tmp_path, monkeypatch):
    path = str(tmp_path / "jarvis.sock")
    monkeypatch.setattr(Config, "JARVIS_GUI_SOCKET", path)
    monkeypatch.setattr(Config, "JARVIS_DATA_DIR", str(tmp_path))
    return path


class FakeDaemon:
    def __init__(self, path):
        self.path = path
        self.received = []
        self.server = None
        self.writers = []

    async def start(self):
        self.server = await asyncio.start_unix_server(self._handle, path=self.path)

    async def _handle(self, reader, writer):
        self.writers.append(writer)
        while True:
            line = await reader.readline()
            if not line:
                return
            self.received.append(json.loads(line))

    async def push(self, msg):
        for w in self.writers:
            w.write((json.dumps(msg) + "\n").encode())
            await w.drain()

    async def stop(self):
        for w in self.writers:
            w.close()
        self.writers.clear()
        self.server.close()
        await self.server.wait_closed()


async def _until(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


def _client(messages, transitions):
    return DaemonClient(
        lambda m: messages.append(m),
        lambda up, why: transitions.append((up, why)),
        retry_seconds=0.05,
    )


def test_no_endpoint_reports_offline_once_and_keeps_retrying(sock_path):
    async def go():
        messages, transitions = [], []
        client = _client(messages, transitions)
        task = asyncio.create_task(client.run())
        await asyncio.sleep(0.3)
        client.stop()
        await task
        assert transitions == [(False, "daemon offline")]
        assert await client.send({"type": "message"}) is False

    asyncio.run(go())


def test_connects_identifies_itself_and_delivers_messages(sock_path):
    async def go():
        daemon = FakeDaemon(sock_path)
        await daemon.start()
        messages, transitions = [], []
        client = _client(messages, transitions)
        task = asyncio.create_task(client.run())
        await _until(lambda: (True, "connected") in transitions)
        await _until(
            lambda: {"type": "hello", "label": "jarvis-tui"} in daemon.received
        )

        await daemon.push({"type": "state", "state": "idle"})
        await _until(lambda: messages == [{"type": "state", "state": "idle"}])

        assert await client.send({"type": "message", "content": "hi"}) is True
        await _until(lambda: {"type": "message", "content": "hi"} in daemon.received)

        client.stop()
        await task
        await daemon.stop()

    asyncio.run(go())


def test_ping_is_answered_and_not_surfaced(sock_path):
    async def go():
        daemon = FakeDaemon(sock_path)
        await daemon.start()
        messages, transitions = [], []
        client = _client(messages, transitions)
        task = asyncio.create_task(client.run())
        await _until(lambda: (True, "connected") in transitions)
        await daemon.push({"type": "ping"})
        await _until(lambda: {"type": "pong"} in daemon.received)
        assert messages == []
        client.stop()
        await task
        await daemon.stop()

    asyncio.run(go())


def test_reattaches_when_the_daemon_appears_later_and_after_it_drops(sock_path):
    async def go():
        messages, transitions = [], []
        client = _client(messages, transitions)
        task = asyncio.create_task(client.run())
        await _until(lambda: transitions == [(False, "daemon offline")])

        daemon = FakeDaemon(sock_path)
        await daemon.start()
        await _until(lambda: (True, "connected") in transitions)

        await daemon.stop()
        await _until(lambda: (False, "daemon disconnected") in transitions)

        daemon = FakeDaemon(sock_path)
        await daemon.start()
        await _until(lambda: transitions.count((True, "connected")) == 2)

        client.stop()
        await task
        await daemon.stop()

    asyncio.run(go())


def test_async_callbacks_are_awaited(sock_path):
    async def go():
        seen = []

        async def on_message(msg):
            seen.append(msg)

        async def on_connection(up, why):
            seen.append((up, why))

        daemon = FakeDaemon(sock_path)
        await daemon.start()
        client = DaemonClient(on_message, on_connection, retry_seconds=0.05)
        task = asyncio.create_task(client.run())
        await _until(lambda: (True, "connected") in seen)
        client.stop()
        await task
        await daemon.stop()

    asyncio.run(go())


def test_find_gui_endpoint(sock_path, tmp_path):
    assert find_gui_endpoint() is None
    (tmp_path / "jarvis.sock").write_text("")
    assert find_gui_endpoint() == sock_path


def test_start_daemon_returns_the_daemons_own_output_when_it_dies(sock_path):
    def spawn(log_file):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('port in use\\n'); sys.exit(3)",
            ],
            stdout=log_file,
            stderr=log_file,
        )

    ok, detail = asyncio.run(start_daemon(timeout=5, spawn=spawn))
    assert ok is False
    assert detail == "port in use"


def test_start_daemon_waits_for_the_endpoint(sock_path, tmp_path):
    code = (
        "import time, pathlib; time.sleep(0.3);"
        f"pathlib.Path({sock_path!r}).write_text(''); time.sleep(5)"
    )

    def spawn(log_file):
        return subprocess.Popen(
            [sys.executable, "-c", code], stdout=log_file, stderr=log_file
        )

    ok, detail = asyncio.run(start_daemon(timeout=5, spawn=spawn))
    assert (ok, detail) == (True, "")


def test_start_daemon_times_out_with_a_reason(sock_path):
    def spawn(log_file):
        return subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdout=log_file,
            stderr=log_file,
        )

    ok, detail = asyncio.run(start_daemon(timeout=0.6, spawn=spawn))
    assert ok is False
    assert "did not open its socket" in detail


def test_start_daemon_skips_spawning_when_a_daemon_is_already_up(sock_path, tmp_path):
    (tmp_path / "jarvis.sock").write_text("")

    def spawn(log_file):
        raise AssertionError("must not spawn")

    assert asyncio.run(start_daemon(timeout=1, spawn=spawn)) == (True, "")
