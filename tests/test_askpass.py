"""The sudo password popup (#310): sudo's askpass helper, the relay that
vets who asks and hands the answer back, and the GUI socket's side."""

import asyncio
import json
import os
import socket
import stat
import subprocess
import threading
from types import SimpleNamespace

import pytest

from jarvis.config import Config
from jarvis.runtime import askpass, askpass_helper, io
from jarvis.runtime.password_wait import WAITING_NOTE, WAITING_NOTE_POPUP, waiting_note

HELPER = "/data/jarvis/askpass-helper"
DMCP = "/usr/bin/dmcp"


def _proc_table(**over):
    """A daemon (100) > dispatch (200) > dmcp call (300) > sudo (400) > helper (500)."""
    table = {
        100: askpass.ProcInfo(100, 1, "python", 1000, ["python", "-m", "jarvis"], None),
        200: askpass.ProcInfo(200, 100, "dispatch", 1000, ["dispatch", "serve"], None),
        300: askpass.ProcInfo(300, 200, "dmcp", 1000, ["dmcp", "call"], DMCP),
        400: askpass.ProcInfo(
            400,
            300,
            "sudo",
            0,
            [
                "sudo",
                "-A",
                "--",
                DMCP,
                "call",
                "shell",
                "execute_command",
                "--args",
                '{"command": "pacman -Syu"}',
                "--dmcp-internal-elevation-authenticated",
                "--dmcp-internal-sudo-reexec",
            ],
            None,
        ),
        500: askpass.ProcInfo(
            500,
            400,
            "askpass-helper",
            1000,
            ["/usr/bin/python3", "-I", HELPER, "[sudo] password"],
            None,
        ),
    }
    table.update(over)
    return table.get


def _vet(proc):
    return askpass.vet(500, daemon_pid=100, helper=HELPER, dmcp=DMCP, proc=proc)


@pytest.mark.unit
class TestVet:
    def test_a_real_chain_shows_sudos_own_command(self):
        command, sudo_pid, why = _vet(_proc_table())
        assert why == ""
        assert sudo_pid == 400
        assert command == (
            "/usr/bin/dmcp call shell execute_command --args "
            '\'{"command": "pacman -Syu"}\''
        )

    def test_not_our_helper(self):
        base = _proc_table()
        evil = askpass.ProcInfo(500, 400, "evil", 1000, ["/tmp/evil", "p"], None)
        command, _, why = _vet(lambda p: evil if p == 500 else base(p))
        assert command is None and why == "not JARVIS's askpass helper"

    def test_parent_must_be_sudo_running_as_root(self):
        base = _proc_table()
        for bad in (
            askpass.ProcInfo(400, 300, "sudo", 1000, ["sudo", "-A", "--", DMCP], None),
            askpass.ProcInfo(400, 300, "sh", 0, ["sh"], None),
        ):
            proc = lambda p, bad=bad: bad if p == 400 else base(p)  # noqa: E731
            assert _vet(proc)[0] is None

    def test_sudo_must_be_run_by_dmcp(self):
        base = _proc_table()
        other = askpass.ProcInfo(
            300, 200, "python", 1000, ["python"], "/usr/bin/python3"
        )
        assert _vet(lambda p: other if p == 300 else base(p))[2].startswith(
            "sudo was run by"
        )

    def test_dmcp_must_be_below_this_daemon(self):
        base = _proc_table()
        stray = askpass.ProcInfo(200, 1, "bash", 1000, ["bash"], None)
        assert _vet(lambda p: stray if p == 200 else base(p))[2] == (
            "dmcp was not started by this JARVIS"
        )

    def test_unknown_dmcp_refuses(self):
        assert (
            askpass.vet(
                500, daemon_pid=100, helper=HELPER, dmcp=None, proc=_proc_table()
            )[0]
            is None
        )

    def test_a_vanished_process_refuses(self):
        base = _proc_table()
        assert _vet(lambda p: None if p == 400 else base(p))[0] is None

    def test_sudo_command(self):
        assert askpass.sudo_command(["sudo", "-A", "--", "cp", "a", "b"]) == [
            "cp",
            "a",
            "b",
        ]
        assert askpass.sudo_command(["sudo", "-A", "rm", "-rf", "x"]) == [
            "rm",
            "-rf",
            "x",
        ]
        assert askpass.sudo_command(["sudo", "-A", "--"]) == []

    def test_read_proc_reads_this_process(self):
        me = askpass.read_proc(os.getpid())
        assert me.ppid == os.getppid()
        assert me.euid == os.geteuid()
        assert me.exe and os.path.exists(me.exe)
        assert askpass.read_proc(2**22 + 12345) is None

    @pytest.mark.parametrize(
        "password, ok",
        [
            ("hunter2", True),
            ("", False),
            (None, False),
            ("a\nb", False),
            ("a\0", False),
            ("x" * 5000, False),
        ],
    )
    def test_password_error(self, password, ok):
        assert (askpass.password_error(password) is None) is ok


