"""JARVIS's own CLI never runs through a shell server.

The owner's run (2026-11-15): analyze_image found no vision provider, its
VISION_ERROR named `jarvis providers add … --vision`, and the model sent that to
the root shell server. The owner approved; the CLI ran as root and rewrote
~/.config/jarvis/providers.json as root:root 0600, and the daemon (the user) hit
PermissionError on every provider read after it.
"""

import asyncio
import logging
from unittest.mock import Mock, patch

import pytest

import jarvis.core.confirmation_manager as cm
from jarvis.core.confirmation_manager import ConfirmationManager
from jarvis.dispatch.goal_manager import GoalManager
from jarvis.runtime import dispatch_flow, root_handlers

_LOG = logging.getLogger("test")
ROOT = "com.github.yakupatahanov.jarvis-shell-system-mcp"
USER = "com.github.yakupatahanov.jarvis-shell-mcp"
_SHELL = ["execute_command", "execute_script", "run_job", "send_input", "read_output"]


class _Adapter:
    is_connected = True

    def __init__(self, forbid=()):
        self.sent = None
        self.forbid = forbid

    async def list_server_tools(self, server_id):
        return {
            "server": server_id,
            "tools": [{"name": t, "inputSchema": {}} for t in _SHELL],
        }

    async def get_server_manifest(self, server_id):
        return {
            "scope": "system" if server_id == ROOT else "user",
            "trustStatus": "community",
            "tools": [
                {
                    "name": t,
                    "threat_level": "forbidden" if t in self.forbid else "dangerous",
                }
                for t in _SHELL
            ],
        }

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
    # allow_all: the confirmation the owner gave is not what stops it.
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


def _refused(app, result):
    assert app.dispatch.sent is None, "nothing in the batch is sent"
    error = result["error"]
    assert "JARVIS's own jarvis command" in error
    assert "Settings" in error and "Do not retry" in error
    return error


@pytest.mark.unit
class TestTheIncident:
    def test_the_batch_as_the_model_sent_it(self, monkeypatch):
        app, result = _send(
            monkeypatch,
            {
                "server": ROOT,
                "tool": "execute_command",
                "params": {
                    "command": "jarvis providers add --type llamacpp --model "
                    "ggml-org/gemma-3-4b-it-GGUF --vision"
                },
            },
        )
        assert "providers add" in _refused(app, result)

    def test_the_batch_as_dispatch_logged_it(self, monkeypatch):
        app, result = _send(
            monkeypatch,
            _cmd(
                ROOT,
                "jarvis",
                "providers",
                "add",
                "--type",
                "llamacpp",
                "--model",
                "ggml-org/gemma-3-4b-it-GGUF",
                "--vision",
            ),
        )
        _refused(app, result)

    def test_the_whole_batch_is_refused(self, monkeypatch):
        app, result = _send(
            monkeypatch,
            _cmd(USER, "uname", "-r"),
            _cmd(USER, "jarvis", "config", "set", "CONFIRMATION_MODE", "allow_all"),
        )
        _refused(app, result)


@pytest.mark.unit
class TestEveryWayToRunIt:
    @pytest.mark.parametrize(
        "command, args",
        [
            ("/home/u/Project-JARVIS/.venv/bin/jarvis", ["providers", "list"]),
            ("sudo", ["-E", "jarvis", "providers", "add"]),
            ("sudo", ["-u", "toufic", "jarvis", "config", "set", "X", "1"]),
            ("sudo", ["jarvis providers add --type api"]),
            ("pkexec", ["/usr/bin/jarvis", "providers"]),
            ("doas", ["jarvis", "sudo", "enable"]),
            ("env", ["HOME=/root", "jarvis", "providers"]),
            ("/usr/bin/env", ["-i", "jarvis"]),
            ("python3", ["-m", "jarvis", "providers"]),
            ("python", ["-u", "-m", "jarvis.cli", "providers"]),
            ("/home/u/.venv/bin/python3.12", ["-mjarvis"]),
            ("bash", ["-lc", "cd /tmp && jarvis providers add --vision"]),
            ("sh", ["-c", "sudo jarvis config set X 1"]),
        ],
    )
    def test_on_either_shell_server(self, monkeypatch, command, args):
        for server in (ROOT, USER):
            app, result = _send(monkeypatch, _cmd(server, command, *args))
            _refused(app, result)

    @pytest.mark.parametrize(
        "tool, key, text",
        [
            ("run_job", "command", "sudo -E jarvis providers add --type llamacpp"),
            ("run_job", "command", "cd ~ && FOO=1 jarvis providers list | head"),
            ("execute_script", "script", "set -e\necho hi\nnohup jarvis run &"),
            ("execute_script", "script", "if true; then ~/.local/bin/jarvis x; fi"),
            ("execute_script", "script", "echo $(python3 -m jarvis providers)"),
        ],
    )
    def test_inside_a_command_line_or_script(self, monkeypatch, tool, key, text):
        app, result = _send(
            monkeypatch,
            {"server": USER, "tool": tool, "params": {"job": "j", key: text}},
        )
        _refused(app, result)


