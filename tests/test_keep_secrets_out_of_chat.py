"""Secrets must not travel through the conversation (#242).

Before this, a key the user typed in answer to "what's your API key?" was
logged, archived as a goal, stored in contextor's memory (searched on every
later prompt), sent to the model, echoed in the model's own configure_server
call, and written to a second plaintext file. On the reference machine a
GitHub token was found in four files and an API key in the goal archive,
after the user had deleted sessions repeatedly.

These tests assert on where a secret can and cannot end up -- not on whether
configuration eventually succeeds, which it always did.
"""

import logging
import tomllib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.core import params_store
from jarvis.core.output_manager import OutputManager
from jarvis.core.secret_scrubber import (
    REDACTING_FILTER,
    redact_secrets,
    redaction_notice,
)
from jarvis.runtime import root_actions, root_handlers, sync_ask
from jarvis.runtime.root_context import configure_call_hint

GITHUB = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
BRAVE = "BSA" + "kQ3xLm9Pz7Wv2Rt5Yu8Io1Gh4Df6"


def _leaked(obj, secret):
    """True if `secret` appears anywhere in a mock's recorded calls."""
    return secret in repr(obj.mock_calls if hasattr(obj, "mock_calls") else obj)


# --- the scrubber --------------------------------------------------------------


@pytest.mark.unit
class TestScrubber:
    @pytest.mark.parametrize(
        "secret,label",
        [
            (GITHUB, "GitHub token"),
            ("github_pat_" + "x" * 60, "GitHub token"),
            ("glpat-" + "a" * 20, "GitLab token"),
            ("sk-ant-api03-" + "b" * 40, "Anthropic API key"),
            ("sk-proj-" + "c" * 48, "OpenAI API key"),
            ("sk-" + "D" * 48, "OpenAI API key"),
            (BRAVE, "Brave API key"),
            ("xoxb-1234567890-abcdefghij", "Slack token"),
            ("AIza" + "e" * 35, "Google API key"),
            ("AKIA" + "F" * 16, "AWS access key"),
            ("hf_" + "g" * 34, "Hugging Face token"),
            ("ntn_" + "h" * 46, "Notion token"),
            ("123456789:" + "i" * 35, "Telegram bot token"),
            ("eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4", "JSON web token"),
            (
                "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
                "private key",
            ),
        ],
    )
    def test_known_credential_formats_are_redacted(self, secret, label):
        clean, found = redact_secrets(f"here you go: {secret} thanks")
        assert secret not in clean
        assert f"[redacted {label}]" in clean
        assert found == [label]

    @pytest.mark.parametrize(
        "text",
        [
            "commit 3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a3f fixed it",
            "request 550e8400-e29b-41d4-a716-446655440000 timed out",
            "git checkout sk-feature-add-login-page-now",
            "set secret_key_for_the_database_connection in settings",
            "the Notion page at https://notion.so/My-Page-0123456789abcdef",
            "base64 payload aGVsbG8gd29ybGQgdGhpcyBpcyBhIHRlc3Q=",
        ],
    )
    def test_ordinary_high_entropy_text_is_left_alone(self, text):
        # Hashes, UUIDs, slugs and payloads are legitimate in a conversation
        # about code. Redacting them would break the conversation for nothing.
        assert redact_secrets(text) == (text, [])

    def test_every_secret_in_a_message_is_removed_and_labelled_once(self):
        clean, found = redact_secrets(f"{GITHUB} and {BRAVE} and {GITHUB} again")
        assert GITHUB not in clean and BRAVE not in clean
        assert found == ["GitHub token", "Brave API key"]

    def test_the_notice_names_what_was_removed_but_never_repeats_it(self):
        notice = redaction_notice(["GitHub token", "Brave API key"])
        assert "GitHub token" in notice and "Brave API key" in notice
        assert "ghp_" not in notice and "BSA" not in notice


# --- the log ---------------------------------------------------------------------


