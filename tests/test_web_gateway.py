"""Tests for `jarvis web` (jarvis/server/web_gateway.py): the allowlist and
role policy, the request checks, and a full relay against a fake daemon socket.
"""

import asyncio
import json
import os

import pytest

pytest.importorskip("websockets")

from websockets.asyncio.client import connect  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402
from websockets.exceptions import ConnectionClosed, InvalidStatus  # noqa: E402

from jarvis.server import web_gateway as wg  # noqa: E402

HOST_HEADER = "laptop.tail1234.ts.net"
ORIGIN = f"https://{HOST_HEADER}"


def _headers(login="toufic@example.com", **extra):
    h = {"Host": HOST_HEADER, "Origin": ORIGIN}
    if login:
        h["Tailscale-User-Login"] = login
    h.update(extra)
    return h


@pytest.mark.unit
class TestPolicy:
    def test_guest_can_chat_but_not_approve(self):
        assert wg.sendable("guest", {"type": "message", "content": "hi"})
        assert not wg.sendable("guest", {"type": "approve_confirmation", "id": "x"})

    def test_trusted_approves_owner_manages_sessions(self):
        assert wg.sendable("trusted", {"type": "approve_confirmation", "id": "x"})
        assert not wg.sendable("trusted", {"type": "delete_session", "id": "x"})
        assert wg.sendable("owner", {"type": "delete_session", "id": "x"})

    @pytest.mark.parametrize(
        "kind",
        [
            "confirmation_response",
            "shutdown_request",
            "setup_completed",
            "cancel_setup",
        ],
    )
    def test_no_role_may_send_what_belongs_to_the_laptop(self, kind):
        for role in wg.ROLES:
            assert not wg.sendable(role, {"type": kind})

    @pytest.mark.parametrize(
        "kind",
        [
            "approve_all_confirmations",
            "get_settings",
            "set_setting",
            "list_providers",
            "add_provider",
            "edit_provider",
            "remove_provider",
            "move_provider",
            "start_listening",
            "stop_listening",
        ],
    )
    def test_only_the_owners_phone_is_jarvis_app(self, kind):
        assert wg.sendable("owner", {"type": kind})
        assert not wg.sendable("trusted", {"type": kind})
        assert not wg.sendable("guest", {"type": kind})

    def test_approvals_cannot_be_turned_off_from_a_phone(self):
        off = {"type": "set_setting", "key": "CONFIRMATION_MODE", "value": "allow_all"}
        assert "laptop" in wg.refused(off)
        assert wg.refused({**off, "value": "ask_all"}) is None
        assert wg.refused({**off, "value": "smart"}) is None

    def test_the_wake_sound_can_only_be_restored_from_a_phone(self):
        chime = {"type": "set_setting", "key": "WAKE_CHIME_PATH"}
        assert wg.refused({**chime, "value": "/etc/shadow"})
        assert wg.refused({**chime, "value": ""}) is None
        assert (
            wg.refused({"type": "set_setting", "key": "OUTPUT_MODE", "value": "voice"})
            is None
        )

    @pytest.mark.parametrize(
        "value", ["allow_all ", " allow_all", "allow_all\n", "\tallow_all\t"]
    )
    def test_allow_all_is_refused_as_the_daemon_reads_it(self, value):
        from jarvis.core import settings

        mode = settings.find("CONFIRMATION_MODE")
        assert settings.parse(mode, value) == ("allow_all", None), "the daemon's view"
        msg = {"type": "set_setting", "key": "CONFIRMATION_MODE", "value": value}
        assert "laptop" in wg.refused(msg)

    def test_a_mode_the_daemon_refuses_is_left_to_it(self):
        msg = {"type": "set_setting", "key": "CONFIRMATION_MODE", "value": "ALLOW_ALL"}
        assert wg.refused(msg) is None
        from jarvis.core import settings

        _, error = settings.parse(settings.find("CONFIRMATION_MODE"), "ALLOW_ALL")
        assert error, "the daemon answers config_error itself"

    def test_a_blank_wake_sound_restores_the_default(self):
        chime = {"type": "set_setting", "key": "WAKE_CHIME_PATH"}
        assert wg.refused({**chime, "value": "  "}) is None
        assert wg.refused({**chime, "value": None}) is None
        assert wg.refused({**chime, "value": " /home/x/beep.wav"})

    @pytest.mark.parametrize(
        "content, acts_as",
        [
            ("/delete 5bd5", "delete_session"),
            ("  /DELETE 5bd5", "delete_session"),
            ("/rename 5bd5 New name", "rename_session"),
            ("/switch 5bd5", "switch_session"),
            ("/new Groceries", "create_session"),
            ("/sessions", "list_sessions"),
            ("/stop", "stop"),
            ("/stop now please", "stop"),
            ("/stop all", "stop_all"),
            ("/STOP   ALL", "stop_all"),
            ("/stop 7", "stop_task"),
            ("/context", "message"),
            ("/whatever", "message"),
            ("hello /delete 5bd5", "message"),
            ("", "message"),
        ],
    )
    def test_a_slash_command_in_the_chat_is_what_it_does(self, content, acts_as):
        assert wg.acts_as({"type": "message", "content": content}) == acts_as

    def test_a_guest_cannot_type_what_it_may_not_send(self):
        for line in ("/delete 5bd5", "/rename 5bd5 x", "/stop all", "/stop 7", "/new"):
            msg = {"type": "message", "content": line}
            assert not wg.sendable("guest", msg), line
            assert wg.sendable("owner", msg), line
        assert wg.sendable("guest", {"type": "message", "content": "/stop"})
        assert wg.sendable("guest", {"type": "message", "content": "/sessions"})
        assert wg.sendable("trusted", {"type": "message", "content": "/stop all"})
        assert not wg.sendable("trusted", {"type": "message", "content": "/delete x"})

    def test_the_daemons_parsers_read_a_line_as_acts_as_does(self):
        """acts_as stands in for stop.handle_command and handle_slash_command:
        a line the daemon would run as a command must map to that command."""
        from unittest.mock import MagicMock

        from jarvis.runtime import session_commands, stop

        for line in ("/delete x", "/rename x y", "/switch x", "/new", "/sessions"):
            app = MagicMock()
            app.sessions.find.return_value = (None, "no such session")
            assert session_commands.handle_slash_command(app, line), line
            assert wg.acts_as({"type": "message", "content": line}) != "message"
        app = MagicMock()
        assert not session_commands.handle_slash_command(app, "/whatever")
        assert wg.acts_as({"type": "message", "content": "/whatever"}) == "message"
        assert asyncio.run(stop.handle_command(None, None, "/stopx")) is None
        assert wg.acts_as({"type": "message", "content": "/stopx"}) == "message"

    def test_a_message_must_be_text(self):
        for content in (5, None, ["/delete"], {"a": 1}):
            assert not wg.sendable("owner", {"type": "message", "content": content})

    def test_approve_all_names_what_it_approves(self):
        bare = {"type": "approve_all_confirmations"}
        assert wg.refused(bare)
        assert wg.refused({**bare, "ids": "c1"})
        assert wg.refused({**bare, "ids": ["c1", 2]})
        assert wg.refused({**bare, "ids": ["c1", "c2"]}) is None
        assert wg.refused({**bare, "ids": []}) is None

    def test_a_phone_never_sees_a_providers_headers(self):
        listed = {
            "type": "provider_list",
            "providers": [
                {
                    "name": "x",
                    "type": "api",
                    "model": "m",
                    "headers": {"x-api-key": "hidden-secret"},
                }
            ],
        }
        shown = wg.shown_to(listed, "owner")
        assert "hidden-secret" not in json.dumps(shown)
        assert "headers" not in shown["providers"][0]

    def test_a_guest_gets_a_request_without_its_parameters(self):
        request = {
            "type": "confirmation_request",
            "id": "c1",
            "tools": ["shell.write_file"],
            "details": [{"index": 0, "params": {"content": "secret body"}}],
            "timeout": 0,
        }
        assert wg.shown_to(request, "guest") == {
            "type": "confirmation_request",
            "id": "c1",
            "tools": ["shell.write_file"],
            "timeout": 0,
        }
        assert wg.shown_to(request, "trusted") == request

    def test_a_guest_sees_which_tools_wait_not_their_commands(self):
        listed = {
            "type": "confirmation_list",
            "confirmations": [
                {
                    "id": "c1",
                    "tool_names": ["shell.write_file"],
                    "tool_lines": ["shell.write_file: path=~/.aws/credentials"],
                    "goal_description": "rotate keys",
                    "created_at": 1.0,
                }
            ],
        }
        assert wg.shown_to(listed, "guest")["confirmations"] == [
            {"id": "c1", "tool_names": ["shell.write_file"], "created_at": 1.0}
        ]
        assert wg.shown_to(listed, "trusted") == listed

    def test_the_sudo_password_popup_is_for_those_who_approve(self):
        request = {"type": "password_request", "id": "r1", "command": "c"}
        done = {"type": "password_request_done", "id": "r1", "outcome": "answered"}
        for role in ("trusted", "owner"):
            assert wg.shown_to(request, role) == request
            assert wg.shown_to(done, role) == done
            for kind in ("password_response", "password_cancel"):
                assert wg.sendable(role, {"type": kind, "id": "r1"})
        assert wg.shown_to(request, "guest") is None
        assert not wg.sendable("guest", {"type": "password_response", "id": "r1"})

    def test_the_owners_phone_may_turn_the_popup_on(self):
        """Toufic, 2026-11-11: the laptop stays at home. The popup changes where
        sudo asks, not whether a password is needed."""
        msg = {"type": "set_setting", "key": "SUDO_PASSWORD_POPUP"}
        for value in (True, "on", False):
            assert wg.refused({**msg, "value": value}) is None
        assert wg.sendable("owner", {**msg, "value": True})
        assert not wg.sendable("trusted", {**msg, "value": True})

    def test_those_who_answer_may_ask_what_waits(self):
        ask = {"type": "list_password_requests"}
        assert wg.sendable("trusted", ask) and wg.sendable("owner", ask)
        assert not wg.sendable("guest", ask)

    def test_a_guest_gets_sessions_without_their_summaries(self):
        session = {"id": "s1", "title": "t", "summary": "AWS keys", "metadata": {}}
        listed = {"type": "session_list", "sessions": [session]}
        assert wg.shown_to(listed, "guest")["sessions"] == [{"id": "s1", "title": "t"}]
        assert wg.shown_to(listed, "owner") == listed
        switched = {"type": "session_switched", "session": session, "messages": []}
        assert "summary" not in wg.shown_to(switched, "guest")["session"]
        assert wg.shown_to(switched, "trusted")["session"] == session

    def test_a_phone_never_sees_a_providers_key(self):
        listed = {
            "type": "provider_list",
            "providers": [
                {
                    "name": "nvidia",
                    "type": "api",
                    "model": "m",
                    "api_key": "nvapi-secret",
                },
                {"name": "local", "type": "llamacpp", "model": "q"},
            ],
        }
        shown = wg.shown_to(listed, "owner")
        assert "nvapi-secret" not in json.dumps(shown)
        assert shown["providers"][0] == {
            "name": "nvidia",
            "type": "api",
            "model": "m",
            "api_key_set": True,
        }
        assert shown["providers"][1]["api_key_set"] is False
        assert (
            listed["providers"][0]["api_key"] == "nvapi-secret"
        ), "not changed in place"
        assert wg.shown_to(listed, "trusted") is None

    def test_status_and_work_need_trust(self):
        for kind in ("get_status", "list_work"):
            assert not wg.sendable("guest", {"type": kind})
            assert wg.sendable("trusted", {"type": kind})
            assert wg.sendable("owner", {"type": kind})

    def test_activity_for_everyone_actions_for_trusted(self):
        # Action blocks carry tool parameters and raw output.
        activity = {"type": "activity", "text": "Searching…", "kind": "dispatch"}
        said = {"type": "user_message", "content": "update my system", "at": 1.0}
        for role in wg.ROLES:
            assert wg.relayable(activity, role)
            assert wg.relayable(said, role)
        for kind in ("action", "status", "work"):
            assert not wg.relayable({"type": kind}, "guest")
            assert wg.relayable({"type": kind}, "trusted")
            assert wg.relayable({"type": kind}, "owner")
        assert not wg.relayable({"type": "action"}, None)

    def test_malformed_messages_are_refused(self):
        assert not wg.sendable("owner", [])
        assert not wg.sendable("owner", {"type": ["message"]})
        assert not wg.sendable("owner", {})

    def test_provider_and_settings_broadcasts_reach_only_the_owner(self):
        for kind in ("provider_list", "settings", "config_updated", "config_error"):
            assert not wg.relayable({"type": kind})
            assert not wg.relayable({"type": kind}, "trusted")
            assert wg.relayable({"type": kind}, "owner")
        assert wg.relayable({"type": "response", "content": "x", "done": True})

    def test_shutdown_snapshot_is_cut_to_the_state(self):
        notice = wg.shutdown_notice(
            {"type": "DAEMON_SHUTDOWN", "state": "idle", "goals": ["secret"]}
        )
        assert notice == {"type": "DAEMON_SHUTDOWN", "state": "idle"}

    def test_a_guest_gets_a_session_without_its_blocks(self):
        history = {
            "type": "session_switched",
            "session": {"id": "s1"},
            "messages": [
                {"role": "user", "content": "update", "timestamp": 1.0},
                {"role": "action", "id": "tool:1", "output": "secret", "timestamp": 2},
            ],
        }
        assert wg.shown_to(history, "guest")["messages"] == [history["messages"][0]]
        assert wg.shown_to(history, "owner") == history
        assert wg.shown_to(history, None) is None

    def test_shown_to_cuts_a_shutdown_and_drops_what_a_role_may_not_see(self):
        shutdown = {"type": "DAEMON_SHUTDOWN", "state": "idle", "goals": ["secret"]}
        assert wg.shown_to(shutdown, "guest") == {
            "type": "DAEMON_SHUTDOWN",
            "state": "idle",
        }
        assert wg.shown_to({"type": "action"}, "guest") is None
        assert wg.shown_to({"type": "settings"}, "trusted") is None
        assert wg.shown_to(["not", "a", "dict"], "owner") is None

    def test_allow_all_switch_is_unsafe(self):
        assert wg.unsafe_mode_change(
            {"type": "config_updated", "key": "CONFIRMATION_MODE", "value": "allow_all"}
        )
        assert not wg.unsafe_mode_change(
            {"type": "config_updated", "key": "CONFIRMATION_MODE", "value": "smart"}
        )


