"""The TUI as a client of the daemon (#236), driven headless with Textual's
test pilot against a fake GUI socket.
"""

import asyncio
import json
import sys

import pytest

pytest.importorskip("textual")

from jarvis.config import Config
from jarvis.tui.app import JarvisTUI
from jarvis.tui.daemon_client import DaemonClient

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix-socket fixture")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "JARVIS_GUI_SOCKET", str(tmp_path / "jarvis.sock"))
    monkeypatch.setattr(Config, "JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(DaemonClient, "__init__", _fast_retry(DaemonClient.__init__))
    return tmp_path


def _fast_retry(init):
    def wrapper(self, on_message, on_connection, retry_seconds=0.05, capabilities=()):
        init(self, on_message, on_connection, retry_seconds, capabilities)

    return wrapper


class FakeDaemon:
    def __init__(self, path):
        self.path = str(path)
        self.received = []
        self.writers = []
        self.tasks = []
        self.server = None

    async def start(self):
        self.server = await asyncio.start_unix_server(self._handle, path=self.path)

    async def _handle(self, reader, writer):
        self.tasks.append(asyncio.current_task())
        self.writers.append(writer)
        while True:
            line = await reader.readline()
            if not line:
                return
            msg = json.loads(line)
            self.received.append(msg)
            reply = self.replies(msg)
            for r in reply:
                await self.push(r)

    def replies(self, msg):
        return {
            "get_status": [STATUS],
            "list_sessions": [SESSIONS],
            "get_history": [HISTORY],
            "list_confirmations": [{"type": "confirmation_list", "confirmations": []}],
        }.get(msg["type"], [])

    async def push(self, msg):
        await until(lambda: self.writers)
        for w in self.writers:
            w.write((json.dumps(msg) + "\n").encode())
            await w.drain()

    async def stop(self):
        self.server.close()
        await asyncio.sleep(0.05)
        for w in self.writers:
            w.close()
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.server.wait_closed()

    def types(self):
        return [m["type"] for m in self.received]


STATUS = {
    "type": "status",
    "session": {"id": "s1-full-id", "short_id": "s1fu", "title": "Bug fix"},
    "llm_configured": True,
    "provider": "local",
    "model": "qwen3:4b",
    "prompt_tokens": 100,
    "context_window": 1000,
}
SESSIONS = {
    "type": "session_list",
    "sessions": [
        {"id": "s1-full-id", "title": "Bug fix"},
        {"id": "s2-full-id", "title": "Refactor"},
    ],
}
HISTORY = {
    "type": "history",
    "session": None,
    "messages": [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ],
}


async def until(predicate, timeout=4.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


def log_text(app):
    return "\n".join(app._export_lines)


async def type_line(pilot, app, text):
    inp = app.query_one("#input")
    inp.value = text
    await pilot.press("enter")


@pytest.mark.asyncio
async def test_starts_offline_says_so_and_does_not_send(isolated):
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: not app.connected and "daemon offline" in app.status_text)
        assert "/start to launch it" in app.status_text
        await type_line(pilot, app, "hello")
        await until(lambda: "daemon offline" in log_text(app))
        assert "you > hello" not in log_text(app)


@pytest.mark.asyncio
async def test_attaches_and_renders_status_sessions_and_history(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: app.connected)
        await until(lambda: "model: qwen3:4b" in app.status_text)
        assert "ctx: 100/1000 (10%)" in app.status_text
        assert "session: s1fu" in app.status_text
        await until(lambda: "earlier answer" in log_text(app))
        await until(lambda: len(app.query("SessionItem")) == 2)
        assert {"get_status", "list_sessions", "get_history"} <= set(daemon.types())
        hello = next(m for m in daemon.received if m["type"] == "hello")
        assert hello["capabilities"] == ["config_form", "confirmations"]
    await daemon.stop()


@pytest.mark.asyncio
async def test_a_typed_message_goes_to_the_daemon_and_the_reply_renders(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: app.connected and app.daemon_status)
        await type_line(pilot, app, "what time is it")
        await until(
            lambda: {"type": "message", "content": "what time is it"} in daemon.received
        )
        await daemon.push({"type": "response", "content": "noon", "done": True})
        await until(lambda: "jarvis > noon" in log_text(app))
    await daemon.stop()


@pytest.mark.asyncio
async def test_activity_lines_render_dimmed(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: app.connected)
        await daemon.push(
            {"type": "activity", "text": "Installing x", "kind": "dispatch"}
        )
        await until(lambda: "... Installing x" in log_text(app))
    await daemon.stop()


@pytest.mark.asyncio
async def test_losing_the_daemon_shows_the_banner_and_it_reattaches(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: app.connected)
        await daemon.stop()
        await until(lambda: not app.connected and "daemon" in app.status_text)

        daemon = FakeDaemon(isolated / "jarvis.sock")
        await daemon.start()
        await until(lambda: app.connected)
    await daemon.stop()


@pytest.mark.asyncio
async def test_a_confirmation_request_opens_the_modal_and_y_approves(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: app.connected)
        await daemon.push(
            {
                "type": "confirmation_request",
                "id": "c1",
                "tools": ["shell"],
                "details": [{"index": 0, "tool": "shell", "command": "rm -rf /tmp/x"}],
            }
        )
        await until(lambda: "c1" in app.confirm_screens)
        await pilot.press("y")
        await until(
            lambda: {"type": "approve_confirmation", "id": "c1"} in daemon.received
        )
        assert app.confirm_screens == {}
    await daemon.stop()


@pytest.mark.asyncio
async def test_n_denies(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: app.connected)
        await daemon.push(
            {"type": "confirmation_request", "id": "c2", "details": [{"command": "ls"}]}
        )
        await until(lambda: "c2" in app.confirm_screens)
        await pilot.press("n")
        await until(
            lambda: {"type": "deny_confirmation", "id": "c2"} in daemon.received
        )
    await daemon.stop()


@pytest.mark.asyncio
async def test_a_request_resolved_elsewhere_closes_its_modal_and_sends_nothing(
    isolated,
):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: app.connected)
        await daemon.push(
            {"type": "confirmation_request", "id": "c3", "details": [{"command": "ls"}]}
        )
        await until(lambda: "c3" in app.confirm_screens)
        await daemon.push({"type": "confirmation_list", "confirmations": []})
        await until(lambda: app.confirm_screens == {})
        await asyncio.sleep(0.1)
        assert not any(
            m["type"] in ("approve_confirmation", "deny_confirmation")
            for m in daemon.received
        )
    await daemon.stop()


