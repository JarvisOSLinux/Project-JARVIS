"""Tests for the GUI socket's settings + provider CRUD handlers
(jarvis/runtime/io.py), added for jarvisos-app#12's settings panel.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.config import Config
from jarvis.runtime import io as runtime_io


def _make_app():
    return SimpleNamespace(_gui_clients={Mock()})


@pytest.mark.unit
class TestGetSettings:
    @pytest.mark.asyncio
    async def test_returns_whitelisted_settings_only(self):
        app = _make_app()
        writer = Mock()
        with (
            patch.object(Config, "CONFIRMATION_MODE", "smart"),
            patch.object(Config, "WAKE_CHIME_PATH", "/some/chime.wav"),
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
        ):
            await runtime_io._process_gui_message(
                app, Mock(), {"type": "get_settings"}, writer
            )
        gw.assert_awaited_once_with(
            writer,
            {
                "type": "settings",
                "confirmation_mode": "smart",
                "wake_chime_path": "/some/chime.wav",
            },
        )


@pytest.mark.unit
class TestSetConfirmationMode:
    @pytest.mark.asyncio
    async def test_valid_mode_persists_and_broadcasts(self):
        app = _make_app()
        writer = Mock()
        with (
            patch("jarvis.cli._update_env_setting") as mock_update,
            patch.object(runtime_io, "broadcast_to_gui_clients", new=AsyncMock()) as bc,
        ):
            await runtime_io._handle_set_confirmation_mode(
                app, {"mode": "ask_all"}, writer
            )
        mock_update.assert_called_once_with("CONFIRMATION_MODE", "ask_all")
        bc.assert_awaited_once_with(
            app,
            {"type": "config_updated", "key": "CONFIRMATION_MODE", "value": "ask_all"},
        )

    @pytest.mark.asyncio
    async def test_invalid_mode_replies_error_to_requester_only(self):
        app = _make_app()
        writer = Mock()
        with (
            patch("jarvis.cli._update_env_setting") as mock_update,
            patch.object(runtime_io, "broadcast_to_gui_clients", new=AsyncMock()) as bc,
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
        ):
            await runtime_io._handle_set_confirmation_mode(
                app, {"mode": "yolo"}, writer
            )
        mock_update.assert_not_called()
        bc.assert_not_awaited()
        gw.assert_awaited_once_with(
            writer,
            {
                "type": "config_error",
                "key": "CONFIRMATION_MODE",
                "message": "Invalid mode 'yolo'. Use one of: smart, ask_all, allow_all.",
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
                {"ptype": "ollama", "model": "qwen3:8b", "name": "my-ollama"}
            )
        assert result["type"] == "provider_list"
        assert [p["name"] for p in result["providers"]] == ["my-ollama"]

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
                {"ptype": "ollama", "model": "qwen3:8b", "name": "my-ollama"}
            )
            result = runtime_io._handle_edit_provider(
                {"name": "my-ollama", "fields": {"model": "qwen3:14b"}}
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
                {"ptype": "ollama", "model": "qwen3:8b", "name": "my-ollama"}
            )
            result = runtime_io._handle_remove_provider({"name": "my-ollama"})
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
                {"ptype": "ollama", "model": "qwen3:8b", "name": "solo"}
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
                {"type": "add_provider", "ptype": "ollama", "model": "qwen3:8b"},
                writer,
            )
        bc.assert_awaited_once()
        assert bc.await_args.args[1]["type"] == "provider_list"