@pytest.mark.unit
class TestRequestChecks:
    def test_identity_header_required(self):
        assert wg.request_login(_headers(login=None), None)[0] is None
        assert wg.request_login(_headers(), None)[0] == "toufic@example.com"

    def test_funnel_requests_refused(self):
        h = _headers(**{"Tailscale-Funnel-Request": "?1"})
        assert wg.request_login(h, None)[0] is None

    def test_host_must_be_in_tailnet(self, monkeypatch):
        monkeypatch.setattr(wg.Config, "WEB_HOST_SUFFIX", ".ts.net")
        assert wg.host_ok({"Host": HOST_HEADER}, None)
        assert not wg.host_ok({"Host": "evil.example.com"}, None)
        assert not wg.host_ok({"Host": "localhost:8318"}, None)
        assert wg.host_ok({"Host": "localhost:8318"}, "dev@example.com")

    def test_the_dev_login_is_for_this_computer_only(self, monkeypatch):
        monkeypatch.setattr(wg.Config, "WEB_HOST_SUFFIX", ".ts.net")
        dev = "dev@localhost"
        assert wg.host_ok({"Host": "127.0.0.1:8318"}, dev)
        assert not wg.host_ok({"Host": HOST_HEADER}, dev)
        assert wg.request_login({"Host": "localhost"}, dev) == (dev, "")
        assert wg.request_login(_headers(), dev)[0] is None
        # A tagged tailnet node gets no identity headers from serve, but serve
        # always says it forwarded the request.
        for proxy in ("X-Forwarded-For", "x-forwarded-proto", "Forwarded"):
            assert (
                wg.request_login({"Host": "localhost", proxy: "100.1.2.3"}, dev)[0]
                is None
            )
        assert (
            wg.request_login(_headers(login=None, **{"Tailscale-User-Name": "T"}), dev)[
                0
            ]
            is None
        )

    def test_origin_must_equal_host(self):
        assert wg.origin_ok({"Host": HOST_HEADER, "Origin": ORIGIN})
        assert not wg.origin_ok(
            {"Host": HOST_HEADER, "Origin": "https://evil.example.com"}
        )
        assert not wg.origin_ok({"Host": HOST_HEADER})

    def test_rate_limit(self):
        limiter = wg.RateLimit(limit=2, window=10)
        assert limiter.allow(0) and limiter.allow(1)
        assert not limiter.allow(2)
        assert limiter.allow(11)