@pytest.mark.unit
class TestLogFilter:
    def _capture(self, logger):
        records = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(handler.format(record))
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        return records

    def test_a_logged_secret_is_scrubbed_before_any_handler_writes_it(self):
        logger = logging.getLogger("test.secret.filter.plain")
        logger.propagate = False
        logger.addFilter(REDACTING_FILTER)
        out = self._capture(logger)
        # The socket reader logs the first 80 characters of every message,
        # which is enough to hold a whole GitHub token.
        logger.warning(
            f"JARVIS: Socket input: {('Here is your key: ' + GITHUB)[:80]}..."
        )
        assert out and GITHUB not in out[0] and "[redacted GitHub token]" in out[0]

    def test_percent_style_arguments_are_scrubbed_too(self):
        logger = logging.getLogger("test.secret.filter.args")
        logger.propagate = False
        logger.addFilter(REDACTING_FILTER)
        out = self._capture(logger)
        logger.warning("token is %s", BRAVE)
        assert BRAVE not in out[0]

    def test_jarvis_loggers_carry_the_filter(self):
        from jarvis.core.logger import JarvisLogger

        assert REDACTING_FILTER in JarvisLogger.get_logger("jarvis.test.secret").filters


# --- the input funnel ---------------------------------------------------------------


def _chat_app():
    # spec'd: a bare Mock accepted `output_manager.display`, which does not
    # exist, so these tests passed while the real daemon crashed on it.
    return SimpleNamespace(
        llm=Mock(),
        output_manager=Mock(spec=OutputManager),
        goals=Mock(get_active_goals=Mock(return_value=[])),
        sessions=Mock(current_id="s1"),
        contextor=Mock(),
        _act_on_root_response=AsyncMock(),
    )


@pytest.mark.unit
class TestInputFunnel:
    @pytest.mark.asyncio
    async def test_a_pasted_key_reaches_no_log_goal_memory_or_model(self):
        app, logger = _chat_app(), Mock()
        with (
            patch.object(root_handlers, "set_gui_state", new=AsyncMock()),
            patch.object(
                root_handlers, "build_root_context", return_value="ctx"
            ) as build,
            patch.object(root_handlers, "emit_activity"),
            patch.object(root_handlers, "ask_llm", new=AsyncMock(return_value="{}")),
        ):
            await root_handlers.on_user_input(
                app, logger, f"Here is your key: {GITHUB}"
            )

        for where, obj in (
            ("the log", logger),
            ("the goal tree", app.goals),
            ("contextor's memory", app.contextor),
            ("the model's context", build),
        ):
            assert not _leaked(obj, GITHUB), f"the token reached {where}"
        app.goals.add_goal.assert_called_once_with(
            "Here is your key: [redacted GitHub token]"
        )
        shown = app.output_manager.handle_response.call_args.args[0]["output"]
        assert "GitHub token" in shown and GITHUB not in shown

    @pytest.mark.asyncio
    async def test_slash_commands_get_the_raw_text_but_the_log_does_not(self):
        app, logger = _chat_app(), Mock()
        with (
            patch.object(root_handlers, "set_gui_state", new=AsyncMock()),
            patch.object(
                root_handlers, "handle_slash_command", return_value=True
            ) as slash,
        ):
            await root_handlers.on_user_input(app, logger, f"/something {GITHUB}")
        slash.assert_called_once_with(app, f"/something {GITHUB}")
        assert not _leaked(logger, GITHUB)
        app.goals.add_goal.assert_not_called()

    def test_jarvis_ask_scrubs_the_prompt_the_same_way(self):
        app, logger = _chat_app(), Mock()
        app.llm.switch_mode = Mock()
        app._build_root_context = Mock(return_value="ctx")
        app.task_parser = Mock(
            parse=Mock(return_value={"action": "respond", "output": "ok"})
        )
        with (
            patch.object(sync_ask, "ask_llm_sync", return_value="{}"),
            patch.object(sync_ask, "persist_assistant_turn"),
        ):
            sync_ask.sync_ask(app, logger, f"my token is {BRAVE}")
        for obj in (logger, app.goals, app.contextor, app._build_root_context):
            assert not _leaked(obj, BRAVE)


# --- configure_server and install_server ----------------------------------------------


MANIFEST = {
    "name": "Demo",
    "configurableProperties": [
        {"key": "API_KEY", "sensitive": True, "required": True},
        {"key": "BASE_URL", "required": True},
    ],
}