@pytest.mark.asyncio
async def test_start_is_local_and_reports_the_daemons_own_error(isolated, monkeypatch):
    from jarvis.tui import lifecycle

    async def failing_start(*a, **k):
        return False, "port in use"

    monkeypatch.setattr(lifecycle, "start_daemon", failing_start)
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: not app.connected)
        await type_line(pilot, app, "/start")
        await until(lambda: "port in use" in log_text(app))
        assert "you > /start" not in log_text(app)


@pytest.mark.asyncio
async def test_start_when_already_running_says_so_and_sends_nothing(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: app.connected)
        before = len(daemon.received)
        await type_line(pilot, app, "/dstart")
        await until(lambda: "already running" in log_text(app))
        await asyncio.sleep(0.1)
        assert not any(m.get("content") == "/dstart" for m in daemon.received[before:])
    await daemon.stop()


@pytest.mark.asyncio
async def test_switching_a_session_asks_the_daemon(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: len(app.query("SessionItem")) == 2)
        item = [i for i in app.query("SessionItem") if i.session_id == "s2-full-id"][0]
        from textual.widgets import ListView

        lv = app.query_one("#session-list", ListView)
        lv.index = list(lv.children).index(item)
        lv.action_select_cursor()
        await until(
            lambda: {"type": "switch_session", "id": "s2-full-id"} in daemon.received
        )
    await daemon.stop()