@pytest.mark.unit
class TestAllowlistAndAudit:
    def test_roundtrip_is_case_insensitive_and_private(self, tmp_path):
        allow = wg.Allowlist(str(tmp_path / "web_allowlist.json"))
        allow.set("Toufic@Example.com", "owner")
        assert allow.role_for("toufic@example.com") == "owner"
        assert oct(os.stat(allow.path).st_mode & 0o777) == "0o600"
        assert allow.remove("TOUFIC@example.com")
        assert allow.role_for("toufic@example.com") is None

    def test_unknown_role_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            wg.Allowlist(str(tmp_path / "a.json")).set("a@b.c", "root")

    def test_garbage_file_allows_nobody(self, tmp_path):
        path = tmp_path / "a.json"
        path.write_text("not json")
        assert wg.Allowlist(str(path)).load() == {}

    def test_audit_records_account_and_scrubs_secrets(self, tmp_path):
        log = wg.AuditLog(str(tmp_path / "audit.jsonl"))
        log.record(
            "yakup@example.com",
            "trusted",
            {"type": "message", "content": "token ghp_" + "a" * 36},
        )
        log.record(
            "yakup@example.com", "trusted", {"type": "approve_confirmation", "id": "c1"}
        )
        lines = [json.loads(x) for x in open(log.path)]
        assert lines[0]["login"] == "yakup@example.com"
        assert "ghp_" not in lines[0]["content"]
        assert lines[1]["id"] == "c1"

    def test_audit_names_changes_but_never_keys(self, tmp_path):
        log = wg.AuditLog(str(tmp_path / "audit.jsonl"))
        log.record(
            "y@example.com",
            "owner",
            {"type": "set_setting", "key": "OUTPUT_MODE", "value": "voice"},
        )
        log.record(
            "y@example.com",
            "owner",
            {
                "type": "add_provider",
                "ptype": "api",
                "model": "m",
                "url": "https://integrate.api.nvidia.com",
                "api_key": "nvapi-" + "k" * 64,
            },
        )
        log.record(
            "y@example.com",
            "owner",
            {"type": "edit_provider", "name": "nvidia", "fields": {"key": "sk-x"}},
        )
        text = open(log.path).read()
        assert "nvapi-" not in text and "sk-x" not in text
        setting, added, edited = [json.loads(x) for x in text.splitlines()]
        assert (setting["key"], setting["value"]) == ("OUTPUT_MODE", "voice")
        assert added["provider"]["model"] == "m" and added["key_changed"] is True
        assert edited["provider"] == {"name": "nvidia"} and edited["key_changed"]