class _Daemon:
    """A relay on a real unix socket, with a stand-in for /proc and the peer."""

    def __init__(self, tmp_path, proc=None):
        self.sent = []
        self.relay = askpass.AskpassRelay(
            self.sent.append,
            dmcp=DMCP,
            daemon_pid=100,
            proc=proc or _proc_table(),
            peer=lambda writer: 500,
        )
        self.path = str(tmp_path / "a.sock")

    async def __aenter__(self):
        self.server = await asyncio.start_unix_server(self.relay.handle, path=self.path)
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def ask(self):
        reader, writer = await asyncio.open_unix_connection(self.path)
        writer.write(b'{"type": "ask", "prompt": "[sudo] password for t: "}\n')
        await writer.drain()
        return reader, writer

    async def request(self):
        for _ in range(100):
            if self.sent:
                return self.sent[0]
            await asyncio.sleep(0.01)
        raise AssertionError("no password_request broadcast")


@pytest.fixture
def popup(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "SUDO_PASSWORD_POPUP", True)
    monkeypatch.setattr(Config, "JARVIS_DATA_DIR", "/data/jarvis")


@pytest.mark.unit
@pytest.mark.asyncio
class TestRelay:
    async def test_check_follows_the_setting(self, tmp_path, monkeypatch):
        async with _Daemon(tmp_path) as d:
            for on in (True, False):
                monkeypatch.setattr(Config, "SUDO_PASSWORD_POPUP", on)
                reader, writer = await asyncio.open_unix_connection(d.path)
                writer.write(b'{"type": "check"}\n')
                await writer.drain()
                assert json.loads(await reader.readline()) == {"ready": on}
                writer.close()

    async def test_the_answer_goes_to_the_helper_alone(self, tmp_path, popup):
        async with _Daemon(tmp_path) as d:
            reader, writer = await d.ask()
            request = await d.request()
            assert request["type"] == "password_request"
            assert request["attempt"] == 1
            assert "pacman -Syu" in request["command"]
            assert "password" not in request
            assert d.relay.pending() == [request]
            assert d.relay.answer(request["id"], "hunter2") is None
            assert json.loads(await reader.readline()) == {"password": "hunter2"}
            writer.close()
            await asyncio.sleep(0.05)
            assert d.sent[-1] == {
                "type": "password_request_done",
                "id": request["id"],
                "outcome": "answered",
            }
            assert "hunter2" not in json.dumps(d.sent)
            assert d.relay.pending() == []
            assert d.relay.answer(request["id"], "again") == (
                "That password request has already ended."
            )

    async def test_cancel(self, tmp_path, popup):
        async with _Daemon(tmp_path) as d:
            reader, writer = await d.ask()
            request = await d.request()
            assert d.relay.cancel(request["id"]) is None
            assert json.loads(await reader.readline()) == {"cancel": True}
            writer.close()
            await asyncio.sleep(0.05)
            assert d.sent[-1]["outcome"] == "cancelled"

    async def test_a_helper_that_goes_away_ends_the_request(self, tmp_path, popup):
        async with _Daemon(tmp_path) as d:
            reader, writer = await d.ask()
            request = await d.request()
            writer.close()
            for _ in range(100):
                if len(d.sent) > 1:
                    break
                await asyncio.sleep(0.01)
            assert d.sent[-1] == {
                "type": "password_request_done",
                "id": request["id"],
                "outcome": "gone",
            }
            assert d.relay.pending() == []

    async def test_a_bad_password_is_refused_and_the_request_stays(
        self, tmp_path, popup
    ):
        async with _Daemon(tmp_path) as d:
            reader, writer = await d.ask()
            request = await d.request()
            assert d.relay.answer(request["id"], "two\nlines")
            assert d.relay.pending() == [request]
            d.relay.cancel(request["id"])
            writer.close()

    async def test_sudo_asking_again_is_the_next_try(self, tmp_path, popup):
        async with _Daemon(tmp_path) as d:
            for want in (1, 2):
                d.sent.clear()
                reader, writer = await d.ask()
                request = await d.request()
                assert request["attempt"] == want
                d.relay.answer(request["id"], "wrong")
                await reader.readline()
                writer.close()

    async def test_off_means_no_popup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Config, "SUDO_PASSWORD_POPUP", False)
        async with _Daemon(tmp_path) as d:
            reader, writer = await d.ask()
            assert "error" in json.loads(await reader.readline())
            assert d.sent == []

    async def test_a_stranger_gets_no_popup(self, tmp_path, popup):
        base = _proc_table()
        not_sudo = askpass.ProcInfo(400, 300, "fake", 1000, ["sudo"], None)
        async with _Daemon(
            tmp_path, proc=lambda p: not_sudo if p == 400 else base(p)
        ) as d:
            reader, writer = await d.ask()
            assert "not sudo" in json.loads(await reader.readline())["error"]
            assert d.sent == []