@pytest.mark.asyncio
async def test_attach_history_does_not_wipe_what_is_already_on_screen(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test():
        await until(lambda: app.connected and "earlier answer" in log_text(app))
        assert "Connected to the JARVIS daemon" in log_text(app)
    await daemon.stop()


@pytest.mark.asyncio
async def test_reattaching_does_not_repeat_the_transcript(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test():
        await until(lambda: "earlier answer" in log_text(app))
        await daemon.stop()
        await until(lambda: not app.connected)
        daemon = FakeDaemon(isolated / "jarvis.sock")
        await daemon.start()
        await until(lambda: app.connected and daemon.received)
        await until(lambda: "list_confirmations" in daemon.types())
        assert "get_history" not in daemon.types()
        assert log_text(app).count("earlier answer") == 1
    await daemon.stop()


@pytest.mark.asyncio
async def test_a_switched_session_replaces_the_transcript(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test():
        await until(lambda: "earlier answer" in log_text(app))
        switched = {
            **STATUS,
            "session": {"id": "s2-full-id", "short_id": "s2fu", "title": "Refactor"},
        }
        daemon.replies = lambda msg: [switched] if msg["type"] == "get_status" else []
        await daemon.push(
            {
                "type": "session_switched",
                "session": {"id": "s2-full-id", "title": "Refactor"},
                "messages": [{"role": "user", "content": "other session"}],
            }
        )
        await until(lambda: "other session" in log_text(app))
        assert "earlier answer" not in log_text(app)
        assert app.current_session_id == "s2-full-id"
    await daemon.stop()


@pytest.mark.asyncio
async def test_offline_shows_sessions_and_pending_confirmations_from_disk(
    isolated, monkeypatch
):
    from jarvis.tui import offline

    async def sessions(self, limit=50):
        return SESSIONS["sessions"]

    async def history(self, session_id):
        return [{"role": "user", "content": f"stored in {session_id}"}]

    monkeypatch.setattr(offline.OfflineStore, "sessions", sessions)
    monkeypatch.setattr(offline.OfflineStore, "history", history)
    monkeypatch.setattr(
        offline, "pending_confirmations", lambda: [{"id": "p1"}, {"id": "p2"}]
    )
    monkeypatch.setattr(offline, "queued_decisions", lambda: [{"id": "p1"}])

    app = JarvisTUI()
    async with app.run_test():
        await until(lambda: len(app.query("SessionItem")) == 2)
        await until(lambda: "2 confirmation(s) pending" in log_text(app))
        assert "1 decision(s) queued" in log_text(app)

        item = [i for i in app.query("SessionItem") if i.session_id == "s2-full-id"][0]
        from textual.widgets import ListView

        lv = app.query_one("#session-list", ListView)
        lv.index = list(lv.children).index(item)
        lv.action_select_cursor()
        await until(lambda: "stored in s2-full-id" in log_text(app))
        assert app.current_session_id == "s2-full-id"


@pytest.mark.asyncio
async def test_attaching_closes_the_offline_store(isolated, monkeypatch):
    from jarvis.tui import offline

    closed = []

    async def close(self):
        closed.append(True)

    monkeypatch.setattr(offline.OfflineStore, "close", close)
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test():
        await until(lambda: app.connected)
        await until(lambda: closed)
    await daemon.stop()


@pytest.mark.asyncio
async def test_a_repeated_session_error_is_shown_once(isolated):
    daemon = FakeDaemon(isolated / "jarvis.sock")
    await daemon.start()
    app = JarvisTUI()
    async with app.run_test():
        await until(lambda: app.connected)
        for _ in range(3):
            await daemon.push(
                {
                    "type": "session_error",
                    "message": "Sessions unavailable (memory disabled)",
                }
            )
        await until(lambda: "Sessions unavailable" in log_text(app))
        await asyncio.sleep(0.2)
        assert log_text(app).count("Sessions unavailable") == 1
    await daemon.stop()
