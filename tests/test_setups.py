"""Pending server setups (#254): install queues a setup instead of blocking on a form.

A server whose required setting is unset becomes a *pending setup*, stored like
the confirmation queue and filled in later from `jarvis setups fill` or the TUI.
Filling it in resumes the goal. Values go from the form to dmcp over stdin and
are never stored in the queue, sent over the socket, or shown to the model.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis import cli
from jarvis.core import setup_queue
from jarvis.core.setup_queue import SetupQueue, missing_required, setting_fields
from jarvis.runtime import io as runtime_io
from jarvis.runtime import lifecycle, root_actions

MANIFEST = {
    "name": "Demo",
    "summary": "A demo server",
    "configurableProperties": [
        {"key": "API_KEY", "label": "API key", "required": True, "sensitive": True},
        {"key": "REGION", "label": "Region", "required": True},
        {"key": "MODE", "label": "Mode", "default": "safe", "extra": "dropped"},
    ],
}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_queue.Config, "JARVIS_DATA_DIR", str(tmp_path))
    return tmp_path


# --- the queue --------------------------------------------------------------------------


@pytest.mark.unit
class TestQueue:
    def test_fields_keep_only_what_a_form_needs(self):
        fields = setting_fields(MANIFEST["configurableProperties"])
        assert fields[2] == {"key": "MODE", "label": "Mode", "default": "safe"}

    def test_missing_is_required_and_unset(self):
        props = MANIFEST["configurableProperties"]
        assert missing_required(props, {}) == ["API_KEY", "REGION"]
        assert missing_required(props, {"API_KEY": "k", "REGION": "  "}) == ["REGION"]
        assert missing_required(props, {"API_KEY": "k", "REGION": "eu"}) == []

    def test_add_find_pop_and_one_entry_per_server(self):
        queue = SetupQueue()
        goal = SimpleNamespace(id="g1", description="check my mail")
        first = queue.add("demo", MANIFEST, ["API_KEY"], goal)
        second = queue.add("demo", MANIFEST, ["API_KEY", "REGION"], goal)
        assert [s["setup_id"] for s in queue.list()] == [second.setup_id]
        assert queue.find("demo") is second and queue.find(second.setup_id) is second
        assert queue.find(first.setup_id) is None
        assert second.goal_description == "check my mail"
        assert queue.pop("demo") is second and queue.list() == []

    def test_survives_a_restart_and_stores_no_values(self, data_dir):
        path = str(setup_queue.store_path())
        queue = SetupQueue(store_path=path)
        added = queue.add(
            "demo", {**MANIFEST, "config": {"API_KEY": "s3cret"}}, ["REGION"]
        )
        assert "s3cret" not in (data_dir / "setups.json").read_text()
        restored = SetupQueue(store_path=path)
        assert restored.find("demo").setup_id == added.setup_id

    def test_a_corrupt_store_starts_empty(self, data_dir):
        (data_dir / "setups.json").write_text("{not json")
        assert SetupQueue(store_path=str(setup_queue.store_path())).list() == []


# --- install ---------------------------------------------------------------------------


def _app(manifest, goals=()):
    return SimpleNamespace(
        dispatch=SimpleNamespace(
            install_server=AsyncMock(return_value={}),
            get_server_manifest=AsyncMock(return_value=manifest),
            run_server_setup=AsyncMock(return_value={}),
            auto_index_server=AsyncMock(),
            list_server_tools=AsyncMock(return_value={"tools": []}),
        ),
        goals=SimpleNamespace(get_active_goals=lambda: list(goals)),
        setups=SetupQueue(),
        setup_ready_callback=None,
        _act_on_root_response=AsyncMock(),
    )


def _run(coro_fn, *args):
    ask = AsyncMock(return_value={"action": "respond"})
    with (
        patch.object(root_actions, "build_root_context", return_value=""),
        patch.object(root_actions, "emit_activity"),
        patch.object(root_actions, "get_embeddings", return_value=None),
        patch.object(root_actions, "format_server_docs", return_value="DOCS"),
        patch.object(root_actions, "ask_llm", ask),
    ):
        asyncio.run(coro_fn(*args))
    return ask.await_args.args[2] if ask.await_args else None


@pytest.mark.unit
class TestInstall:
    def test_a_missing_required_setting_is_queued_not_asked_for(self):
        app = _app(MANIFEST, [SimpleNamespace(id="g1", description="check my mail")])
        told = _run(
            root_actions._handle_install_server,
            app,
            Mock(),
            {"server_id": "demo"},
            0,
            5,
        )
        (pending,) = app.setups.list()
        assert pending["missing"] == ["API_KEY", "REGION"]
        assert pending["goal_description"] == "check my mail"
        app.dispatch.run_server_setup.assert_not_awaited()
        assert f"jarvis setups fill {pending['setup_id']}" in told
        assert "Never ask for these values in chat" in told

    def test_the_tui_is_offered_the_form(self):
        app = _app(MANIFEST)
        app.setup_ready_callback = AsyncMock()
        told = _run(
            root_actions._handle_install_server,
            app,
            Mock(),
            {"server_id": "demo"},
            0,
            5,
        )
        (offered,) = app.setup_ready_callback.await_args.args
        assert offered.server_id == "demo"
        assert "A setup form has opened in the TUI" in told

    def test_optional_settings_do_not_stop_the_install(self):
        optional = {"configurableProperties": [{"key": "MODE", "default": "safe"}]}
        app = _app(optional)
        told = _run(
            root_actions._handle_install_server,
            app,
            Mock(),
            {"server_id": "demo"},
            0,
            5,
        )
        assert app.setups.list() == []
        app.dispatch.run_server_setup.assert_awaited_once_with("demo")
        assert "INSTALL_RESULT" in told

    def test_required_settings_already_set_do_not_stop_it(self):
        app = _app({**MANIFEST, "config": {"API_KEY": "k", "REGION": "eu"}})
        _run(
            root_actions._handle_install_server,
            app,
            Mock(),
            {"server_id": "demo"},
            0,
            5,
        )
        assert app.setups.list() == []
        app.dispatch.run_server_setup.assert_awaited_once()


@pytest.mark.unit
class TestSetupResult:
    def _pending(self, app, manifest_now):
        pending = app.setups.add(
            "demo",
            MANIFEST,
            ["API_KEY"],
            SimpleNamespace(id="g", description="check my mail"),
        )
        app.dispatch.get_server_manifest = AsyncMock(return_value=manifest_now)
        return pending

    def test_filling_it_in_runs_setup_and_resumes(self):
        app = _app(MANIFEST)
        pending = self._pending(
            app, {**MANIFEST, "config": {"API_KEY": "k", "REGION": "eu"}}
        )
        told = _run(
            root_actions.on_setup_result,
            app,
            Mock(),
            {"type": "setup_completed", "id": pending.setup_id},
        )
        assert app.setups.list() == []
        app.dispatch.run_server_setup.assert_awaited_once_with("demo")
        assert "SETUP_COMPLETED" in told and "INSTALL_RESULT" in told
        assert "originally asked: check my mail" in told

    def test_still_missing_stays_pending(self):
        app = _app(MANIFEST)
        pending = self._pending(app, {**MANIFEST, "config": {"API_KEY": "k"}})
        told = _run(
            root_actions.on_setup_result,
            app,
            Mock(),
            {"type": "setup_completed", "id": pending.setup_id},
        )
        app.dispatch.run_server_setup.assert_not_awaited()
        assert app.setups.find("demo").missing == ["REGION"]
        assert "SETUP_PENDING" in told

    def test_cancelling_tells_the_model(self):
        app = _app(MANIFEST)
        pending = self._pending(app, MANIFEST)
        told = _run(
            root_actions.on_setup_result,
            app,
            Mock(),
            {"type": "cancel_setup", "id": "demo"},
        )
        assert app.setups.list() == []
        app.dispatch.run_server_setup.assert_not_awaited()
        assert "SETUP_CANCELLED" in told and pending.server_id in told

    def test_an_unknown_setup_is_ignored(self):
        app = _app(MANIFEST)
        assert (
            _run(
                root_actions.on_setup_result,
                app,
                Mock(),
                {"type": "setup_completed", "id": "nope"},
            )
            is None
        )


# --- the socket and the offline queue --------------------------------------------------


class _Writer:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(json.loads(data))

    async def drain(self):
        pass


@pytest.mark.unit
class TestSocket:
    def _app(self):
        app = SimpleNamespace(
            setups=SetupQueue(), events=SimpleNamespace(inject_setup_result=Mock())
        )
        pending = app.setups.add("demo", MANIFEST, ["API_KEY"])
        return app, pending

    def _ask(self, app, msg):
        writer = _Writer()
        handled = asyncio.run(runtime_io._handle_setup_query(app, msg, writer))
        return handled, writer.sent

    def test_list(self):
        app, pending = self._app()
        handled, sent = self._ask(app, {"type": "list_setups"})
        assert handled and sent[0]["setups"][0]["setup_id"] == pending.setup_id

    def test_completing_carries_only_the_id(self):
        app, pending = self._app()
        handled, sent = self._ask(
            app, {"type": "setup_completed", "id": "demo", "API_KEY": "x"}
        )
        app.events.inject_setup_result.assert_called_once_with(
            {"type": "setup_completed", "id": pending.setup_id}
        )
        assert sent[0]["type"] == "ack"

    def test_unknown_id_is_an_error_and_other_messages_pass_through(self):
        app, _ = self._app()
        handled, sent = self._ask(app, {"type": "cancel_setup", "id": "nope"})
        assert handled and sent[0]["type"] == "error"
        app.events.inject_setup_result.assert_not_called()
        assert self._ask(app, {"type": "list_confirmations"}) == (False, [])


@pytest.mark.unit
def test_offline_completions_are_applied_at_startup(data_dir):
    from jarvis.core.confirmation_manager import queue_decision

    app = SimpleNamespace(setups=SetupQueue())
    done = app.setups.add("demo", MANIFEST, ["API_KEY"])
    kept = app.setups.add("other", MANIFEST, ["API_KEY"])
    queue_decision(
        {"type": "setup_completed", "id": done.setup_id},
        setup_queue.completion_queue_path(),
    )
    assert lifecycle.apply_queued_setup_completions(app, Mock()) == 1
    assert [s["setup_id"] for s in app.setups.list()] == [kept.setup_id]
    assert not setup_queue.completion_queue_path().exists()


# --- the CLI ---------------------------------------------------------------------------


SETUP = {
    "setup_id": "ab12cd34",
    "server_id": "demo",
    "server_name": "Demo",
    "summary": "",
    "missing": ["API_KEY"],
    "fields": setting_fields(MANIFEST["configurableProperties"]),
}


def _answers(*replies, prompts=None):
    replies = list(replies)

    def answer(prompt=""):
        if prompts is not None:
            prompts.append(prompt)
        return replies.pop(0)

    return answer


@pytest.mark.unit
class TestPrompts:
    def test_required_is_asked_again_secret_is_hidden_and_masked(self, capsys):
        prompts = []
        values = cli._prompt_setup_values(
            SETUP,
            ask=_answers("", "", "y", prompts=prompts),  # REGION, MODE kept; save
            ask_secret=_answers("", "s3cret", prompts=prompts),  # empty, then the key
        )
        assert values == {"API_KEY": "s3cret"}
        out = capsys.readouterr().out
        assert "cannot run without this one" in out
        assert "s3cret" not in out and "••••" in out
        assert prompts[:2] == ["API key (required): ", "API key (required): "]
        assert "Region (already set; Enter keeps it): " in prompts
        assert "Mode [safe]: " in prompts

    def test_declining_saves_nothing(self):
        assert (
            cli._prompt_setup_values(
                SETUP, ask=_answers("eu", "", "n"), ask_secret=_answers("k")
            )
            is None
        )


def _completed(returncode=0):
    return SimpleNamespace(returncode=returncode, stderr="", stdout="")


@pytest.mark.unit
class TestFill:
    def _fill(self, monkeypatch, live, ipc_answer=None):
        calls = []
        monkeypatch.setattr(cli, "_load_setups", lambda: ([SETUP], live))
        monkeypatch.setattr(
            cli,
            "_prompt_setup_values",
            lambda setup: {"API_KEY": "s3cret", "REGION": "eu"},
        )
        ipc = Mock(return_value=ipc_answer)
        monkeypatch.setattr(cli, "_ipc_request", ipc)

        def run(args, **kw):
            calls.append((args, kw))
            return _completed()

        monkeypatch.setattr("subprocess.run", run)
        params = Mock()
        monkeypatch.setattr("jarvis.core.params_store.ParamsStore", lambda sid: params)
        cli._setups_fill("demo")
        return calls, ipc, params

    def test_values_go_over_stdin_and_the_daemon_resumes(self, monkeypatch, data_dir):
        calls, ipc, params = self._fill(
            monkeypatch, True, {"type": "ack", "message": "ok"}
        )
        sets = [(a, kw) for a, kw in calls if a[1:3] == ["config", "demo"]]
        assert [a[4:] for a, _ in sets] == [
            ["API_KEY", "--stdin"],
            ["REGION", "--stdin"],
        ]
        assert all("s3cret" not in a for a, _ in sets)
        assert sets[0][1]["input"] == "s3cret"
        params.set_many.assert_called_once_with({"REGION": "eu"})
        ipc.assert_called_once_with({"type": "setup_completed", "id": "ab12cd34"})
        assert not any(a[1] == "setup" for a, _ in calls)

    def test_offline_it_runs_setup_and_queues_the_completion(
        self, monkeypatch, data_dir
    ):
        calls, ipc, _ = self._fill(monkeypatch, False)
        assert any(a[1:] == ["setup", "demo"] for a, _ in calls)
        queued = json.loads(setup_queue.completion_queue_path().read_text())
        assert queued == [{"type": "setup_completed", "id": "ab12cd34"}]

    def test_offline_cancel_is_queued(self, monkeypatch, data_dir):
        monkeypatch.setattr(cli, "_load_setups", lambda: ([SETUP], False))
        cli._setups_cancel("ab12cd34")
        queued = json.loads(setup_queue.completion_queue_path().read_text())
        assert queued == [{"type": "cancel_setup", "id": "ab12cd34"}]


# --- the TUI ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTuiForm:
    @pytest.fixture(autouse=True)
    def _textual(self):
        pytest.importorskip("textual")

    def _finish(self, confirmed, values):
        from jarvis.tui import app as tui_app
        from jarvis.tui.server_config_modal import ConfigModalResult

        pending = SetupQueue().add("demo", MANIFEST, ["API_KEY"])
        host = SimpleNamespace(
            jarvis=SimpleNamespace(
                dispatch=SimpleNamespace(set_server_config=AsyncMock()),
                events=SimpleNamespace(inject_setup_result=Mock()),
            ),
            _append_log=Mock(),
            _escape=lambda s: s,
        )
        params = Mock()

        async def run():
            future = asyncio.get_running_loop().create_future()
            future.set_result(ConfigModalResult(confirmed=confirmed, values=values))
            await tui_app.JarvisTUI._finish_setup_form(host, pending, future)

        with patch("jarvis.core.params_store.ParamsStore", lambda sid: params):
            asyncio.run(run())
        return host, pending, params

    def test_saving_writes_values_and_resumes(self):
        host, pending, params = self._finish(
            True, {"API_KEY": "k", "REGION": "eu", "MODE": ""}
        )
        host.jarvis.dispatch.set_server_config.assert_awaited_once_with(
            "demo", {"API_KEY": "k", "REGION": "eu"}
        )
        params.set_many.assert_called_once_with({"REGION": "eu"})
        host.jarvis.events.inject_setup_result.assert_called_once_with(
            {"type": "setup_completed", "id": pending.setup_id}
        )

    def test_closing_the_form_leaves_it_pending(self):
        host, _, _ = self._finish(False, {})
        host.jarvis.events.inject_setup_result.assert_not_called()
        assert "still pending" in host._append_log.call_args.args[0]
