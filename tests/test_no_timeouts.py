"""Shell commands are not killed on a clock (2026-10-23, Yakup's call).

A timeout kills a command wherever it is. Three jobs waiting on pacman's
"[Y/n]" died at theirs, and a system update cut off mid-install can leave the
machine unbootable. The ROOT prompt said to set a generous timeout "so it
isn't cut off at the ~30s default", a default no shell server has. Told
otherwise, the model on its own still set one in 2 of 5 runs. So a shell
tool's timeout becomes a reminder no later than that point.

run_job takes one command string, and the model passed execute_command's
{command, args} split in 2 of 5 runs, which runs the bare program.
"""

import asyncio
import logging

import pytest

import jarvis.core.confirmation_manager as cm
from jarvis.runtime import dispatch_flow
from jarvis.runtime.dispatch_flow import TIMEOUT_REMINDER_CAP, _no_timeouts


def _one(task):
    _no_timeouts([task])
    return task


@pytest.mark.unit
class TestTimeoutBecomesReminder:
    def test_a_long_timeout_becomes_a_capped_reminder(self):
        task = _one(
            {"tool": "execute_command", "params": {"command": "pacman", "timeout": 300}}
        )
        assert task == {
            "tool": "execute_command",
            "params": {"command": "pacman"},
            "remind_after": TIMEOUT_REMINDER_CAP,
        }

    def test_a_short_one_reminds_at_that_point(self):
        task = _one(
            {"tool": "execute_script", "params": {"script": "x", "timeout": 20}}
        )
        assert task["remind_after"] == 20 and "timeout" not in task["params"]

    def test_the_models_own_reminder_wins(self):
        task = _one(
            {
                "tool": "run_job",
                "params": {"command": "pacman -Syu", "job": "u", "timeout": 900},
                "remind_after": 30,
            }
        )
        assert task["remind_after"] == 30 and "timeout" not in task["params"]

    @pytest.mark.parametrize("value", [0, None, "soon", -5])
    def test_no_usable_timeout_adds_no_reminder(self, value):
        task = _one(
            {"tool": "execute_command", "params": {"command": "ls", "timeout": value}}
        )
        assert "timeout" not in task["params"] and not task.get("remind_after")

    def test_other_tools_keep_their_timeout(self):
        task = _one({"tool": "fetch", "params": {"url": "u", "timeout": 10}})
        assert task == {"tool": "fetch", "params": {"url": "u", "timeout": 10}}


@pytest.mark.unit
class TestRunJobCommand:
    def test_split_arguments_are_joined_back(self):
        task = _one(
            {
                "tool": "run_job",
                "params": {"command": "pacman", "args": ["-Syu"], "job": "u"},
            }
        )
        assert task["params"] == {"command": "pacman -Syu", "job": "u"}

    def test_the_command_stays_as_typed_and_arguments_are_quoted(self):
        task = _one(
            {
                "tool": "run_job",
                "params": {"command": "sudo pacman", "args": ["-S", "a b"], "job": "u"},
            }
        )
        assert task["params"]["command"] == "sudo pacman -S 'a b'"

    def test_execute_command_keeps_its_split(self):
        task = _one(
            {"tool": "execute_command", "params": {"command": "ls", "args": ["-l"]}}
        )
        assert task["params"] == {"command": "ls", "args": ["-l"]}


class _Adapter:
    is_connected = True

    def __init__(self):
        self.sent = None

    async def list_server_tools(self, server_id):
        return {"tools": []}

    async def get_server_manifest(self, server_id):
        return {"trustStatus": "community"}

    async def send_tasks(self, tasks, session_id=None):
        self.sent = tasks
        return {"output": "ok"}


@pytest.mark.unit
def test_what_dispatch_receives(monkeypatch):
    monkeypatch.setattr(dispatch_flow, "_THREAT_DECL_BY_SERVER", {})
    monkeypatch.setattr(dispatch_flow, "_STATEFUL_BY_SERVER", {})
    monkeypatch.setattr(cm.Config, "CONFIRMATION_MODE", "allow_all")
    monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)
    app = type("App", (), {})()
    app.dispatch = _Adapter()
    app.confirmation = cm.ConfirmationManager()
    task = {
        "server": "shell-system",
        "tool": "run_job",
        "params": {"command": "pacman", "args": ["-Syu"], "job": "u", "timeout": 300},
    }
    asyncio.run(dispatch_flow.dispatch_send(app, logging.getLogger("t"), [task]))
    (sent,) = app.dispatch.sent
    assert sent["params"] == {"command": "pacman -Syu", "job": "u"}
    assert sent["remind_after"] == TIMEOUT_REMINDER_CAP