class FakeDaemon:
    """A jarvis.sock stand-in: records what it is sent, plays back what it is told."""

    def __init__(self, path, mode="smart"):
        self.path = path
        self.mode = mode
        self.received = []
        self.writers = []

    async def start(self):
        self.server = await asyncio.start_unix_server(self._client, self.path)

    async def _client(self, reader, writer):
        self.writers.append(writer)
        writer.write(b'{"type": "state", "state": "idle"}\n')
        while line := await reader.readline():
            msg = json.loads(line)
            self.received.append(msg)
            if msg["type"] == "get_settings":
                listed = [] if self.mode is None else [self.mode]
                out = {
                    "type": "settings",
                    "settings": [
                        {"key": "CONFIRMATION_MODE", "kind": "select", "value": m}
                        for m in listed
                    ],
                }
                writer.write((json.dumps(out) + "\n").encode())
                await writer.drain()

    async def push(self, msg):
        for w in self.writers:
            w.write((json.dumps(msg) + "\n").encode())
            await w.drain()

    async def stop(self):
        self.server.close()
        for w in self.writers:
            w.close()


def _wire(url, login="toufic@example.com", **extra):
    """Headers for a real client: it adds Host itself, so Origin must match it."""
    host = url.split("//")[1].split("/")[0]
    h = {"Origin": f"http://{host}"}
    if login:
        h["Tailscale-User-Login"] = login
    h.update(extra)
    return h


