"""JARVIS knows who is on the machine, not just its own account.

Asked "list my Pictures folder", the ROOT model had no idea who the user was
and ran it in /home/user. A packaged JARVIS runs as the system user `jarvis`,
so its own account does not answer that either: logind does, read on every
call since people log in and out while JARVIS runs.
"""

import json
import subprocess
from unittest.mock import Mock

import pytest

from jarvis.core import system_info
from jarvis.core.system_info import SystemInfo
from jarvis.llm.context_manager import ContextManager

HOMES = {"toufic": "/home/toufic", "alice": "/home/alice", "root": "/root"}


def _session(session, uid, user, cls="user", seat=None):
    return {"session": session, "uid": uid, "user": user, "seat": seat, "class": cls}


@pytest.fixture
def host(monkeypatch):
    """A Linux host with loginctl; returns a setter for what it reports."""

    def configure(
        sessions=(), seat_session=None, me="jarvis", euid=960, home=None, json_ok=True
    ):
        def run(args, **kwargs):
            if args[1:] == ["list-sessions", "--json=short"]:
                if not json_ok:
                    return subprocess.CompletedProcess(args, 1, "", "unrecognized")
                return subprocess.CompletedProcess(args, 0, json.dumps(sessions), "")
            if args[1:] == ["list-sessions", "--no-legend"]:
                rows = "".join(
                    f"{s['session']} {s['uid']} {s['user']} {s['seat'] or ''}\n"
                    for s in sessions
                )
                return subprocess.CompletedProcess(args, 0, rows, "")
            if seat_session is None:
                return subprocess.CompletedProcess(args, 1, "", "No seat 'seat0'.")
            return subprocess.CompletedProcess(args, 0, seat_session + "\n", "")

        monkeypatch.setattr(system_info.platform, "system", lambda: "Linux")
        monkeypatch.setattr(system_info.shutil, "which", lambda name: "/usr/bin/x")
        monkeypatch.setattr(system_info.subprocess, "run", run)
        monkeypatch.setattr("pwd.getpwnam", lambda n: Mock(pw_dir=HOMES[n]))
        monkeypatch.setattr(system_info.os, "geteuid", lambda: euid)
        monkeypatch.setattr(system_info.getpass, "getuser", lambda: me)
        monkeypatch.setenv("HOME", home or f"/var/lib/{me}")

    return configure


@pytest.mark.unit
class TestGetUsers:
    def test_the_seat_owner_is_active_and_other_humans_are_listed(self, host):
        host(
            sessions=[
                _session("1", 1000, "toufic", cls="manager"),
                _session("3", 1000, "toufic", seat="seat0"),
                _session("5", 1001, "alice"),
                _session("6", 1001, "alice"),
                _session("c1", 120, "gdm", cls="greeter", seat="seat0"),
                _session("7", 0, "root"),
            ],
            seat_session="3",
        )
        assert SystemInfo.get_users() == {
            "active": ("toufic", "/home/toufic"),
            "others": [("alice", "/home/alice")],
            "runs_as": "jarvis",
        }

    def test_headless_jarvis_under_a_regular_account_is_that_user(self, host):
        host(
            sessions=[_session("5", 1001, "alice")],
            me="toufic",
            euid=1000,
            home="/home/toufic",
        )
        assert SystemInfo.get_users() == {
            "active": ("toufic", "/home/toufic"),
            "others": [("alice", "/home/alice")],
            "runs_as": "toufic",
        }

    def test_a_user_run_jarvis_is_talked_to_by_its_owner_not_the_seat(self, host):
        # Its sockets admit its own account only, so alice at the screen is
        # not who asked "list my Pictures".
        host(
            sessions=[_session("3", 1001, "alice", seat="seat0")],
            seat_session="3",
            me="toufic",
            euid=1000,
            home="/home/toufic",
        )
        assert SystemInfo.get_users() == {
            "active": ("toufic", "/home/toufic"),
            "others": [("alice", "/home/alice")],
            "runs_as": "toufic",
        }

    def test_systemd_without_json_is_read_from_the_table(self, host):
        host(
            sessions=[
                _session("3", 1000, "toufic", seat="seat0"),
                _session("c1", 120, "gdm", seat="seat0"),
                _session("5", 1001, "alice"),
            ],
            seat_session="3",
            json_ok=False,
        )
        assert SystemInfo.get_users() == {
            "active": ("toufic", "/home/toufic"),
            "others": [("alice", "/home/alice")],
            "runs_as": "jarvis",
        }

    def test_a_system_account_takes_the_one_human_logged_in(self, host):
        host(sessions=[_session("5", 1001, "alice")])
        assert SystemInfo.get_users() == {
            "active": ("alice", "/home/alice"),
            "others": [],
            "runs_as": "jarvis",
        }

    @pytest.mark.parametrize(
        "broken",
        [
            {"which": None},
            {"run": subprocess.CompletedProcess([], 1, "", "Failed to connect")},
            {"run": subprocess.CompletedProcess([], 0, "not json", "")},
            {"run": subprocess.TimeoutExpired("loginctl", 2)},
        ],
    )
    def test_loginctl_failing_names_no_one_rather_than_the_service_account(
        self, host, monkeypatch, broken
    ):
        host()
        if "which" in broken:
            monkeypatch.setattr(system_info.shutil, "which", lambda name: None)
        else:

            def run(args, **kwargs):
                if isinstance(broken["run"], Exception):
                    raise broken["run"]
                return broken["run"]

            monkeypatch.setattr(system_info.subprocess, "run", run)
        assert SystemInfo.get_users() == {
            "active": None,
            "others": [],
            "runs_as": "jarvis",
        }

    def test_off_linux_jarvis_own_account_is_the_user(self, host, monkeypatch):
        host(me="toufic", home="/Users/toufic")
        monkeypatch.setattr(system_info.platform, "system", lambda: "Darwin")
        assert SystemInfo.get_users() == {
            "active": ("toufic", "/Users/toufic"),
            "others": [],
            "runs_as": "toufic",
        }


@pytest.mark.unit
def test_every_root_prompt_names_the_users_on_the_machine_now(monkeypatch):
    users = {
        "active": ("toufic", "/home/toufic"),
        "others": [("alice", "/home/alice")],
        "runs_as": "jarvis",
    }
    monkeypatch.setattr(SystemInfo, "get_users", lambda: users)
    cm = ContextManager(provider=Mock(), system_prompt="RULES")

    system = cm.build_messages([], "list my Pictures folder")[0]["content"]
    assert system == (
        "RULES\n"
        "\n--- USERS ON THIS MACHINE ---\n"
        "Active user (the one talking to you): toufic, home /home/toufic\n"
        "Also logged in: alice (/home/alice)\n"
        "JARVIS runs as: jarvis"
    )

    users = {"active": None, "others": [("alice", "/home/alice")], "runs_as": "jarvis"}
    system = cm.build_messages([])[0]["content"]
    assert "Active user: unknown; ask whose home" in system
    assert "Logged in: alice (/home/alice)" in system
