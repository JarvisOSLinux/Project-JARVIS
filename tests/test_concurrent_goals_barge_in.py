"""Tests for concurrent goals + TTS barge-in (Project-JARVIS#142):
interruptible PiperTTS playback, OutputManager.stop_speaking/is_speaking,
the barge-in path in run_voice_activation, and the concurrent-goals meta
on the PROCESSING broadcast.
"""

import asyncio
import sys
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from jarvis.core.output_manager import OutputManager
from jarvis.core.voice_state import VoiceState
from jarvis.runtime import io as runtime_io
from jarvis.runtime import root_handlers
from jarvis.runtime import voice_activation_thread as vat


@pytest.mark.unit
class TestOutputManagerBargeIn:
    def test_is_speaking_true_only_during_playback(self):
        tts = Mock()
        om = OutputManager(tts=tts)
        observed = []
        tts.say.side_effect = lambda text: observed.append(om.is_speaking())

        from jarvis.config import Config

        with patch.object(Config, "OUTPUT_MODE", "voice"):
            om.handle_response({"output": "hello"})

        assert observed == [True]
        assert om.is_speaking() is False

    def test_stop_speaking_delegates_to_tts_stop_only_while_speaking(self):
        tts = Mock()
        om = OutputManager(tts=tts)

        om.stop_speaking()  # not speaking -> must not touch tts.stop
        tts.stop.assert_not_called()

        om._speaking.set()
        om.stop_speaking()
        tts.stop.assert_called_once()

    def test_stop_speaking_survives_tts_without_stop_method(self):
        class LegacyTTS:
            def say(self, text):
                pass

        om = OutputManager(tts=LegacyTTS())
        om._speaking.set()
        om.stop_speaking()  # must not raise

    def test_speaking_flag_cleared_even_when_tts_raises(self):
        tts = Mock()
        tts.say.side_effect = RuntimeError("boom")
        om = OutputManager(tts=tts, suppress_stdout=True)

        from jarvis.config import Config

        with patch.object(Config, "OUTPUT_MODE", "voice"):
            om.handle_response({"output": "hello"})

        assert om.is_speaking() is False


@pytest.mark.unit
class TestPiperTtsInterrupt:
    def _make_piper(self):
        """Real PiperTTS instance with faked sounddevice/piper modules."""
        fake_sd = types.ModuleType("sounddevice")
        stream = MagicMock()
        stream.__enter__ = MagicMock(return_value=stream)
        stream.__exit__ = MagicMock(return_value=False)
        fake_sd.RawOutputStream = MagicMock(return_value=stream)
        fake_sd.query_devices = lambda: [
            {"name": "spk", "max_input_channels": 0, "max_output_channels": 2}
        ]
        fake_sd.default = types.SimpleNamespace(device=[0, 0])

        fake_piper_voice = types.ModuleType("piper.voice")
        fake_piper = types.ModuleType("piper")

        voice = Mock()
        voice.config.sample_rate = 22050
        fake_piper_voice.PiperVoice = Mock(load=Mock(return_value=voice))
        fake_piper.voice = fake_piper_voice

        with patch.dict(
            sys.modules,
            {
                "sounddevice": fake_sd,
                "piper": fake_piper,
                "piper.voice": fake_piper_voice,
            },
        ):
            from jarvis.voice.tts.piper_tts import PiperTTS

            tts = PiperTTS(model_path="fake.onnx", config_path="fake.json")
        return tts, voice, stream

    @staticmethod
    def _chunk(data=b"\x00\x00" * 100):
        chunk = Mock()
        chunk.audio_int16_bytes = data
        return chunk

    def test_plays_all_chunks_when_not_stopped(self):
        tts, voice, stream = self._make_piper()
        voice.synthesize.return_value = [self._chunk(), self._chunk(), self._chunk()]

        tts.say("hello world")

        assert stream.write.call_count == 3
        stream.abort.assert_not_called()

    def test_stop_mid_playback_aborts_remaining_chunks(self):
        tts, voice, stream = self._make_piper()

        def chunks():
            yield self._chunk()
            tts.stop()  # barge-in arrives after the first chunk plays
            yield self._chunk()
            yield self._chunk()

        voice.synthesize.return_value = chunks()

        tts.say("a long reply that gets interrupted")

        assert stream.write.call_count == 1
        stream.abort.assert_called_once()

    def test_stop_flag_resets_between_utterances(self):
        tts, voice, stream = self._make_piper()
        tts.stop()  # stale stop from a previous barge-in
        voice.synthesize.return_value = [self._chunk()]

        tts.say("next reply must play normally")

        assert stream.write.call_count == 1
        stream.abort.assert_not_called()


def _drain_scheduled_broadcasts(events_mock, action):
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