@pytest.fixture(autouse=True)
def _loopback_suffix(monkeypatch):
    monkeypatch.setattr(wg.Config, "WEB_HOST_SUFFIX", "127.0.0.1")


async def _boot(tmp_path, roles, mode="smart", idle=900):
    daemon = FakeDaemon(str(tmp_path / "jarvis.sock"), mode)
    await daemon.start()
    allow = wg.Allowlist(str(tmp_path / "allow.json"))
    for login, role in roles.items():
        allow.set(login, role)
    gateway = wg.Gateway(
        allow, wg.AuditLog(str(tmp_path / "audit.jsonl")), daemon.path, idle
    )
    server = await serve(
        gateway.handle, "127.0.0.1", 0, process_request=gateway.process_request
    )
    port = server.sockets[0].getsockname()[1]
    return daemon, server, f"ws://127.0.0.1:{port}/ws", gateway


async def _recv_types(ws, until, limit=10):
    seen = []
    for _ in range(limit):
        msg = json.loads(await asyncio.wait_for(ws.recv(), 3))
        seen.append(msg)
        if msg["type"] == until:
            return seen
    raise AssertionError(f"never saw {until}: {seen}")


@pytest.mark.unit
@pytest.mark.asyncio
class TestRelay:
    async def test_chat_roundtrip_with_label_and_audit(self, tmp_path):
        daemon, server, url, _ = await _boot(tmp_path, {"toufic@example.com": "owner"})
        try:
            async with connect(url, additional_headers=_wire(url)) as ws:
                hello = json.loads(await ws.recv())
                assert hello["type"] == "gateway" and hello["role"] == "owner"
                await ws.send(json.dumps({"type": "message", "content": "hello"}))
                await asyncio.sleep(0.2)
                await daemon.push({"type": "response", "content": "hi", "done": True})
                seen = await _recv_types(ws, "response")
                assert seen[-1]["content"] == "hi"
            assert {
                "type": "hello",
                "label": "web:toufic@example.com",
                "subscribe": [
                    "activity",
                    "action",
                    "user_message",
                    "password_request",
                    "password_request_done",
                ],
            } in daemon.received
            assert {"type": "message", "content": "hello"} in daemon.received
            audit = [json.loads(x) for x in open(tmp_path / "audit.jsonl")]
            assert audit[0]["login"] == "toufic@example.com"
        finally:
            server.close()
            await daemon.stop()

    async def test_the_daemons_greeting_state_reaches_the_phone(self, tmp_path):
        # The daemon greets each client with its state before the settings
        # reply; the handshake swallowed it and the page said "Offline".
        daemon, server, url, _ = await _boot(tmp_path, {"t@example.com": "trusted"})
        try:
            async with connect(
                url, additional_headers=_wire(url, "t@example.com")
            ) as ws:
                first = json.loads(await ws.recv())
                second = json.loads(await asyncio.wait_for(ws.recv(), 3))
            assert first["type"] == "gateway"
            assert second == {"type": "state", "state": "idle"}
        finally:
            server.close()
            await daemon.stop()

    async def test_guest_cannot_approve_and_nothing_reaches_daemon(self, tmp_path):
        daemon, server, url, _ = await _boot(tmp_path, {"g@example.com": "guest"})
        try:
            async with connect(
                url, additional_headers=_wire(url, "g@example.com")
            ) as ws:
                await ws.recv()
                await ws.send(json.dumps({"type": "approve_confirmation", "id": "c1"}))
                seen = await _recv_types(ws, "error")
                assert "not available" in seen[-1]["message"]
            assert not [
                m for m in daemon.received if m["type"] == "approve_confirmation"
            ]
        finally:
            server.close()
            await daemon.stop()

    async def test_a_guests_slash_command_never_reaches_the_daemon(self, tmp_path):
        daemon, server, url, _ = await _boot(tmp_path, {"g@example.com": "guest"})
        try:
            async with connect(
                url, additional_headers=_wire(url, "g@example.com")
            ) as ws:
                await ws.recv()
                for line in ("/delete 5bd5", "/stop all"):
                    await ws.send(json.dumps({"type": "message", "content": line}))
                    seen = await _recv_types(ws, "error")
                    assert "is not available to a guest" in seen[-1]["message"]
                await ws.send(json.dumps({"type": "message", "content": "hi"}))
                await asyncio.sleep(0.2)
            sent = [m.get("content") for m in daemon.received if m["type"] == "message"]
            assert sent == ["hi"]
        finally:
            server.close()
            await daemon.stop()

    async def test_a_refused_chat_line_puts_the_phone_back_to_the_last_state(
        self, tmp_path
    ):
        daemon, server, url, _ = await _boot(tmp_path, {"g@example.com": "guest"})
        try:
            async with connect(
                url, additional_headers=_wire(url, "g@example.com")
            ) as ws:
                await ws.recv()
                await daemon.push({"type": "state", "state": "idle"})
                await _recv_types(ws, "state")
                await ws.send(json.dumps({"type": "message", "content": "/new x"}))
                seen = await _recv_types(ws, "error")
                assert "not available to a guest" in seen[-1]["message"]
                after = json.loads(await asyncio.wait_for(ws.recv(), 3))
                assert after == {"type": "state", "state": "idle"}
        finally:
            server.close()
            await daemon.stop()

    async def test_a_password_answer_is_audited_without_the_password(self, tmp_path):
        daemon, server, url, _ = await _boot(tmp_path, {"t@example.com": "trusted"})
        answer = {"type": "password_response", "id": "r1", "password": "hunter2"}
        try:
            async with connect(
                url, additional_headers=_wire(url, "t@example.com")
            ) as ws:
                await ws.recv()
                await ws.send(json.dumps(answer))
                await asyncio.sleep(0.2)
            assert answer in daemon.received, "the password reaches the daemon"
            audit = open(tmp_path / "audit.jsonl").read()
            assert "hunter2" not in audit
            assert json.loads(audit.splitlines()[-1])["id"] == "r1"
        finally:
            server.close()
            await daemon.stop()

    async def test_approve_all_without_ids_never_reaches_the_daemon(self, tmp_path):
        daemon, server, url, _ = await _boot(tmp_path, {"o@example.com": "owner"})
        try:
            async with connect(
                url, additional_headers=_wire(url, "o@example.com")
            ) as ws:
                await ws.recv()
                await ws.send(json.dumps({"type": "approve_all_confirmations"}))
                seen = await _recv_types(ws, "error")
                assert "must name" in seen[-1]["message"]
                ok = {"type": "approve_all_confirmations", "ids": ["c1"]}
                await ws.send(json.dumps(ok))
                await asyncio.sleep(0.2)
            alls = [m for m in daemon.received if m["type"] == ok["type"]]
            assert alls == [ok]
            audited = [
                json.loads(line)
                for line in open(tmp_path / "audit.jsonl")
                if '"approve_all_confirmations"' in line
            ]
            assert [a["ids"] for a in audited] == [["c1"]]
        finally:
            server.close()
            await daemon.stop()

    async def test_the_owner_adds_a_provider_and_gets_the_list_without_keys(
        self, tmp_path
    ):
        daemon, server, url, _ = await _boot(tmp_path, {"o@example.com": "owner"})
        add = {
            "type": "add_provider",
            "ptype": "api",
            "model": "m",
            "url": "https://integrate.api.nvidia.com",
            "api_key": "nvapi-typed-on-the-phone",
        }
        try:
            async with connect(
                url, additional_headers=_wire(url, "o@example.com")
            ) as ws:
                await ws.recv()
                await ws.send(json.dumps(add))
                await asyncio.sleep(0.2)
                assert add in daemon.received, "the key reaches the daemon"
                await daemon.push(
                    {
                        "type": "provider_list",
                        "providers": [{"name": "nvidia", "api_key": add["api_key"]}],
                    }
                )
                seen = await _recv_types(ws, "provider_list")
                assert seen[-1]["providers"] == [
                    {"name": "nvidia", "api_key_set": True}
                ]
            assert add["api_key"] not in open(tmp_path / "audit.jsonl").read()
        finally:
            server.close()
            await daemon.stop()

    async def test_allow_all_from_the_owners_phone_never_reaches_the_daemon(
        self, tmp_path
    ):
        daemon, server, url, _ = await _boot(tmp_path, {"o@example.com": "owner"})
        try:
            async with connect(
                url, additional_headers=_wire(url, "o@example.com")
            ) as ws:
                await ws.recv()
                await ws.send(
                    json.dumps(
                        {
                            "type": "set_setting",
                            "key": "CONFIRMATION_MODE",
                            "value": "allow_all",
                        }
                    )
                )
                seen = await _recv_types(ws, "config_error")
                assert seen[-1]["key"] == "CONFIRMATION_MODE"
            assert all(m["type"] != "set_setting" for m in daemon.received)
        finally:
            server.close()
            await daemon.stop()

    async def test_provider_list_broadcast_is_not_forwarded(self, tmp_path):
        daemon, server, url, _ = await _boot(tmp_path, {"t@example.com": "trusted"})
        try:
            async with connect(
                url, additional_headers=_wire(url, "t@example.com")
            ) as ws:
                await ws.recv()
                await asyncio.sleep(0.2)
                await daemon.push(
                    {"type": "provider_list", "providers": [{"api_key": "sk-secret"}]}
                )
                await daemon.push({"type": "state", "state": "processing"})
                seen = await _recv_types(ws, "state")
                assert all(m["type"] != "provider_list" for m in seen)
        finally:
            server.close()
            await daemon.stop()

    async def test_a_guest_sees_activity_but_not_actions(self, tmp_path):
        daemon, server, url, gateway = await _boot(
            tmp_path, {"g@example.com": "guest", "t@example.com": "trusted"}
        )
        try:
            async with (
                connect(url, additional_headers=_wire(url, "g@example.com")) as guest,
                connect(url, additional_headers=_wire(url, "t@example.com")) as trusted,
            ):
                await guest.recv()
                await trusted.recv()
                await asyncio.sleep(0.2)
                await daemon.push({"type": "action", "phase": "start", "id": "tool:1"})
                await daemon.push({"type": "activity", "text": "Running…"})
                guest_saw = await _recv_types(guest, "activity")
                trusted_saw = await _recv_types(trusted, "activity")
                assert [m["type"] for m in guest_saw if m["type"] != "state"] == [
                    "activity"
                ]
                assert [m["type"] for m in trusted_saw if m["type"] != "state"] == [
                    "action",
                    "activity",
                ]
                # Demoted while connected: actions stop at the phone's next message.
                gateway.allowlist.set("t@example.com", "guest")
                await trusted.send(json.dumps({"type": "ping"}))
                await asyncio.sleep(0.2)
                await daemon.push({"type": "action", "phase": "end", "id": "tool:1"})
                await daemon.push({"type": "activity", "text": "Done"})
                later = await _recv_types(trusted, "activity")
                assert all(m["type"] != "action" for m in later)
        finally:
            server.close()
            await daemon.stop()

    async def test_refused_while_host_is_allow_all(self, tmp_path):
        daemon, server, url, _ = await _boot(
            tmp_path, {"t@example.com": "owner"}, mode="allow_all"
        )
        try:
            async with connect(
                url, additional_headers=_wire(url, "t@example.com")
            ) as ws:
                with pytest.raises(ConnectionClosed) as err:
                    await ws.recv()
                assert err.value.rcvd.code == wg.CLOSE_UNSAFE
        finally:
            server.close()
            await daemon.stop()

    async def test_refused_when_the_host_does_not_say_its_mode(self, tmp_path):
        daemon, server, url, _ = await _boot(
            tmp_path, {"t@example.com": "owner"}, mode=None
        )
        try:
            async with connect(
                url, additional_headers=_wire(url, "t@example.com")
            ) as ws:
                with pytest.raises(ConnectionClosed) as err:
                    await ws.recv()
                assert err.value.rcvd.code == wg.CLOSE_UNSAFE
        finally:
            server.close()
            await daemon.stop()

    async def test_session_ends_when_host_switches_to_allow_all(self, tmp_path):
        daemon, server, url, _ = await _boot(tmp_path, {"t@example.com": "owner"})
        try:
            async with connect(
                url, additional_headers=_wire(url, "t@example.com")
            ) as ws:
                await ws.recv()
                await asyncio.sleep(0.2)
                await daemon.push(
                    {
                        "type": "config_updated",
                        "key": "CONFIRMATION_MODE",
                        "value": "allow_all",
                    }
                )
                with pytest.raises(ConnectionClosed) as err:
                    while True:
                        await asyncio.wait_for(ws.recv(), 3)
                assert err.value.rcvd.code == wg.CLOSE_UNSAFE
        finally:
            server.close()
            await daemon.stop()

    async def test_revoked_account_is_cut_off_at_its_next_message(self, tmp_path):
        daemon, server, url, gateway = await _boot(tmp_path, {"t@example.com": "owner"})
        try:
            async with connect(
                url, additional_headers=_wire(url, "t@example.com")
            ) as ws:
                await ws.recv()
                gateway.allowlist.remove("t@example.com")
                await ws.send(json.dumps({"type": "message", "content": "still here?"}))
                with pytest.raises(ConnectionClosed) as err:
                    while True:
                        await asyncio.wait_for(ws.recv(), 3)
                assert err.value.rcvd.code == wg.CLOSE_REFUSED
            assert not [m for m in daemon.received if m["type"] == "message"]
        finally:
            server.close()
            await daemon.stop()

    async def test_idle_session_is_closed(self, tmp_path):
        daemon, server, url, _ = await _boot(
            tmp_path, {"t@example.com": "owner"}, idle=1
        )
        try:
            async with connect(
                url, additional_headers=_wire(url, "t@example.com")
            ) as ws:
                await ws.recv()
                with pytest.raises(ConnectionClosed) as err:
                    while True:
                        await asyncio.wait_for(ws.recv(), 5)
                assert err.value.rcvd.code == wg.CLOSE_IDLE
        finally:
            server.close()
            await daemon.stop()


