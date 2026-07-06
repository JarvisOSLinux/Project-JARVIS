"""Tests for the wake-word earcon (Project-JARVIS#139): path validation,
best-effort playback, the unconditional wake_word_detected broadcast, and
the validated set_wake_chime_path GUI-socket message.
"""

import asyncio
import os
import stat
import sys
import types
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from jarvis.config import Config
from jarvis.core.voice_state import VoiceState
from jarvis.runtime import io as runtime_io
from jarvis.runtime import voice_activation_thread as vat
from jarvis.voice import chime


def _write_wav(path, n_frames=100, sample_rate=16000, channels=1, sample_width=2):
    with wave.open(path, "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(sample_width)
        f.setframerate(sample_rate)
        f.writeframes(b"\x00" * n_frames * sample_width * channels)


@pytest.mark.unit
class TestValidateChimePath:
    def test_bundled_default_is_valid(self):
        assert chime.validate_chime_path(Config.WAKE_CHIME_PATH) is None

    def test_empty_path_is_invalid(self):
        assert chime.validate_chime_path("") == "no path configured"

    def test_missing_file_is_invalid(self):
        error = chime.validate_chime_path("/definitely/not/a/real/path.wav")
        assert "not a file" in error

    def test_unreadable_file_is_invalid(self, tmp_path):
        path = tmp_path / "chime.wav"
        _write_wav(str(path))
        os.chmod(path, 0)
        try:
            if os.access(path, os.R_OK):
                pytest.skip(
                    "running as a user that bypasses permission bits (e.g. root)"
                )
            assert "not readable" in chime.validate_chime_path(str(path))
        finally:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    def test_corrupt_file_is_invalid(self, tmp_path):
        path = tmp_path / "chime.wav"
        path.write_bytes(b"not actually a wav file")
        error = chime.validate_chime_path(str(path))
        assert "not a valid WAV file" in error

    def test_valid_wav_passes(self, tmp_path):
        path = tmp_path / "chime.wav"
        _write_wav(str(path))
        assert chime.validate_chime_path(str(path)) is None


def _install_fake_sounddevice():
    """Returns (fake_module, stream_mock) so tests can assert on playback calls."""
    fake_sd = types.ModuleType("sounddevice")
    stream = MagicMock()
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    fake_sd.RawOutputStream = MagicMock(return_value=stream)
    return fake_sd, stream


@pytest.mark.unit
class TestPlayChime:
    def test_invalid_path_never_imports_sounddevice(self):
        with patch.dict(sys.modules, {"sounddevice": None}):
            chime.play_chime("/nonexistent.wav")  # must not raise

    def test_missing_sounddevice_is_silent_noop(self, tmp_path):
        path = tmp_path / "chime.wav"
        _write_wav(str(path))
        with patch.dict(sys.modules, {"sounddevice": None}):
            chime.play_chime(str(path))  # must not raise

    def test_plays_with_correct_stream_params(self, tmp_path):
        path = tmp_path / "chime.wav"
        _write_wav(str(path), sample_rate=22050, channels=1, sample_width=2)
        fake_sd, stream = _install_fake_sounddevice()

        with patch.dict(sys.modules, {"sounddevice": fake_sd}):
            chime.play_chime(str(path))

        fake_sd.RawOutputStream.assert_called_once()
        kwargs = fake_sd.RawOutputStream.call_args.kwargs
        assert kwargs["samplerate"] == 22050
        assert kwargs["channels"] == 1
        assert kwargs["dtype"] == "int16"
        stream.write.assert_called_once()

    def test_playback_error_is_caught_not_raised(self, tmp_path):
        path = tmp_path / "chime.wav"
        _write_wav(str(path))
        fake_sd, _ = _install_fake_sounddevice()
        fake_sd.RawOutputStream.side_effect = RuntimeError("no device")

        with patch.dict(sys.modules, {"sounddevice": fake_sd}):
            chime.play_chime(str(path))  # must not raise

    def test_unsupported_sample_width_is_silent_noop(self, tmp_path):
        path = tmp_path / "chime.wav"
        _write_wav(str(path), sample_width=3)  # 24-bit, unsupported
        fake_sd, _ = _install_fake_sounddevice()

        with patch.dict(sys.modules, {"sounddevice": fake_sd}):
            chime.play_chime(str(path))

        fake_sd.RawOutputStream.assert_not_called()


def _make_wake_app():
    stt = Mock()
    stt.is_running.return_value = False  # end capture immediately
    activation = Mock()

    def fake_start_listening():
        vm._wake_word_detected = True
        return True

    activation.start_listening.side_effect = fake_start_listening
    vm = SimpleNamespace(stt=stt, activation=activation, _wake_word_detected=False)
    events = Mock()
    events.call_soon_threadsafe = Mock()
    app = SimpleNamespace(voice_manager=vm, events=events, _running=True)
    return app, events


def _drain_scheduled_broadcasts(events_mock, action):
    """Run `action` with io.set_gui_state and io.broadcast_to_gui_clients faked
    out, then replay every scheduled callback and return what each recorded."""
    recorded = []

    async def fake_set_gui_state(app, state, meta=None):
        recorded.append(("state", state, meta))

    async def fake_broadcast(app, message):
        recorded.append(("broadcast", message))

    with (
        patch.object(runtime_io, "set_gui_state", new=fake_set_gui_state),
        patch.object(runtime_io, "broadcast_to_gui_clients", new=fake_broadcast),
    ):
        action()
        loop = asyncio.new_event_loop()
        try:
            for call in events_mock.call_soon_threadsafe.call_args_list:
                cb = call.args[0]
                loop.run_until_complete(_run_cb(cb))
        finally:
            loop.close()
    return recorded


async def _run_cb(cb):
    task = cb()
    if task is not None:
        await task


@pytest.mark.unit
class TestWakeWordBroadcastOrder:
    def test_wake_word_detected_and_woken_precede_chime_and_capture(self):
        app, events = _make_wake_app()

        def stop_after_one_pass(_seconds):
            app._running = False

        def action():
            with (
                patch.object(vat.time, "sleep", side_effect=stop_after_one_pass),
                patch.object(vat, "play_chime") as mock_chime,
            ):
                vat.run_voice_activation(app, Mock())
                mock_chime.assert_called_once_with(Config.WAKE_CHIME_PATH)

        events_recorded = _drain_scheduled_broadcasts(events, action)

        assert events_recorded[0] == ("broadcast", {"type": "wake_word_detected"})
        assert events_recorded[1] == ("state", VoiceState.WOKEN, None)
        # CAPTURING then IDLE follow from process_voice_command_inject.
        assert events_recorded[2] == ("state", VoiceState.CAPTURING, None)
        assert (
            events_recorded[3][0] == "state"
            and events_recorded[3][1] == VoiceState.IDLE
        )

    def test_chime_plays_before_capture_opens(self):
        # play_chime is called with the real (un-mocked) function; assert it
        # runs (and thus blocks) strictly between the WOKEN broadcast being
        # scheduled and stt.start() being called for capture.
        app, events = _make_wake_app()
        call_order = []
        app.voice_manager.stt.start.side_effect = lambda: call_order.append("stt.start")

        def stop_after_one_pass(_seconds):
            app._running = False

        def fake_play_chime(path):
            call_order.append("play_chime")

        def action():
            with (
                patch.object(vat.time, "sleep", side_effect=stop_after_one_pass),
                patch.object(vat, "play_chime", side_effect=fake_play_chime),
            ):
                vat.run_voice_activation(app, Mock())

        _drain_scheduled_broadcasts(events, action)
        assert call_order == ["play_chime", "stt.start"]


@pytest.mark.unit
class TestSetWakeChimePathHandler:
    @pytest.mark.asyncio
    async def test_valid_path_persists_and_broadcasts(self, tmp_path):
        path = tmp_path / "chime.wav"
        _write_wav(str(path))
        app = SimpleNamespace(_gui_clients={Mock()})
        writer = Mock()

        with (
            patch("jarvis.cli._update_env_setting") as mock_update,
            patch.object(runtime_io, "broadcast_to_gui_clients", new=AsyncMock()) as bc,
        ):
            await runtime_io._handle_set_wake_chime_path(
                app, {"path": str(path)}, writer
            )

        mock_update.assert_called_once_with("WAKE_CHIME_PATH", str(path))
        bc.assert_awaited_once_with(
            app,
            {"type": "config_updated", "key": "WAKE_CHIME_PATH", "value": str(path)},
        )

    @pytest.mark.asyncio
    async def test_invalid_path_replies_error_to_requester_only(self):
        app = SimpleNamespace(_gui_clients={Mock()})
        writer = Mock()

        with (
            patch("jarvis.cli._update_env_setting") as mock_update,
            patch.object(runtime_io, "broadcast_to_gui_clients", new=AsyncMock()) as bc,
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
        ):
            await runtime_io._handle_set_wake_chime_path(
                app, {"path": "/nonexistent.wav"}, writer
            )

        mock_update.assert_not_called()
        bc.assert_not_awaited()
        gw.assert_awaited_once_with(
            writer,
            {
                "type": "config_error",
                "key": "WAKE_CHIME_PATH",
                "message": "not a file: /nonexistent.wav",
            },
        )
