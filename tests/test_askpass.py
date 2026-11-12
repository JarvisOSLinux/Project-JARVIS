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
CALL = [
    "dmcp",
    "call",
    "shell",
    "execute_command",
    "--args",
    '{"command": "pacman -Syu"}',
]
PROMPT = "[sudo] password for t: "


def _proc_table(**over):
    """A daemon (100) > dispatch (200) > dmcp call (300) > sudo (400) > helper (500)."""
    table = {
        100: askpass.ProcInfo(
            100, 1, "python", 1000, ["python", "-m", "jarvis"], None, 1
        ),
        200: askpass.ProcInfo(
            200, 100, "dispatch", 1000, ["dispatch", "serve"], None, 2
        ),
        300: askpass.ProcInfo(300, 200, "dmcp", 1000, CALL, DMCP, 3),
        400: askpass.ProcInfo(
            400,
            300,
            "sudo",
            0,
            ["sudo", "-A", "--", DMCP, *CALL[1:], "--dmcp-internal-sudo-reexec"],
            None,
            4,
        ),
        500: askpass.ProcInfo(
            500,
            400,
            "askpass-helper",
            1000,
            ["/usr/bin/python3", "-I", HELPER, PROMPT],
            None,
            5,
        ),
    }
    table.update({int(k): v for k, v in over.items()})
    return table.get


def _vet(proc):
    return askpass.vet(500, daemon_pid=100, helper=HELPER, dmcp=DMCP, proc=proc)


def _with(pid, info):
    base = _proc_table()
    return lambda p: info if p == pid else base(p)


@pytest.mark.unit
class TestVet:
    def test_a_real_chain_shows_sudos_own_command_and_prompt(self):
        vetted, why = _vet(_proc_table())
        assert why == ""
        assert vetted.sudo.pid == 400 and vetted.caller.pid == 300
        assert vetted.prompt == PROMPT.strip()
        assert vetted.command == (
            "/usr/bin/dmcp call shell execute_command --args "
            '\'{"command": "pacman -Syu"}\''
        )

    def test_not_our_helper(self):
        evil = askpass.ProcInfo(500, 400, "evil", 1000, ["/tmp/evil", "p"], None)
        vetted, why = _vet(_with(500, evil))
        assert vetted is None and why == "not JARVIS's askpass helper"

    def test_parent_must_be_sudo_running_as_root(self):
        for bad in (
            askpass.ProcInfo(400, 300, "sudo", 1000, ["sudo", "-A", "--", DMCP], None),
            askpass.ProcInfo(400, 300, "sh", 0, ["sh"], None),
        ):
            assert _vet(_with(400, bad))[0] is None

    def test_sudo_must_be_run_by_dmcp(self):
        other = askpass.ProcInfo(
            300, 200, "python", 1000, ["python"], "/usr/bin/python3"
        )
        assert _vet(_with(300, other))[1].startswith("sudo was run by")

    def test_dmcp_must_be_below_this_daemon(self):
        stray = askpass.ProcInfo(200, 1, "bash", 1000, ["bash"], None)
        assert _vet(_with(200, stray))[1] == "dmcp was not started by this JARVIS"

    def test_unknown_dmcp_refuses(self):
        assert (
            askpass.vet(
                500, daemon_pid=100, helper=HELPER, dmcp=None, proc=_proc_table()
            )[0]
            is None
        )

    def test_a_vanished_process_refuses(self):
        assert _vet(_with(400, None))[0] is None

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
        assert me.start > 0
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


@pytest.fixture
def clean_expected():
    askpass._expected.clear()
    yield
    askpass._expected.clear()