def _fake_daemon(path, reply):
    """One-connection server answering the helper's first line with `reply`."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    seen = []

    def run():
        conn, _ = srv.accept()
        with conn:
            seen.append(json.loads(conn.makefile("rb").readline()))
            if reply is not None:
                conn.sendall((json.dumps(reply) + "\n").encode())
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, seen


@pytest.mark.unit
class TestHelper:
    def test_no_socket_means_not_ready(self, monkeypatch, tmp_path):
        monkeypatch.delenv("JARVIS_ASKPASS_SOCKET", raising=False)
        assert askpass_helper.main(["--check"]) == 1
        monkeypatch.setenv("JARVIS_ASKPASS_SOCKET", str(tmp_path / "none.sock"))
        assert askpass_helper.main(["--check"]) == 1

    @pytest.mark.parametrize("ready, code", [(True, 0), (False, 1)])
    def test_check(self, monkeypatch, tmp_path, ready, code):
        path = str(tmp_path / "c.sock")
        t, seen = _fake_daemon(path, {"ready": ready})
        monkeypatch.setenv("JARVIS_ASKPASS_SOCKET", path)
        assert askpass_helper.main(["--check"]) == code
        t.join(2)
        assert seen == [{"type": "check"}]

    def test_the_password_goes_to_stdout(self, monkeypatch, tmp_path, capsys):
        path = str(tmp_path / "p.sock")
        t, seen = _fake_daemon(path, {"password": "hunter2"})
        monkeypatch.setenv("JARVIS_ASKPASS_SOCKET", path)
        assert askpass_helper.main(["[sudo] password for t: "]) == 0
        t.join(2)
        assert capsys.readouterr().out == "hunter2\n"
        assert seen == [{"type": "ask", "prompt": "[sudo] password for t: "}]

    @pytest.mark.parametrize("reply", [{"cancel": True}, {"error": "no"}, None])
    def test_no_password_is_a_failure(self, monkeypatch, tmp_path, capsys, reply):
        path = str(tmp_path / "n.sock")
        t, _ = _fake_daemon(path, reply)
        monkeypatch.setenv("JARVIS_ASKPASS_SOCKET", path)
        assert askpass_helper.main(["p"]) == 1
        t.join(2)
        assert capsys.readouterr().out == ""

    def test_installed_helper_runs_on_its_own(self, monkeypatch, tmp_path):
        """The copy sudo runs: owner-only, isolated Python, no JARVIS import."""
        monkeypatch.setattr(Config, "JARVIS_DATA_DIR", str(tmp_path))
        path = askpass.install_helper()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o700
        assert open(path).readline().endswith(" -I\n")
        sock = str(tmp_path / "i.sock")
        t, _ = _fake_daemon(sock, {"ready": True})
        env = {"PATH": os.environ.get("PATH", ""), "JARVIS_ASKPASS_SOCKET": sock}
        assert subprocess.run([path, "--check"], env=env, timeout=10).returncode == 0
        t.join(2)


class _Writer:
    def __init__(self):
        self.sent = []


def _app(relay=None):
    writer = _Writer()

    async def write(w, message):
        w.sent.append(message)

    app = SimpleNamespace(
        askpass=relay, _gui_clients={writer: ""}, _gui_subscriptions={}
    )
    return app, writer, write


@pytest.mark.unit
class TestGuiSocket:
    def _run(self, app, writer, write, msg, monkeypatch):
        monkeypatch.setattr(io, "_gui_write", write)
        asyncio.run(
            io._process_gui_message(
                app, __import__("logging").getLogger("t"), msg, writer
            )
        )

    def test_an_answer_reaches_the_relay(self, monkeypatch):
        calls = []
        relay = SimpleNamespace(
            answer=lambda i, p: calls.append(("answer", i, p)),
            cancel=lambda i: calls.append(("cancel", i)),
            pending=lambda: [],
        )
        app, writer, write = _app(relay)
        self._run(
            app,
            writer,
            write,
            {"type": "password_response", "id": "r1", "password": "pw"},
            monkeypatch,
        )
        self._run(
            app, writer, write, {"type": "password_cancel", "id": "r1"}, monkeypatch
        )
        assert calls == [("answer", "r1", "pw"), ("cancel", "r1")]
        assert writer.sent == []

    def test_a_refused_answer_says_why(self, monkeypatch):
        relay = SimpleNamespace(
            answer=lambda i, p: "ended", cancel=None, pending=lambda: []
        )
        app, writer, write = _app(relay)
        self._run(
            app,
            writer,
            write,
            {"type": "password_response", "id": "x", "password": "p"},
            monkeypatch,
        )
        assert writer.sent == [{"type": "error", "message": "ended"}]

    def test_without_a_relay(self, monkeypatch):
        app, writer, write = _app(None)
        self._run(
            app,
            writer,
            write,
            {"type": "password_response", "id": "x", "password": "p"},
            monkeypatch,
        )
        assert writer.sent[0]["type"] == "error"

    def test_a_client_that_subscribes_gets_what_waits(self, monkeypatch):
        waiting = {"type": "password_request", "id": "r1", "command": "c", "attempt": 1}
        relay = SimpleNamespace(pending=lambda: [waiting])
        app, writer, write = _app(relay)
        hello = {
            "type": "hello",
            "label": "jarvis-app",
            "subscribe": ["password_request"],
        }
        self._run(app, writer, write, hello, monkeypatch)
        assert writer.sent == [waiting]
        other, w2, _ = _app(relay)
        self._run(
            other,
            w2,
            write,
            {"type": "hello", "label": "tui", "subscribe": ["activity"]},
            monkeypatch,
        )
        assert w2.sent == []


@pytest.mark.unit
def test_the_model_is_told_where_to_type_it():
    polkit = "dmcp: waiting for the administrator password on the computer running JARVIS (polkit prompt)"
    sudo = "dmcp: waiting for the administrator password on the computer running JARVIS (sudo askpass)"
    assert waiting_note(polkit) == WAITING_NOTE
    assert (
        waiting_note("out\n" + sudo + "\n[sudo] Sorry, try again.")
        == WAITING_NOTE_POPUP
    )
    assert "Never ask for the password in the chat" in WAITING_NOTE_POPUP


@pytest.mark.unit
def test_dispatch_hands_the_relay_to_dmcp(monkeypatch):
    from jarvis.dispatch import transport

    monkeypatch.setenv("DMCP_SUDO_ASKPASS", "/d/askpass-helper")
    monkeypatch.setenv("JARVIS_ASKPASS_SOCKET", "/d/askpass.sock")
    env = transport._dmcp_env()
    assert env["DMCP_SUDO_ASKPASS"] == "/d/askpass-helper"
    assert env["JARVIS_ASKPASS_SOCKET"] == "/d/askpass.sock"