def _make_wake_app(output_manager):
    stt = Mock()
    stt.is_running.return_value = False
    activation = Mock()

    def fake_start_listening():
        vm._wake_word_detected = True
        return True

    activation.start_listening.side_effect = fake_start_listening
    vm = SimpleNamespace(stt=stt, activation=activation, _wake_word_detected=False)
    events = Mock()
    events.call_soon_threadsafe = Mock()
    app = SimpleNamespace(
        voice_manager=vm,
        events=events,
        _running=True,
        _gui_clients=set(),
        output_manager=output_manager,
    )
    return app, events


@pytest.mark.unit
class TestBargeInOnWakeWord:
    def _run_one_wake(self, output_manager):
        app, events = _make_wake_app(output_manager)

        def stop_after_one_pass(_seconds):
            app._running = False

        def action():
            with (
                patch.object(vat.time, "sleep", side_effect=stop_after_one_pass),
                patch.object(vat, "play_chime"),
            ):
                vat.run_voice_activation(app, Mock())

        return _drain_scheduled_broadcasts(events, action)

    def test_wake_during_speaking_stops_tts_and_flags_barge_in(self):
        om = Mock()
        om.is_speaking.return_value = True

        recorded = self._run_one_wake(om)

        om.stop_speaking.assert_called_once()
        woken = [r for r in recorded if r[0] == "state" and r[1] == VoiceState.WOKEN]
        assert woken == [("state", VoiceState.WOKEN, {"barge_in": True})]

    def test_wake_while_idle_does_not_stop_or_flag(self):
        om = Mock()
        om.is_speaking.return_value = False

        recorded = self._run_one_wake(om)

        om.stop_speaking.assert_not_called()
        woken = [r for r in recorded if r[0] == "state" and r[1] == VoiceState.WOKEN]
        assert woken == [("state", VoiceState.WOKEN, None)]


@pytest.mark.unit
class TestConcurrentGoalsMeta:
    @pytest.mark.asyncio
    async def test_processing_meta_when_goals_already_active(self):
        goals = Mock()
        goals.get_active_goals.return_value = [Mock(), Mock()]
        app = SimpleNamespace(llm=None, output_manager=Mock(), goals=goals)

        with patch.object(root_handlers, "set_gui_state", new=AsyncMock()) as st:
            await root_handlers.on_user_input(app, Mock(), "also do B")

        st.assert_awaited_once_with(app, VoiceState.PROCESSING, {"concurrent_goals": 3})

    @pytest.mark.asyncio
    async def test_no_meta_when_nothing_active(self):
        goals = Mock()
        goals.get_active_goals.return_value = []
        app = SimpleNamespace(llm=None, output_manager=Mock(), goals=goals)

        with patch.object(root_handlers, "set_gui_state", new=AsyncMock()) as st:
            await root_handlers.on_user_input(app, Mock(), "do A")

        st.assert_awaited_once_with(app, VoiceState.PROCESSING)


@pytest.mark.unit
class TestStandaloneManagerListeningRestart:
    def test_listening_restarts_before_on_command_runs(self):
        from jarvis.voice.manager import VoiceManager

        call_order = []
        stt = Mock()
        stt.iter_results.return_value = iter([("do the thing", True)])
        activation = Mock()
        activation.start_listening.side_effect = (
            lambda: call_order.append("start_listening") or True
        )

        def on_command(text):
            call_order.append(f"on_command:{text}")
            return {"output": "done."}

        vm = VoiceManager(on_command=on_command, stt=stt, activation=activation)
        vm._process_voice_command()

        assert call_order == ["start_listening", "on_command:do the thing"]

    def test_follow_up_uses_bounded_read_not_iter_results(self):
        from jarvis.voice.manager import VoiceManager

        stt = Mock()
        stt.iter_results.return_value = iter([("what time is it", True)])
        stt.read.side_effect = lambda timeout=None: None  # pure silence
        activation = Mock()
        activation.start_listening.return_value = True

        responses = [{"output": "Do you mean local time?"}]
        commands = []

        def on_command(text):
            commands.append(text)
            return responses.pop(0) if responses else {"output": "ok."}

        # Fake clock advancing 6s per call: deadline computed at ~6, first
        # loop check at ~12 is already past it. Patching time.time on the
        # shared stdlib module would break logging's own time.time() calls,
        # so it must keep returning values indefinitely.
        tick = {"now": 0.0}

        def fake_time():
            tick["now"] += 6.0
            return tick["now"]

        vm = VoiceManager(on_command=on_command, stt=stt, activation=activation)
        with patch.object(
            sys.modules["jarvis.voice.manager"].time, "time", side_effect=fake_time
        ):
            vm._process_voice_command()

        # Only the original command ran; the silent follow-up window timed
        # out via read() polling instead of hanging forever on iter_results.
        assert commands == ["what time is it"]
        assert stt.read.called