@pytest.mark.unit
@pytest.mark.asyncio
class TestHandshakeRefusals:
    async def _status(self, url, headers):
        with pytest.raises(InvalidStatus) as err:
            async with connect(url, additional_headers=headers):
                pass
        return err.value.response.status_code

    async def test_refusals(self, tmp_path):
        daemon, server, url, _ = await _boot(tmp_path, {"toufic@example.com": "owner"})
        try:
            assert await self._status(url, _wire(url, login=None)) == 403
            assert await self._status(url, _wire(url, "stranger@example.com")) == 403
            assert (
                await self._status(url, _wire(url, Origin="https://evil.example.com"))
                == 403
            )
            assert (
                await self._status(
                    url, _wire(url, **{"Tailscale-Funnel-Request": "?1"})
                )
                == 403
            )
        finally:
            server.close()
            await daemon.stop()

    async def test_page_served_only_to_allowed_accounts(self, tmp_path):
        daemon, server, url, _ = await _boot(tmp_path, {"toufic@example.com": "owner"})
        base = url.replace("ws://", "http://").removesuffix("/ws")
        try:
            reader, writer = await asyncio.open_connection(
                *base.removeprefix("http://").split(":")
            )
            writer.write(
                f"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nTailscale-User-Login: toufic@example.com\r\nConnection: close\r\n\r\n".encode()
            )
            body = (await reader.read()).decode()
            assert "200 OK" in body and "<title>JARVIS</title>" in body
            assert "default-src 'self'" in body
            writer.close()
            reader, writer = await asyncio.open_connection(
                *base.removeprefix("http://").split(":")
            )
            writer.write(
                f"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode()
            )
            assert "403" in (await reader.read()).decode()
            writer.close()
        finally:
            server.close()
            await daemon.stop()
