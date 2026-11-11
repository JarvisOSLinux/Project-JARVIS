"""Tests for the GUI socket's settings + provider CRUD handlers
(jarvis/runtime/io.py), added for jarvisos-app#12's settings panel.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.config import Config
from jarvis.core import settings
from jarvis.runtime import io as runtime_io


def _make_app():
    return SimpleNamespace(_gui_clients={Mock()})


@pytest.mark.unit
class TestGetSettings:
    @pytest.mark.asyncio
    async def test_replies_with_every_setting_and_its_value(self):
        app = _make_app()
        writer = Mock()
        with (
            patch.object(Config, "CONFIRMATION_MODE", "ask_all"),
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
        ):
            await runtime_io._process_gui_message(
                app, Mock(), {"type": "get_settings"}, writer
            )
        (sent_to, reply), _ = gw.await_args
        assert sent_to is writer and reply["type"] == "settings"
        assert [e["key"] for e in reply["settings"]] == [
            s.key for s in settings.SETTINGS
        ]
        (mode,) = [e for e in reply["settings"] if e["key"] == "CONFIRMATION_MODE"]
        assert mode["value"] == "ask_all"
        assert [o["value"] for o in mode["options"]] == [
            "smart",
            "ask_all",
            "allow_all",
        ]


async def _set(key, value):
    app = _make_app()
    writer = Mock()
    with (
        patch("jarvis.cli._update_env_setting") as write,
        patch.object(runtime_io, "broadcast_to_gui_clients", new=AsyncMock()) as bc,
        patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
    ):
        await runtime_io._process_gui_message(
            app, Mock(), {"type": "set_setting", "key": key, "value": value}, writer
        )
    return app, writer, write, bc, gw


@pytest.mark.unit
class TestSetSetting:
    @pytest.mark.asyncio
    async def test_a_valid_value_is_saved_applied_and_told_to_everyone(self):
        with patch.object(Config, "CONFIRMATION_MODE", "smart"):
            app, _, write, bc, gw = await _set("CONFIRMATION_MODE", "ask_all")
            assert Config.CONFIRMATION_MODE == "ask_all"
        write.assert_called_once_with("CONFIRMATION_MODE", "ask_all")
        bc.assert_awaited_once_with(
            app,
            {"type": "config_updated", "key": "CONFIRMATION_MODE", "value": "ask_all"},
        )
        gw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_values_keep_their_type(self):
        with patch.object(Config, "VOICE_SPEAK_COMMENTS", True):
            app, _, write, bc, _ = await _set("VOICE_SPEAK_COMMENTS", False)
            assert Config.VOICE_SPEAK_COMMENTS is False
        write.assert_called_once_with("VOICE_SPEAK_COMMENTS", "false")
        bc.assert_awaited_once_with(
            app,
            {"type": "config_updated", "key": "VOICE_SPEAK_COMMENTS", "value": False},
        )

    @pytest.mark.asyncio
    async def test_a_bad_value_is_told_to_the_asker_only(self):
        with patch.object(Config, "CONFIRMATION_MODE", "smart"):
            _, writer, write, bc, gw = await _set("CONFIRMATION_MODE", "yolo")
            assert Config.CONFIRMATION_MODE == "smart"
        write.assert_not_called()
        bc.assert_not_awaited()
        gw.assert_awaited_once_with(
            writer,
            {
                "type": "config_error",
                "key": "CONFIRMATION_MODE",
                "message": "Confirmation mode: one of smart, ask_all, allow_all",
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", ["JARVIS_SUDO_ENABLED", "LLM_URL", "", "LOG_FILE"])
    async def test_a_key_outside_the_list_is_refused(self, key):
        _, writer, write, bc, gw = await _set(key, "true")
        write.assert_not_called()
        bc.assert_not_awaited()
        (_, reply), _ = gw.await_args
        assert reply["type"] == "config_error"
        assert reply["message"] == f"{key} is not a setting JARVIS can change"

    @pytest.mark.asyncio
    async def test_an_empty_wake_sound_restores_the_bundled_one(self):
        with patch.object(Config, "WAKE_CHIME_PATH", "/somewhere/else.wav"):
            app, _, write, bc, _ = await _set("WAKE_CHIME_PATH", "")
            assert Config.WAKE_CHIME_PATH == Config.DEFAULT_WAKE_CHIME_PATH
        write.assert_called_once_with("WAKE_CHIME_PATH", Config.DEFAULT_WAKE_CHIME_PATH)
        bc.assert_awaited_once_with(
            app,
            {
                "type": "config_updated",
                "key": "WAKE_CHIME_PATH",
                "value": Config.DEFAULT_WAKE_CHIME_PATH,
            },
        )


@pytest.mark.unit
class TestProviderCrudHandlers:
    """Exercises the real jarvis.core.providers file-based CRUD (a temp
    providers.json), not mocks -- these handlers are thin wrappers, so the
    thing worth actually proving is the real read-modify-write round-trips
    and that failures surface as provider_error."""

    def test_add_provider_success_returns_refreshed_list(self, tmp_path):
        providers_file = str(tmp_path / "providers.json")
        with patch.object(Config, "PROVIDERS_FILE", providers_file):
            result = runtime_io._handle_add_provider(
                {"ptype": "llamacpp", "model": "qwen3:8b", "name": "my-local"}
            )
        assert result["type"] == "provider_list"
        assert [p["name"] for p in result["providers"]] == ["my-local"]

    def test_add_provider_failure_returns_provider_error(self, tmp_path):
        providers_file = str(tmp_path / "providers.json")
        with patch.object(Config, "PROVIDERS_FILE", providers_file):
            result = runtime_io._handle_add_provider({"ptype": "bogus", "model": "x"})
        assert result["type"] == "provider_error"
        assert "bogus" in result["message"]

    def test_edit_provider_requires_name_and_fields(self):
        assert runtime_io._handle_edit_provider({})["type"] == "provider_error"
        assert (
            runtime_io._handle_edit_provider({"name": "x"})["type"] == "provider_error"
        )

    def test_edit_provider_success_updates_and_returns_list(self, tmp_path):
        providers_file = str(tmp_path / "providers.json")
        with patch.object(Config, "PROVIDERS_FILE", providers_file):
            runtime_io._handle_add_provider(
                {"ptype": "llamacpp", "model": "qwen3:8b", "name": "my-local"}
            )
            result = runtime_io._handle_edit_provider(
                {"name": "my-local", "fields": {"model": "qwen3:14b"}}
            )
        assert result["type"] == "provider_list"
        assert result["providers"][0]["model"] == "qwen3:14b"

    def test_edit_provider_not_found_returns_provider_error(self, tmp_path):
        providers_file = str(tmp_path / "providers.json")
        with patch.object(Config, "PROVIDERS_FILE", providers_file):
            result = runtime_io._handle_edit_provider(
                {"name": "nope", "fields": {"model": "x"}}
            )
        assert result["type"] == "provider_error"

    def test_remove_provider_requires_name(self):
        assert runtime_io._handle_remove_provider({})["type"] == "provider_error"

    def test_remove_provider_success_returns_refreshed_list(self, tmp_path):
        providers_file = str(tmp_path / "providers.json")
        with patch.object(Config, "PROVIDERS_FILE", providers_file):
            runtime_io._handle_add_provider(
                {"ptype": "llamacpp", "model": "qwen3:8b", "name": "my-local"}
            )
            result = runtime_io._handle_remove_provider({"name": "my-local"})
        assert result["type"] == "provider_list"
        assert result["providers"] == []

    def test_remove_provider_not_found_returns_provider_error(self, tmp_path):
        providers_file = str(tmp_path / "providers.json")
        with patch.object(Config, "PROVIDERS_FILE", providers_file):
            result = runtime_io._handle_remove_provider({"name": "nope"})
        assert result["type"] == "provider_error"


@pytest.mark.unit
class TestReplyOrBroadcastProviders:
    @pytest.mark.asyncio
    async def test_error_goes_to_requester_only(self):
        app = _make_app()
        writer = Mock()
        with (
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gui_write,
            patch.object(
                runtime_io, "broadcast_to_gui_clients", new=AsyncMock()
            ) as broadcast,
        ):
            await runtime_io._reply_or_broadcast_providers(
                app, writer, {"type": "provider_error", "message": "boom"}
            )
        gui_write.assert_awaited_once_with(
            writer, {"type": "provider_error", "message": "boom"}
        )
        broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_broadcasts_to_all_clients(self):
        app = _make_app()
        writer = Mock()
        response = {"type": "provider_list", "providers": []}
        with (
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gui_write,
            patch.object(
                runtime_io, "broadcast_to_gui_clients", new=AsyncMock()
            ) as broadcast,
        ):
            await runtime_io._reply_or_broadcast_providers(app, writer, response)
        broadcast.assert_awaited_once_with(app, response)
        gui_write.assert_not_awaited()


@pytest.mark.unit
class TestProcessGuiMessageRouting:
    """Confirms _process_gui_message actually wires the new message types
    to their handlers end-to-end through the real dispatch function."""

    @pytest.mark.asyncio
    async def test_list_providers_replies_with_real_list(self, tmp_path):
        providers_file = str(tmp_path / "providers.json")
        app = _make_app()
        writer = Mock()
        with (
            patch.object(Config, "PROVIDERS_FILE", providers_file),
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
        ):
            runtime_io._handle_add_provider(
                {"ptype": "llamacpp", "model": "qwen3:8b", "name": "solo"}
            )
            await runtime_io._process_gui_message(
                app, Mock(), {"type": "list_providers"}, writer
            )
        gw.assert_awaited_once()
        payload = gw.await_args.args[1]
        assert payload["type"] == "provider_list"
        assert [p["name"] for p in payload["providers"]] == ["solo"]

    @pytest.mark.asyncio
    async def test_add_provider_broadcasts_on_success(self, tmp_path):
        providers_file = str(tmp_path / "providers.json")
        app = _make_app()
        writer = Mock()
        with (
            patch.object(Config, "PROVIDERS_FILE", providers_file),
            patch.object(runtime_io, "broadcast_to_gui_clients", new=AsyncMock()) as bc,
        ):
            await runtime_io._process_gui_message(
                app,
                Mock(),
                {"type": "add_provider", "ptype": "llamacpp", "model": "qwen3:8b"},
                writer,
            )
        bc.assert_awaited_once()
        assert bc.await_args.args[1]["type"] == "provider_list"


@pytest.mark.unit
class TestChangesFromAPhone:
    """A change made on a phone shows on the laptop (Yakup, 2026-11-10)."""

    @staticmethod
    def _app(label):
        writer = Mock()
        app = SimpleNamespace(_gui_clients={writer: label})
        return app, writer

    def test_a_phone_change_is_a_finished_block(self):
        app, writer = self._app("web:yakup@github")
        with patch.object(runtime_io.action_events, "start", return_value="r:1") as st:
            with patch.object(runtime_io.action_events, "end") as end:
                runtime_io.note_change_from_phone(
                    app, writer, "Output mode set to voice"
                )
        st.assert_called_once_with(
            app, "remote", "Output mode set to voice (from yakup@github's phone)"
        )
        end.assert_called_once_with(app, "r:1", "ok")

    def test_the_app_and_the_tui_make_no_block(self):
        for label in ("jarvis-app", "", "tui"):
            app, writer = self._app(label)
            with patch.object(runtime_io.action_events, "start") as st:
                runtime_io.note_change_from_phone(app, writer, "x")
            st.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_setting_changed_from_a_phone_names_itself(self):
        app, writer = self._app("web:yakup@github")
        with (
            patch.object(Config, "OUTPUT_MODE", "text"),
            patch("jarvis.cli._update_env_setting"),
            patch.object(runtime_io, "broadcast_to_gui_clients", new=AsyncMock()),
            patch.object(runtime_io, "note_change_from_phone") as noted,
        ):
            await runtime_io._process_gui_message(
                app,
                Mock(),
                {"type": "set_setting", "key": "OUTPUT_MODE", "value": "voice"},
                writer,
            )
        (_, _, what), _ = noted.call_args
        assert what == "Replies set to Shown and spoken"