def _server_app(tmp_path, *, modal=None):
    return SimpleNamespace(
        dispatch=SimpleNamespace(
            get_server_manifest=AsyncMock(return_value=MANIFEST),
            set_server_config=AsyncMock(),
            _sanitize_config_key=lambda k: k,
        ),
        config_modal_callback=modal,
        _act_on_root_response=AsyncMock(),
    )


@pytest.fixture
def params_file(tmp_path, monkeypatch):
    path = tmp_path / "jarvis_params.toml"
    monkeypatch.setattr(params_store, "_params_path", lambda: path)
    return path


async def _configure(app, config):
    with (
        patch.object(root_actions, "build_root_context", return_value=""),
        patch.object(root_actions, "emit_activity"),
        patch.object(root_actions, "ask_llm", new=AsyncMock(return_value="{}")) as ask,
    ):
        await root_actions._handle_configure_server(
            app, Mock(), {"server_id": "demo", "config": config}, 0, 5
        )
    return ask.call_args.args[2]  # what the model is told next


@pytest.mark.unit
class TestConfigureServer:
    @pytest.mark.asyncio
    async def test_a_secret_is_collected_in_the_form_not_taken_from_the_model(
        self, tmp_path, params_file, monkeypatch
    ):
        monkeypatch.setenv("DISPLAY", ":0")
        form = AsyncMock(return_value={"API_KEY": "from-the-form"})
        app = _server_app(tmp_path)
        with patch("jarvis.ui.config_prompt.prompt_configurable_properties", form):
            # Even a real-looking value from the model is discarded: for the
            # model to hold it, it came through the conversation.
            told = await _configure(app, {"API_KEY": GITHUB, "BASE_URL": "https://x"})

        app.dispatch.set_server_config.assert_awaited_once_with(
            "demo", {"BASE_URL": "https://x", "API_KEY": "from-the-form"}
        )
        assert GITHUB not in repr(app.dispatch.set_server_config.mock_calls)
        assert "from-the-form" not in told and "secure form" in told
        assert "from-the-form" not in params_file.read_text()
        assert "https://x" in params_file.read_text()

    @pytest.mark.asyncio
    async def test_the_tui_modal_is_the_form_when_there_is_one(
        self, tmp_path, params_file
    ):
        async def modal(server_id, name, desc, props, saved, future):
            future.set_result(
                SimpleNamespace(
                    confirmed=True, values={"API_KEY": "typed"}, missing_required=[]
                )
            )

        app = _server_app(tmp_path, modal=AsyncMock(side_effect=modal))
        await _configure(app, {"API_KEY": ""})
        app.config_modal_callback.assert_awaited_once()
        app.dispatch.set_server_config.assert_awaited_once_with(
            "demo", {"API_KEY": "typed"}
        )

    @pytest.mark.asyncio
    async def test_with_no_form_available_the_model_is_told_never_to_ask_in_chat(
        self, tmp_path, params_file, monkeypatch
    ):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        app = _server_app(tmp_path)
        told = await _configure(app, {"API_KEY": ""})
        app.dispatch.set_server_config.assert_not_awaited()
        assert "jarvis tui" in told and "Never ask for a secret value in chat" in told

    @pytest.mark.asyncio
    async def test_a_cancelled_form_is_not_followed_by_a_request_in_chat(
        self, tmp_path, params_file, monkeypatch
    ):
        monkeypatch.setenv("DISPLAY", ":0")
        app = _server_app(tmp_path)
        with patch(
            "jarvis.ui.config_prompt.prompt_configurable_properties",
            AsyncMock(return_value=None),
        ):
            told = await _configure(app, {"API_KEY": ""})
        assert "cancelled" in told and "Do not ask for these values in chat" in told

    @pytest.mark.asyncio
    async def test_non_secret_placeholders_are_still_sent_back_to_the_user(
        self, tmp_path, params_file
    ):
        app = _server_app(tmp_path)
        told = await _configure(app, {"BASE_URL": "<your server url>"})
        assert "CONFIGURE_BLOCKED" in told and "not secrets" in told
        app.dispatch.set_server_config.assert_not_awaited()