@pytest.mark.unit
class TestOnlyWhatJarvisStarted:
    """Anything below the daemon can run the real dmcp; only an elevation
    JARVIS started itself may show the popup."""

    def test_a_dispatched_task(self, clean_expected):
        assert not askpass.started_by_jarvis(CALL)
        askpass.expect_tasks(
            [
                {
                    "server": "shell",
                    "tool": "execute_command",
                    "params": {"command": "pacman -Syu"},
                }
            ]
        )
        assert askpass.started_by_jarvis(CALL)
        # dispatch writes the JSON its own way; the parsed value decides.
        assert askpass.started_by_jarvis(
            [
                "dmcp",
                "call",
                "shell",
                "execute_command",
                "--args",
                '{"command":"pacman -Syu"}',
            ]
        )
        assert askpass.started_by_jarvis(
            [*CALL, "--session", "g1", "--dmcp-internal-sudo-reexec"]
        )

    def test_another_command_on_the_same_tool_is_not(self, clean_expected):
        askpass.expect_tasks(
            [
                {
                    "server": "shell",
                    "tool": "execute_command",
                    "params": {"command": "ls"},
                }
            ]
        )
        assert not askpass.started_by_jarvis(CALL)
        assert not askpass.started_by_jarvis(
            ["dmcp", "call", "shell", "execute_command"]
        )
        assert not askpass.started_by_jarvis(
            ["dmcp", "call", "shell", "x", "--args", "{bad"]
        )

    def test_a_task_with_no_params(self, clean_expected):
        askpass.expect_tasks([{"server": "s", "tool": "t", "params": {}}])
        assert askpass.started_by_jarvis(["dmcp", "call", "s", "t"])

    def test_a_dmcp_command_while_it_runs(self, clean_expected):
        args = ["sources", "remove", "--system", "https://x"]
        assert not askpass.started_by_jarvis(["dmcp", *args])
        key = askpass.expect_command(args)
        assert askpass.started_by_jarvis(["/usr/bin/dmcp", *args])
        assert not askpass.started_by_jarvis(
            ["dmcp", "sources", "add", "--system", "https://x"]
        )
        askpass.forget(key)
        assert not askpass.started_by_jarvis(["dmcp", *args])

    def test_expectations_expire_and_are_bounded(self, clean_expected, monkeypatch):
        askpass.expect_tasks([{"server": "s", "tool": "t"}])
        now = askpass.time.monotonic()
        monkeypatch.setattr(
            askpass.time, "monotonic", lambda: now + askpass.EXPECT_TTL_S + 1
        )
        assert not askpass.started_by_jarvis(["dmcp", "call", "s", "t"])
        for i in range(askpass._MAX_EXPECTED + 10):
            askpass.expect_command([str(i)])
        assert len(askpass._expected) <= askpass._MAX_EXPECTED