@pytest.mark.unit
class TestCommandsAboutJarvisStillRun:
    @pytest.mark.parametrize(
        "command, args",
        [
            ("ls", ["-l", "/home/u/.config/jarvis"]),
            ("cat", ["/home/u/.config/jarvis/providers.json"]),
            ("systemctl", ["--user", "restart", "jarvis"]),
            ("journalctl", ["--user", "-u", "jarvis", "-n", "50"]),
            ("command", ["-v", "jarvis"]),
            ("which", ["jarvis"]),
            ("pgrep", ["-f", "jarvis"]),
            ("jarvis-app", []),
            ("python3", ["-m", "pip", "show", "jarvis"]),
            ("bash", ["-c", "grep -r jarvis /etc/hosts"]),
        ],
    )
    def test_execute_command(self, monkeypatch, command, args):
        app, result = _send(monkeypatch, _cmd(USER, command, *args))
        assert "error" not in result and len(app.dispatch.sent) == 1

    def test_a_script_that_only_mentions_it(self, monkeypatch):
        app, result = _send(
            monkeypatch,
            {
                "server": USER,
                "tool": "execute_script",
                "params": {"script": "# jarvis providers add\nls ~/.config/jarvis"},
            },
        )
        assert "error" not in result and len(app.dispatch.sent) == 1


class _RestartedApp:
    def __init__(self, confirmation, tmp_path, forbid=()):
        self.confirmation = confirmation
        self.dispatch = _Adapter(forbid)
        self.goals = GoalManager(archive_dir=str(tmp_path))
        self.llm = object()
        self._gui_clients = None
        self.told = None

    async def _act_on_root_response(self, response):
        pass


def _approve_after_restart(monkeypatch, tmp_path, task, forbid=()):
    """Hold `task` in confirmations.json as a daemon without the guard did,
    then approve it in a restarted daemon (the offline queue resolves the same)."""
    for cache in ("_THREAT_DECL_BY_SERVER", "_STATEFUL_BY_SERVER", "_ROOT_BY_SERVER"):
        monkeypatch.setattr(dispatch_flow, cache, {})
    for module in (dispatch_flow, root_handlers):
        monkeypatch.setattr(module, "emit_activity", lambda *a, **k: None)
    monkeypatch.setattr(root_handlers, "build_root_context", lambda a, l, **k: "")
    store = tmp_path / "confirmations.json"
    before = ConfirmationManager(store_path=str(store))
    before.set_event_injector(Mock())
    tty = patch.object(ConfirmationManager, "_has_tty", return_value=False)
    desktop = patch.object(
        ConfirmationManager, "_has_desktop_notifications", return_value=False
    )
    with tty, desktop:
        asyncio.run(
            before.request_confirmation(
                request_id="r1",
                tasks=[task],
                tools_needing_confirmation=[
                    {"tool_name": f"{task['server']}.{task['tool']}", "task": task}
                ],
                approved_tasks=[],
                denied_tools=[],
                timeout=0,
                session_id="goal-gone",
            )
        )
    app = _RestartedApp(ConfirmationManager(store_path=str(store)), tmp_path, forbid)

    async def _ask(a, l, context, **k):
        app.told = context
        return {"action": "respond", "output": "ok"}

    monkeypatch.setattr(root_handlers, "ask_llm", _ask)
    asyncio.run(
        root_handlers.on_confirmation_response(
            app, _LOG, {"id": "r1", "approved": True}
        )
    )
    return app


@pytest.mark.unit
class TestApprovedBeforeTheGuard:
    def test_the_incident_held_over_a_restart_is_refused(self, monkeypatch, tmp_path):
        incident = _cmd(
            ROOT,
            "jarvis",
            "providers",
            "add",
            "--type",
            "llamacpp",
            "--model",
            "ggml-org/gemma-3-4b-it-GGUF",
            "--vision",
        )
        app = _approve_after_restart(monkeypatch, tmp_path, incident)
        assert app.dispatch.sent is None, "nothing in the batch is sent"
        assert "DISPATCH_ERROR" in app.told
        assert "JARVIS's own jarvis command" in app.told

    def test_a_tool_now_forbidden_is_refused(self, monkeypatch, tmp_path):
        task = _cmd(USER, "uname", "-r")
        app = _approve_after_restart(
            monkeypatch, tmp_path, task, forbid=("execute_command",)
        )
        assert app.dispatch.sent is None, "nothing in the batch is sent"
        assert "classed FORBIDDEN" in app.told

    def test_an_ordinary_one_still_runs(self, monkeypatch, tmp_path):
        task = _cmd(USER, "uname", "-r")
        app = _approve_after_restart(monkeypatch, tmp_path, task)
        assert app.dispatch.sent == [task]
        assert app.told is None