@pytest.mark.unit
class TestInstallServer:
    @pytest.mark.asyncio
    async def test_outside_the_tui_install_now_collects_config_in_the_form(
        self, tmp_path, params_file, monkeypatch
    ):
        # Before: no TUI meant no form at all, so the server was installed
        # unconfigured and the model asked for its key later -- in the chat.
        monkeypatch.setenv("DISPLAY", ":0")
        app = _server_app(tmp_path)
        app.dispatch.install_server = AsyncMock(return_value={})
        app.dispatch.run_server_setup = AsyncMock(return_value={})
        app.dispatch.auto_index_server = AsyncMock()
        app.dispatch.list_server_tools = AsyncMock(return_value={"tools": []})
        form = AsyncMock(return_value={"API_KEY": "k", "BASE_URL": "https://x"})
        with (
            patch("jarvis.ui.config_prompt.prompt_configurable_properties", form),
            patch.object(root_actions, "build_root_context", return_value=""),
            patch.object(root_actions, "emit_activity"),
            patch.object(root_actions, "get_embeddings", return_value=None),
            patch.object(root_actions, "ask_llm", new=AsyncMock(return_value="{}")),
        ):
            await root_actions._handle_install_server(
                app, Mock(), {"server_id": "demo"}, 0, 5
            )

        form.assert_awaited_once()
        app.dispatch.set_server_config.assert_awaited_once_with(
            "demo", {"API_KEY": "k", "BASE_URL": "https://x"}
        )
        saved = params_file.read_text()
        assert "https://x" in saved and '"k"' not in saved


# --- what the model is coached to send --------------------------------------------------


@pytest.mark.unit
def test_the_configure_hint_leaves_secrets_empty():
    hint = "\n".join(configure_call_hint("demo", MANIFEST["configurableProperties"]))
    assert '"API_KEY": ""' in hint and '"BASE_URL": "<value>"' in hint
    assert "Never ask for a secret in chat" in hint


# --- the TUI form ------------------------------------------------------------------------


@pytest.mark.unit
def test_the_tui_form_does_not_save_secret_keystrokes(params_file):
    from jarvis.tui.server_config_modal import ServerConfigModal

    modal = object.__new__(ServerConfigModal)
    modal._server_id = "demo"
    modal._props = MANIFEST["configurableProperties"]
    modal._values = {}
    modal.query_one = lambda *a, **k: Mock()
    modal.on__field_changed(SimpleNamespace(key="API_KEY", value="s3cret-typed"))
    modal.on__field_changed(SimpleNamespace(key="BASE_URL", value="https://x"))
    saved = params_file.read_text()
    assert "s3cret-typed" not in saved and "https://x" in saved


# --- the params store ---------------------------------------------------------------------


@pytest.mark.unit
class TestParamsStore:
    def test_a_dotted_server_id_round_trips(self, params_file):
        store = params_store.ParamsStore("io.github.missionsquad.mcp-github")
        store.set_many({"HOST": "github.com"})
        params_store.ParamsStore("com.example.other").set("URL", 'q"uote\\slash')
        store.set("OWNER", "me")
        assert store.get() == {"HOST": "github.com", "OWNER": "me"}
        assert params_store.ParamsStore("com.example.other").get() == {
            "URL": 'q"uote\\slash'
        }
        assert tomllib.loads(params_file.read_text())

    def test_the_corrupted_file_the_old_writer_left_reads_empty_and_heals(
        self, params_file
    ):
        # Exactly the shape found on the reference machine, values replaced.
        params_file.write_text(
            "[io]\n"
            "github = \"{'missionsquad': {'mcp-github': {'TOKEN': 'OLD'}}}\"\n\n"
            "[io.github.missionsquad.mcp-github]\n"
            'TOKEN = "OLD"\n'
        )
        store = params_store.ParamsStore("io.github.missionsquad.mcp-github")
        assert store.get() == {}
        store.set("HOST", "github.com")
        healed = params_file.read_text()
        assert tomllib.loads(healed) and "OLD" not in healed

    def test_unquoted_nested_sections_are_dropped_not_stringified(self, params_file):
        params_file.write_text('[io.github.x]\nTOKEN = "OLD"\n')
        assert params_store._load() == {}
