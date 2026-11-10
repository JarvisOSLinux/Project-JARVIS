"""Root shell calls: no sudo, one at a time, package transactions as jobs (#317).

Yakup's system update on a small model (2026-11-10): `pacman -Syu` and
`sudo pacman -Syu` went to the root shell server in one batch. polkit refused
the second password prompt while the first was open; the first ran as root and
died at pacman's "[Y/n]" with no stdin; and on the retry `sudo` got the single
argument "pacman -Syu" and found no such program.
"""

import asyncio
import logging

import pytest

import jarvis.core.confirmation_manager as cm
from jarvis.core.confirmation_manager import ConfirmationManager
from jarvis.runtime import dispatch_flow

_LOG = logging.getLogger("test")
ROOT = "jarvis-shell-system-mcp"
USER = "jarvis-shell-mcp"
_SHELL = ["execute_command", "execute_script", "run_job", "send_input", "read_output"]


def _manifest(scope):
    return {
        "scope": scope,
        "trustStatus": "community",
        "tools": [{"name": t, "threat_level": "dangerous"} for t in _SHELL],
    }


class _Adapter:
    is_connected = True

    def __init__(self):
        self.sent = None

    async def list_server_tools(self, server_id):
        return {
            "server": server_id,
            "tools": [{"name": t, "inputSchema": {}} for t in _SHELL],
        }

    async def get_server_manifest(self, server_id):
        return _manifest("system" if server_id == ROOT else "user")

    async def send_tasks(self, tasks, session_id=None):
        self.sent = tasks
        return {"output": "ok"}


class _App:
    def __init__(self):
        self.dispatch = _Adapter()
        self.confirmation = ConfirmationManager()


def _send(monkeypatch, *tasks):
    for cache in ("_THREAT_DECL_BY_SERVER", "_STATEFUL_BY_SERVER", "_ROOT_BY_SERVER"):
        monkeypatch.setattr(dispatch_flow, cache, {})
    monkeypatch.setattr(cm.Config, "CONFIRMATION_MODE", "allow_all")
    monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)
    app = _App()
    result = asyncio.run(
        dispatch_flow.dispatch_send(app, _LOG, [dict(t) for t in tasks])
    )
    return app, result


def _cmd(server, command, *args):
    return {
        "server": server,
        "tool": "execute_command",
        "params": {"command": command, "args": list(args)},
    }


@pytest.mark.unit
class TestYakupsBatch:
    def test_pacman_and_sudo_pacman_become_one_job(self, monkeypatch):
        app, result = _send(
            monkeypatch,
            _cmd(ROOT, "pacman", "-Syu"),
            _cmd(ROOT, "sudo", "pacman -Syu"),
        )
        assert "error" not in result
        assert len(app.dispatch.sent) == 1
        task = app.dispatch.sent[0]
        assert task["tool"] == "run_job"
        assert task["params"]["command"] == "pacman -Syu"
        assert task["params"]["job"].startswith("pacman-Syu-")

    def test_two_different_root_calls_are_sent_back_whole(self, monkeypatch):
        app, result = _send(
            monkeypatch,
            _cmd(ROOT, "pacman", "-Syu", "--noconfirm"),
            _cmd(ROOT, "systemctl", "restart", "cups"),
            _cmd(USER, "uname", "-r"),
        )
        assert app.dispatch.sent is None, "nothing in the batch is sent"
        assert "Nothing in this batch was sent" in result["error"]
        assert "pacman -Syu --noconfirm" in result["error"]
        assert "systemctl restart cups" in result["error"]

    def test_user_calls_still_run_together(self, monkeypatch):
        app, result = _send(
            monkeypatch, _cmd(USER, "uname", "-r"), _cmd(USER, "df", "-h")
        )
        assert "error" not in result and len(app.dispatch.sent) == 2