class _Daemon:
    """A relay on a real unix socket, with a stand-in for /proc and the peer."""

    def __init__(self, tmp_path, proc=None, started=lambda cmdline: True):
        self.sent = []
        self.killed = []
        self.table = proc or _proc_table()
        self.relay = askpass.AskpassRelay(
            self.sent.append,
            dmcp=DMCP,
            daemon_pid=100,
            proc=lambda p: self.table(p),
            peer=lambda writer: 500,
            started=started,
            kill=lambda info, proc: self.killed.append(info.pid),
            watch_s=0.05,
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

    async def done(self):
        for _ in range(100):
            if self.sent and self.sent[-1]["type"] == "password_request_done":
                return self.sent[-1]
            await asyncio.sleep(0.01)
        raise AssertionError("no password_request_done broadcast")


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
            assert request["attempt"] == 1 and request["retry"] is False
            assert request["prompt"] == PROMPT.strip()
            assert "pacman -Syu" in request["command"]
            assert "password" not in request
            assert d.relay.pending() == [request]
            assert d.relay.answer(request["id"], "hunter2") is None
            assert json.loads(await reader.readline()) == {"password": "hunter2"}
            writer.close()
            assert await d.done() == {
                "type": "password_request_done",
                "id": request["id"],
                "outcome": "answered",
            }
            assert "hunter2" not in json.dumps(d.sent)
            assert d.relay.pending() == []
            assert d.killed == []
            assert d.relay.answer(request["id"], "again") == (
                "That password request has already ended."
            )

    async def test_cancel_kills_sudo_instead_of_failing_its_password(
        self, tmp_path, popup
    ):
        async with _Daemon(tmp_path) as d:
            reader, writer = await d.ask()
            request = await d.request()
            assert d.relay.cancel(request["id"]) is None
            assert (await d.done())["outcome"] == "cancelled"
            assert d.killed == [400]
            assert await reader.readline() == b"", "the helper hears nothing"
            writer.close()

    async def test_when_the_dmcp_that_asked_goes_away_its_sudo_goes(
        self, tmp_path, popup
    ):
        async with _Daemon(tmp_path) as d:
            reader, writer = await d.ask()
            request = await d.request()
            d.table = _with(300, None)  # the task was stopped
            done = await d.done()
            assert done == {
                "type": "password_request_done",
                "id": request["id"],
                "outcome": "gone",
            }
            assert d.killed == [400]
            writer.close()

    async def test_a_reused_pid_is_not_the_same_dmcp(self, tmp_path, popup):
        async with _Daemon(tmp_path) as d:
            reader, writer = await d.ask()
            await d.request()
            again = askpass.ProcInfo(300, 1, "bash", 1000, ["bash"], "/bin/bash", 99)
            d.table = _with(300, again)
            assert (await d.done())["outcome"] == "gone"
            writer.close()

    async def test_a_helper_that_goes_away_ends_the_request(self, tmp_path, popup):
        async with _Daemon(tmp_path) as d:
            reader, writer = await d.ask()
            request = await d.request()
            writer.close()
            assert await d.done() == {
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

    async def test_sudo_asking_the_same_again_is_a_retry(self, tmp_path, popup):
        async with _Daemon(tmp_path) as d:
            for want, retry in ((1, False), (2, True)):
                d.sent.clear()
                reader, writer = await d.ask()
                request = await d.request()
                assert (request["attempt"], request["retry"]) == (want, retry)
                d.relay.answer(request["id"], "wrong")
                await reader.readline()
                writer.close()

    async def test_another_question_from_pam_is_not_a_retry(self, tmp_path, popup):
        async with _Daemon(tmp_path) as d:
            reader, writer = await d.ask()
            first = await d.request()
            d.relay.answer(first["id"], "pw")
            await reader.readline()
            writer.close()
            helper = askpass.ProcInfo(
                500,
                400,
                "askpass-helper",
                1000,
                ["/usr/bin/python3", "-I", HELPER, "Verification code:"],
                None,
                6,
            )
            d.table = _with(500, helper)
            d.sent.clear()
            reader, writer = await d.ask()
            second = await d.request()
            assert second["prompt"] == "Verification code:"
            assert (second["attempt"], second["retry"]) == (2, False)
            d.relay.cancel(second["id"])
            writer.close()

    async def test_off_means_no_popup(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Config, "SUDO_PASSWORD_POPUP", False)
        async with _Daemon(tmp_path) as d:
            reader, writer = await d.ask()
            assert "error" in json.loads(await reader.readline())
            assert d.sent == []

    async def test_a_stranger_gets_no_popup(self, tmp_path, popup):
        not_sudo = askpass.ProcInfo(400, 300, "fake", 1000, ["sudo"], None)
        async with _Daemon(tmp_path, proc=_with(400, not_sudo)) as d:
            reader, writer = await d.ask()
            assert "not sudo" in json.loads(await reader.readline())["error"]
            assert d.sent == []

    async def test_a_command_jarvis_did_not_start_gets_no_popup(self, tmp_path, popup):
        async with _Daemon(tmp_path, started=lambda cmdline: False) as d:
            reader, writer = await d.ask()
            assert "did not start" in json.loads(await reader.readline())["error"]
            assert d.sent == []

    async def test_stopping_ends_what_waits(self, tmp_path, popup):
        d = _Daemon(tmp_path)
        d.relay._server = await asyncio.start_unix_server(d.relay.handle, path=d.path)
        reader, writer = await d.ask()
        await d.request()
        await asyncio.wait_for(d.relay.stop(), 5)
        assert (await d.done())["outcome"] == "cancelled"
        assert d.killed == [400]
        reader2, writer2 = (None, None)
        try:
            reader2, writer2 = await asyncio.open_unix_connection(d.path)
        except OSError:
            pass
        assert writer2 is None, "the socket is gone"


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


@pytest.mark.unit
def test_a_client_asks_for_what_waits(monkeypatch):
    waiting = {"type": "password_request", "id": "r1", "command": "c", "attempt": 1}
    app, writer, write = _app(SimpleNamespace(pending=lambda: [waiting]))
    monkeypatch.setattr(io, "_gui_write", write)
    asyncio.run(
        io._process_gui_message(
            app,
            __import__("logging").getLogger("t"),
            {"type": "list_password_requests"},
            writer,
        )
    )
    assert writer.sent == [waiting]
    none, w2, _ = _app(None)
    asyncio.run(
        io._process_gui_message(
            none,
            __import__("logging").getLogger("t"),
            {"type": "list_password_requests"},
            w2,
        )
    )
    assert w2.sent == []


@pytest.mark.unit
def test_tasks_sent_to_dispatch_are_expected(clean_expected, monkeypatch):
    from jarvis.dispatch import adapter as adapter_mod

    seen = {}

    async def fake_call(self, logger, **kw):
        seen.update(kw)
        return {"ok": True}

    monkeypatch.setattr(adapter_mod, "transport_call_tool", fake_call)
    monkeypatch.setattr(adapter_mod, "require_connection", lambda *a: True)
    a = adapter_mod.DispatchAdapter.__new__(adapter_mod.DispatchAdapter)
    task = {
        "server": "shell",
        "tool": "execute_command",
        "params": {"command": "pacman -Syu"},
    }
    asyncio.run(a.send_tasks([task]))
    assert askpass.started_by_jarvis(CALL)


@pytest.mark.unit
class TestRunDmcp:
    def _fake(self, tmp_path, body):
        path = tmp_path / "dmcp"
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return str(path)

    def test_its_command_is_expected_only_while_it_runs(
        self, tmp_path, monkeypatch, clean_expected
    ):
        from jarvis.dispatch import dmcp_registry

        seen = {}
        real = askpass.started_by_jarvis
        monkeypatch.setattr(Config, "DMCP_BINARY", self._fake(tmp_path, 'echo "$@"\n'))

        async def go():
            out = await dmcp_registry.run_dmcp(
                __import__("logging").getLogger("t"), "sources", "list"
            )
            seen["after"] = real(["dmcp", "sources", "list"])
            return out

        orig = askpass.expect_command

        def spy(args):
            key = orig(args)
            seen["during"] = real(["dmcp", *args])
            return key

        monkeypatch.setattr(askpass, "expect_command", spy)
        assert asyncio.run(go()).strip() == "sources list"
        assert seen == {"during": True, "after": False}

    def test_a_timeout_kills_dmcp(self, tmp_path, monkeypatch, clean_expected):
        from jarvis.dispatch import dmcp_registry

        pidfile = tmp_path / "pid"
        monkeypatch.setattr(
            Config,
            "DMCP_BINARY",
            self._fake(tmp_path, f"echo $$ > {pidfile}\nexec sleep 30\n"),
        )
        monkeypatch.setattr(Config, "SUDO_PASSWORD_POPUP", False)
        real_wait_for = asyncio.wait_for

        async def short(aw, timeout):
            return await real_wait_for(aw, 0.5)

        monkeypatch.setattr(dmcp_registry.asyncio, "wait_for", short)
        out = asyncio.run(
            dmcp_registry.run_dmcp(__import__("logging").getLogger("t"), "install", "x")
        )
        assert out is None
        pid = int(pidfile.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    def test_the_popup_waits_as_long_as_dmcp_waits_for_polkit(self, monkeypatch):
        from jarvis.dispatch import dmcp_registry

        monkeypatch.setenv("DMCP_ELEVATION_TIMEOUT_SECS", "240")
        assert dmcp_registry._elevation_timeout() == 240
        monkeypatch.setenv("DMCP_ELEVATION_TIMEOUT_SECS", "x")
        assert dmcp_registry._elevation_timeout() == 180
