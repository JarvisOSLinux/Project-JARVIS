"""Tests for the formal voice/response state machine (Project-JARVIS#141):
VoiceState, the structured `set_gui_state` broadcast, the PROCESSING funnel
in `on_user_input`, and the SPEAKING/IDLE bracket around TTS playback.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.config import Config
from jarvis.core.output_manager import OutputManager
from jarvis.core.voice_state import VoiceState
from jarvis.runtime import io as runtime_io
from jarvis.runtime import root_handlers


@pytest.mark.unit
class TestVoiceStateEnum:
    def test_members_serialize_to_their_plain_string_value(self):
        assert json.dumps({"state": VoiceState.PROCESSING}) == '{"state": "processing"}'

    def test_expected_members_exist(self):
        assert {s.value for s in VoiceState} == {
            "idle",
            "woken",
            "capturing",
            "processing",
            "speaking",
        }


def _make_gui_app():
    return SimpleNamespace(_gui_state="idle", _gui_clients={Mock()})


@pytest.mark.unit
class TestSetGuiState:
    @pytest.mark.asyncio
    async def test_broadcasts_plain_value_with_no_meta_key(self):
        app = _make_gui_app()
        with patch.object(
            runtime_io, "broadcast_to_gui_clients", new=AsyncMock()
        ) as broadcast:
            await runtime_io.set_gui_state(app, VoiceState.PROCESSING)

        assert app._gui_state == "processing"
        broadcast.assert_awaited_once_with(
            app, {"type": "state", "state": "processing"}
        )

    @pytest.mark.asyncio
    async def test_includes_meta_when_provided(self):
        app = _make_gui_app()
        meta = {"reason": "discard", "detail": "no speech detected"}
        with patch.object(
            runtime_io, "broadcast_to_gui_clients", new=AsyncMock()
        ) as broadcast:
            await runtime_io.set_gui_state(app, VoiceState.IDLE, meta)

        broadcast.assert_awaited_once_with(
            app, {"type": "state", "state": "idle", "meta": meta}
        )

    @pytest.mark.asyncio
    async def test_accepts_plain_string_for_manual_listen_toggle(self):
        app = _make_gui_app()
        with patch.object(
            runtime_io, "broadcast_to_gui_clients", new=AsyncMock()
        ) as broadcast:
            await runtime_io.set_gui_state(app, "listening")

        assert app._gui_state == "listening"
        broadcast.assert_awaited_once_with(app, {"type": "state", "state": "listening"})


@pytest.mark.unit
class TestOnUserInputBroadcastsProcessing:
    @pytest.mark.asyncio
    async def test_broadcasts_processing_even_when_llm_unconfigured(self):
        app = SimpleNamespace(llm=None, output_manager=Mock())
        with patch.object(
            root_handlers, "set_gui_state", new=AsyncMock()
        ) as mock_state:
            await root_handlers.on_user_input(app, Mock(), "hello")

        mock_state.assert_awaited_once_with(app, VoiceState.PROCESSING)
        app.output_manager.display.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcasts_processing_before_slash_command_handling(self):
        app = SimpleNamespace(llm=Mock())
        with (
            patch.object(root_handlers, "set_gui_state", new=AsyncMock()) as mock_state,
            patch.object(
                root_handlers, "handle_slash_command", return_value=True
            ) as mock_slash,
        ):
            await root_handlers.on_user_input(app, Mock(), "/help")

        mock_state.assert_awaited_once_with(app, VoiceState.PROCESSING)
        mock_slash.assert_called_once_with(app, "/help")


@pytest.mark.unit
class TestOutputManagerSpeakingState:
    def test_speaking_then_idle_around_tts_call(self):
        tts = Mock()
        om = OutputManager(tts=tts)
        states = []
        om.set_state_callback(states.append)

        with patch.object(Config, "OUTPUT_MODE", "voice"):
            om.handle_response({"output": "hello"})

        assert states == [VoiceState.SPEAKING, VoiceState.IDLE]
        tts.say.assert_called_once_with("hello")

    def test_idle_still_fires_when_tts_raises(self):
        tts = Mock()
        tts.say.side_effect = RuntimeError("boom")
        om = OutputManager(tts=tts, suppress_stdout=True)
        states = []
        om.set_state_callback(states.append)

        with patch.object(Config, "OUTPUT_MODE", "voice"):
            om.handle_response({"output": "hello"})  # must not raise

        assert states == [VoiceState.SPEAKING, VoiceState.IDLE]

    def test_no_state_calls_when_tts_unavailable(self):
        om = OutputManager(tts=None, suppress_stdout=True)
        states = []
        om.set_state_callback(states.append)

        with patch.object(Config, "OUTPUT_MODE", "voice"):
            om.handle_response({"output": "hello"})

        assert states == []

    def test_no_state_calls_in_text_mode(self):
        tts = Mock()
        om = OutputManager(tts=tts, suppress_stdout=True)
        states = []
        om.set_state_callback(states.append)

        with patch.object(Config, "OUTPUT_MODE", "text"):
            om.handle_response({"output": "hello"})

        assert states == []
        tts.say.assert_not_called()

    def test_state_callback_errors_are_swallowed(self):
        tts = Mock()
        om = OutputManager(tts=tts)
        om.set_state_callback(Mock(side_effect=RuntimeError("cb boom")))

        with patch.object(Config, "OUTPUT_MODE", "voice"):
            om.handle_response({"output": "hello"})  # must not raise

        tts.say.assert_called_once()

    def test_no_state_calls_when_no_callback_registered(self):
        tts = Mock()
        om = OutputManager(tts=tts)  # set_state_callback never called

        with patch.object(Config, "OUTPUT_MODE", "voice"):
            om.handle_response({"output": "hello"})  # must not raise

        tts.say.assert_called_once()