@pytest.mark.unit
class TestNoSudoForRoot:
    def test_sudo_and_its_bare_flags_go(self, monkeypatch):
        app, _ = _send(
            monkeypatch, _cmd(ROOT, "sudo", "-E", "systemctl", "restart", "cups")
        )
        params = app.dispatch.sent[0]["params"]
        assert (params["command"], params["args"]) == ("systemctl", ["restart", "cups"])

    def test_a_whole_command_line_in_one_argument_is_split(self, monkeypatch):
        app, _ = _send(monkeypatch, _cmd(ROOT, "pkexec", "systemctl restart cups"))
        params = app.dispatch.sent[0]["params"]
        assert (params["command"], params["args"]) == ("systemctl", ["restart", "cups"])

    def test_sudo_as_another_user_is_left_alone(self, monkeypatch):
        app, _ = _send(monkeypatch, _cmd(ROOT, "sudo", "-u", "alice", "whoami"))
        assert app.dispatch.sent[0]["params"]["command"] == "sudo"

    def test_job_and_script_strings_lose_the_prefix(self, monkeypatch):
        app, _ = _send(
            monkeypatch,
            {
                "server": ROOT,
                "tool": "run_job",
                "params": {"job": "u", "command": "sudo -E pacman -Syu --noconfirm"},
            },
        )
        assert app.dispatch.sent[0]["params"]["command"] == "pacman -Syu --noconfirm"
        app, _ = _send(
            monkeypatch,
            {
                "server": ROOT,
                "tool": "execute_script",
                "params": {"script": "sudo systemctl restart cups\necho done"},
            },
        )
        assert (
            app.dispatch.sent[0]["params"]["script"]
            == "systemctl restart cups\necho done"
        )

    def test_sudo_on_the_user_server_is_not_touched(self, monkeypatch):
        app, _ = _send(monkeypatch, _cmd(USER, "sudo", "whoami"))
        assert app.dispatch.sent[0]["params"]["command"] == "sudo"


@pytest.mark.unit
class TestPackageTransactionsAsJobs:
    @pytest.mark.parametrize(
        "command, args",
        [
            ("pacman", ["-S", "vim"]),
            ("yay", ["-Syu"]),
            ("apt", ["install", "vim"]),
            ("dnf", ["upgrade"]),
        ],
    )
    def test_a_transaction_that_asks_becomes_a_job(self, monkeypatch, command, args):
        app, _ = _send(monkeypatch, _cmd(USER, command, *args))
        task = app.dispatch.sent[0]
        assert task["tool"] == "run_job"
        assert task["params"]["command"] == " ".join([command, *args])

    @pytest.mark.parametrize(
        "command, args",
        [
            ("pacman", ["-Syu", "--noconfirm"]),
            ("pacman", ["-Ss", "vim"]),
            ("pacman", ["-Q"]),
            ("apt", ["install", "-y", "vim"]),
            ("apt-get", ["-qy", "upgrade"]),
            ("apt", ["list", "--upgradable"]),
            ("ls", ["-l"]),
        ],
    )
    def test_one_that_answers_itself_or_only_reads_is_left(
        self, monkeypatch, command, args
    ):
        app, _ = _send(monkeypatch, _cmd(USER, command, *args))
        assert app.dispatch.sent[0]["tool"] == "execute_command"

    def test_two_jobs_never_share_a_name(self, monkeypatch):
        app, _ = _send(
            monkeypatch, _cmd(USER, "yay", "-S", "a"), _cmd(USER, "yay", "-S", "b")
        )
        names = {t["params"]["job"] for t in app.dispatch.sent}
        assert len(names) == 2


@pytest.mark.unit
def test_a_reminder_names_the_job_it_belongs_to():
    app = _App()
    dispatch_flow.note_dispatched_tools(
        app,
        [7],
        [
            {
                "server": ROOT,
                "tool": "run_job",
                "params": {"job": "pacman-Syu-ab12", "command": "pacman -Syu"},
            }
        ],
    )
    note = dispatch_flow.job_note(
        app, [{"type": "REMIND", "pid": 7}, {"type": "EXIT", "pid": 7}]
    )
    assert note.count("JOB:") == 1
    assert '"pacman-Syu-ab12"' in note and ROOT in note and "send_input" in note
    assert dispatch_flow.job_note(app, [{"type": "REMIND", "pid": 9}]) == ""
