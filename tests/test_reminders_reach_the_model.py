"""A job waiting on a question reaches the model (2026-10-23).

"Can you run the updates on my laptop?" became run_job `pacman -Syu` without
`--noconfirm`. pacman asked "[Y/n]" and the job sat there until its timeout
killed it, three times, while the designed path is: dispatch reminds, the
reminder carries the job's output, the model sees the question and asks the
user. No reminder ever came, for two reasons fixed here:

- the model put `remind_after` inside the tool's arguments, where dispatch
  never looks; it is now lifted onto the task;
- JARVIS read manifests only from the user's install directory, so the
  system-scope shell server (in /usr/share/mcp/installed) had none: no
  threat levels and "no readable manifest" on every dispatch.

(The third, an installed server older than the registry's `blocking: true`
for run_job, is fixed by updating the server.)
"""

import asyncio
import json
import logging

import pytest

from jarvis.core.command_parser import TaskParser
from jarvis.dispatch import dmcp_registry


def _task(task):
    parsed = TaskParser.parse({"action": "dispatch", "tasks": [task]})
    (only,) = parsed["tasks"]
    return only


@pytest.mark.unit
class TestRemindAfter:
    def test_inside_the_arguments_it_moves_onto_the_task(self):
        task = _task(
            {
                "server": "shell",
                "tool": "run_job",
                "params": {
                    "command": "pacman -Syu",
                    "job": "update",
                    "remind_after": 30,
                },
            }
        )
        assert task["remind_after"] == 30
        assert task["params"] == {"command": "pacman -Syu", "job": "update"}

    def test_on_the_task_it_stays_and_the_arguments_are_untouched(self):
        params = {"command": "ls", "remind_after": 5}
        task = _task({"server": "s", "tool": "t", "params": params, "remind_after": 60})
        assert task["remind_after"] == 60 and task["params"] is params

    def test_absent_is_no_preference(self):
        task = _task({"server": "s", "tool": "t", "params": {"a": 1}})
        assert task["remind_after"] is None and task["params"] == {"a": 1}


def _manifest(directory, server_id, body):
    path = directory / server_id
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps(body))


@pytest.mark.unit
class TestSystemScopeManifests:
    def _paths(self, monkeypatch, tmp_path):
        paths = {
            "data_dir": str(tmp_path / "user"),
            "system_install_dir": str(tmp_path / "system"),
        }
        monkeypatch.setattr(dmcp_registry, "_dmcp_paths", lambda: paths)
        return tmp_path / "user" / "installed", tmp_path / "system"

    def _read(self, server_id):
        return asyncio.run(
            dmcp_registry.get_server_manifest(logging.getLogger("t"), server_id)
        )

    def test_a_system_server_has_its_manifest(self, monkeypatch, tmp_path):
        _, system = self._paths(monkeypatch, tmp_path)
        _manifest(
            system,
            "shell-system",
            {"tools": [{"name": "run_job", "blocking": True}]},
        )
        assert self._read("shell-system")["tools"][0]["blocking"] is True

    def test_the_users_install_comes_first(self, monkeypatch, tmp_path):
        user, system = self._paths(monkeypatch, tmp_path)
        _manifest(user, "both", {"scope": "user"})
        _manifest(system, "both", {"scope": "system"})
        assert self._read("both") == {"scope": "user"}

    def test_the_registry_clone_is_the_last_resort(self, monkeypatch, tmp_path):
        self._paths(monkeypatch, tmp_path)
        _manifest(tmp_path / "user" / "registry", "new", {"from": "registry"})
        assert self._read("new") == {"from": "registry"}
        assert self._read("missing") == {}

    def test_without_dmcp_paths_linux_still_looks_in_usr_share(self, monkeypatch):
        monkeypatch.setattr(dmcp_registry, "_dmcp_paths", lambda: {})
        monkeypatch.setattr(dmcp_registry.sys, "platform", "linux")
        dirs = [str(d) for d in dmcp_registry._install_dirs()]
        assert dirs[-1] == "/usr/share/mcp/installed"
