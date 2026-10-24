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
            "approve_all_confirmations",
            "confirmation_response",
            "set_confirmation_mode",
            "add_provider",
            "edit_provider",
            "remove_provider",
            "get_settings",
            "set_wake_chime_path",
            "shutdown_request",
            "setup_completed",
            "cancel_setup",
        ],
    )
    def test_no_role_may_send_admin_messages(self, kind):
        for role in wg.ROLES:
            assert not wg.sendable(role, {"type": kind})

    def test_status_and_work_need_trust(self):
        for kind in ("get_status", "list_work"):
            assert not wg.sendable("guest", {"type": kind})
            assert wg.sendable("trusted", {"type": kind})
            assert wg.sendable("owner", {"type": kind})

    def test_activity_for_everyone_actions_for_trusted(self):
        # Action blocks carry tool parameters and raw output.
        activity = {"type": "activity", "text": "Searching…", "kind": "dispatch"}
        for role in wg.ROLES:
            assert wg.relayable(activity, role)
        for kind in ("action", "status", "work"):
            assert not wg.relayable({"type": kind}, "guest")
            assert wg.relayable({"type": kind}, "trusted")
            assert wg.relayable({"type": kind}, "owner")
        assert not wg.relayable({"type": "action"}, None)

    def test_malformed_messages_are_refused(self):
        assert not wg.sendable("owner", [])
        assert not wg.sendable("owner", {"type": ["message"]})
        assert not wg.sendable("owner", {})

    def test_provider_and_settings_broadcasts_stay_off_the_wire(self):
        assert not wg.relayable({"type": "provider_list", "providers": []})
        assert not wg.relayable({"type": "settings"})
        assert wg.relayable({"type": "response", "content": "x", "done": True})

    def test_shutdown_snapshot_is_cut_to_the_state(self):
        notice = wg.shutdown_notice(
            {"type": "DAEMON_SHUTDOWN", "state": "idle", "goals": ["secret"]}
        )
        assert notice == {"type": "DAEMON_SHUTDOWN", "state": "idle"}

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
                out = {
                    "type": "settings",
                    "confirmation_mode": self.mode,
                    "wake_chime_path": "/x",
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
                "subscribe": ["activity", "action"],
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
