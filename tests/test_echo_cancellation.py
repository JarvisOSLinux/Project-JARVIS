"""Tests for acoustic echo cancellation (Project-JARVIS#143).

WebRtcAEC is exercised for real (aec-audio-processing + numpy are already
installed here) with synthetic echo signals -- not mocked -- since that's
the only way to actually prove cancellation happens rather than merely that
methods were called. VoskSTT/VoskActivation/PiperTTS wiring is exercised
against real instances driven by injected fake vosk/sounddevice/piper
modules, matching this codebase's established pattern (tests/test_noise_gate.py,
tests/test_concurrent_goals_barge_in.py).
"""

import sys
import types
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pytest

from jarvis.voice.aec import create_echo_canceller
from jarvis.voice.aec.webrtc_aec import WebRtcAEC
from jarvis.voice.base import EchoCanceller


def _tone(freq: float, duration_s: float, sample_rate: int, amplitude: float = 0.5):
    n = int(sample_rate * duration_s)
    t = np.arange(n) / sample_rate
    return (amplitude * 32767 * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def _simulate_echo(far: np.ndarray, delay_samples: int, attenuation: float = 0.6):
    echo = np.zeros(len(far), dtype=np.float64)
    echo[delay_samples:] = attenuation * far[:-delay_samples].astype(np.float64)
    return echo.astype(np.int16)


def _energy(samples: np.ndarray) -> float:
    return float(np.sum(samples.astype(np.float64) ** 2))


@pytest.mark.unit
class TestCreateEchoCanceller:
    def test_webrtc_provider_returns_webrtc_aec(self):
        aec = create_echo_canceller(provider="webrtc")
        assert isinstance(aec, WebRtcAEC)
        assert isinstance(aec, EchoCanceller)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown AEC provider"):
            create_echo_canceller(provider="nonexistent")


@pytest.mark.unit
class TestWebRtcAecRealCancellation:
    """Real end-to-end proof: synthetic echo goes in, attenuated signal
    comes out. No mocking of the DSP itself -- this is the actual
    webrtc-audio-processing engine (via aec-audio-processing)."""

    def test_lockstep_10ms_frames_cancel_echo(self):
        aec = WebRtcAEC(
            sample_rate=16000, reference_sample_rate=16000, stream_delay_ms=10
        )
        sr = 16000
        far = _tone(440, 1.0, sr)
        mic = _simulate_echo(far, delay_samples=160)

        frame_len = 160  # 10ms @ 16kHz
        out = bytearray()
        for i in range(len(mic) // frame_len):
            far_frame = far[i * frame_len : (i + 1) * frame_len].tobytes()
            mic_frame = mic[i * frame_len : (i + 1) * frame_len].tobytes()
            aec.feed_reference(far_frame)
            out.extend(aec.process(mic_frame))

        out_arr = np.frombuffer(bytes(out), dtype=np.int16)
        attenuation_db = 10 * np.log10(_energy(mic) / max(_energy(out_arr), 1e-9))
        assert attenuation_db > 20, f"only {attenuation_db:.1f}dB attenuation"

    def test_realistic_mismatched_chunk_sizes_still_cancel_echo(self):
        """Mirrors real deployment: VoskSTT delivers fixed 250ms (4000-sample)
        mic callbacks; Piper's TTS reference arrives as independently-sized,
        differently-rated chunks. An earlier "buffer and drain each side in
        isolation" design measured only ~6dB attenuation here (vs ~30dB+ in
        lockstep) because WebRTC's echo-path tracking needs forward/reverse
        calls interleaved roughly 1:1 -- this proves the fix holds under the
        conditions that actually break it."""
        aec = WebRtcAEC(
            sample_rate=16000, reference_sample_rate=22050, stream_delay_ms=10
        )
        sr, ref_sr = 16000, 22050
        far16 = _tone(440, 1.0, sr)
        far_ref = _tone(440, 1.0, ref_sr)
        mic = _simulate_echo(far16, delay_samples=160)

        mic_chunk, ref_chunk = 4000, 2205  # deliberately misaligned, cross-rate
        mic_bytes, ref_bytes = mic.tobytes(), far_ref.tobytes()

        events = []
        t_ms = 0.0
        for i in range(0, len(mic_bytes), mic_chunk * 2):
            events.append((t_ms, "mic", mic_bytes[i : i + mic_chunk * 2]))
            t_ms += (mic_chunk / sr) * 1000
        t_ms = 0.0
        for i in range(0, len(ref_bytes), ref_chunk * 2):
            events.append((t_ms, "ref", ref_bytes[i : i + ref_chunk * 2]))
            t_ms += (ref_chunk / ref_sr) * 1000
        events.sort(key=lambda e: (e[0], e[1] != "ref"))

        out = bytearray()
        for _, kind, chunk in events:
            if kind == "ref":
                aec.feed_reference(chunk)
            else:
                out.extend(aec.process(chunk))

        out_arr = np.frombuffer(bytes(out), dtype=np.int16)
        assert len(out_arr) == len(mic)
        attenuation_db = 10 * np.log10(_energy(mic) / max(_energy(out_arr), 1e-9))
        assert attenuation_db > 20, f"only {attenuation_db:.1f}dB attenuation"

    def test_no_reference_signal_still_passes_mic_through(self):
        """TTS isn't playing -- AEC must not withhold mic audio waiting for a
        reverse stream that may never come."""
        aec = WebRtcAEC(sample_rate=16000, reference_sample_rate=16000)
        mic = np.random.randint(-3000, 3000, 4000, dtype=np.int16).tobytes()
        out = aec.process(mic)
        assert len(out) == len(mic)

    def test_partial_frames_buffer_across_calls_without_crashing(self):
        aec = WebRtcAEC(sample_rate=16000, reference_sample_rate=16000)
        total = 0
        for _ in range(50):
            total += len(aec.process(b"\x01\x00" * 37))  # 37 samples, unaligned
        assert total > 0
        assert total % 320 == 0  # always whole 160-sample (320-byte) frames out

    def test_reset_clears_state_without_raising(self):
        aec = WebRtcAEC(sample_rate=16000, reference_sample_rate=16000)
        aec.feed_reference(_tone(440, 0.1, 16000).tobytes())
        aec.process(_tone(200, 0.1, 16000).tobytes())
        aec.reset()  # must not raise
        assert aec.process(_tone(200, 0.05, 16000).tobytes()) is not None


def _fake_vosk_module():
    fake = types.ModuleType("vosk")
    fake.Model = Mock(return_value=Mock())
    fake.KaldiRecognizer = Mock()
    return fake


def _fake_sounddevice_module():
    fake = types.ModuleType("sounddevice")

    class _Stream:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    fake.InputStream = _Stream
    fake.query_devices = lambda: [
        {"name": "fake-mic", "max_input_channels": 1, "max_output_channels": 1}
    ]
    fake.default = types.SimpleNamespace(device=[0, 0])
    return fake


@pytest.fixture
def fake_audio_modules():
    fake_vosk = _fake_vosk_module()
    fake_sd = _fake_sounddevice_module()
    with patch.dict(
        sys.modules, {"vosk": fake_vosk, "sounddevice": fake_sd, "sd": fake_sd}
    ):
        yield fake_vosk, fake_sd


class _StubEchoCanceller(EchoCanceller):
    """Records every mic frame it's asked to process and returns a fixed,
    recognisably-different payload -- proves the wiring actually routes
    through the AEC rather than merely constructing one."""

    def __init__(self):
        self.processed = []
        self.reference_fed = []

    def process(self, mic_frame: bytes) -> bytes:
        self.processed.append(mic_frame)
        return b"CANCELLED" + mic_frame

    def feed_reference(self, reference_frame: bytes) -> None:
        self.reference_fed.append(reference_frame)

    def reset(self) -> None:
        pass


class _RaisingEchoCanceller(EchoCanceller):
    def process(self, mic_frame: bytes) -> bytes:
        raise RuntimeError("boom")

    def feed_reference(self, reference_frame: bytes) -> None:
        raise RuntimeError("boom")

    def reset(self) -> None:
        pass


@pytest.mark.unit
class TestVoskSttEchoCancellerWiring:
    def test_process_loop_applies_echo_canceller_before_recognizer(
        self, fake_audio_modules
    ):
        from jarvis.voice.stt.vosk_stt import VoskSTT

        aec = _StubEchoCanceller()
        stt = VoskSTT(noise_gate_threshold=0, echo_canceller=aec)
        recognizer = Mock()
        recognizer.AcceptWaveform.return_value = False
        recognizer.PartialResult.return_value = "{}"
        stt._recognizer = recognizer
        stt._running.set()

        raw = b"\x10\x02" * 100
        stt._audio_buffer.put(raw)

        _run_loop_until_drained(stt._process_loop, stt._audio_buffer, stt._running)

        assert aec.processed == [raw]
        # the recognizer must see the AEC's *output*, not the raw mic bytes
        recognizer.AcceptWaveform.assert_called_once_with(b"CANCELLED" + raw)

    def test_echo_canceller_failure_falls_back_to_raw_audio(self, fake_audio_modules):
        from jarvis.voice.stt.vosk_stt import VoskSTT

        stt = VoskSTT(noise_gate_threshold=0, echo_canceller=_RaisingEchoCanceller())
        recognizer = Mock()
        recognizer.AcceptWaveform.return_value = False
        recognizer.PartialResult.return_value = "{}"
        stt._recognizer = recognizer
        stt._running.set()

        raw = b"\x10\x02" * 100
        stt._audio_buffer.put(raw)

        _run_loop_until_drained(stt._process_loop, stt._audio_buffer, stt._running)

        recognizer.AcceptWaveform.assert_called_once_with(raw)

    def test_no_echo_canceller_behaves_exactly_as_before(self, fake_audio_modules):
        from jarvis.voice.stt.vosk_stt import VoskSTT

        stt = VoskSTT(noise_gate_threshold=0)
        recognizer = Mock()
        recognizer.AcceptWaveform.return_value = False
        recognizer.PartialResult.return_value = "{}"
        stt._recognizer = recognizer
        stt._running.set()

        raw = b"\x10\x02" * 100
        stt._audio_buffer.put(raw)

        _run_loop_until_drained(stt._process_loop, stt._audio_buffer, stt._running)

        recognizer.AcceptWaveform.assert_called_once_with(raw)


@pytest.mark.unit
class TestVoskActivationEchoCancellerWiring:
    def test_listen_loop_applies_echo_canceller_before_recognizer(
        self, fake_audio_modules
    ):
        from jarvis.voice.activation.vosk_activation import VoskActivation

        aec = _StubEchoCanceller()
        activation = VoskActivation(noise_gate_threshold=0, echo_canceller=aec)
        recognizer = Mock()
        recognizer.AcceptWaveform.return_value = False
        activation._recognizer = recognizer
        activation._running.set()

        raw = b"\x10\x02" * 100
        activation._audio_buffer.put(raw)

        _run_loop_until_drained(
            activation._listen_loop, activation._audio_buffer, activation._running
        )

        assert aec.processed == [raw]
        recognizer.AcceptWaveform.assert_called_once_with(b"CANCELLED" + raw)

    def test_echo_canceller_failure_falls_back_to_raw_audio(self, fake_audio_modules):
        from jarvis.voice.activation.vosk_activation import VoskActivation

        activation = VoskActivation(
            noise_gate_threshold=0, echo_canceller=_RaisingEchoCanceller()
        )
        recognizer = Mock()
        recognizer.AcceptWaveform.return_value = False
        activation._recognizer = recognizer
        activation._running.set()

        raw = b"\x10\x02" * 100
        activation._audio_buffer.put(raw)

        _run_loop_until_drained(
            activation._listen_loop, activation._audio_buffer, activation._running
        )

        recognizer.AcceptWaveform.assert_called_once_with(raw)


@pytest.mark.unit
class TestPiperTtsFeedsReferenceSignal:
    def _make_piper(self, echo_canceller=None):
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

            tts = PiperTTS(
                model_path="fake.onnx",
                config_path="fake.json",
                echo_canceller=echo_canceller,
            )
        return tts, voice, stream

    @staticmethod
    def _chunk(data=b"\x00\x00" * 100):
        chunk = Mock()
        chunk.audio_int16_bytes = data
        return chunk

    def test_sample_rate_exposed_for_component_factory_wiring(self):
        tts, _, _ = self._make_piper()
        assert tts.sample_rate == 22050

    def test_each_synthesized_chunk_is_fed_as_reference(self):
        aec = _StubEchoCanceller()
        tts, voice, stream = self._make_piper(echo_canceller=aec)
        chunks = [self._chunk(b"\x01\x00" * 10), self._chunk(b"\x02\x00" * 10)]
        voice.synthesize.return_value = chunks

        tts.say("hello world")

        assert aec.reference_fed == [c.audio_int16_bytes for c in chunks]
        assert stream.write.call_count == 2

    def test_reference_feed_failure_does_not_interrupt_playback(self):
        tts, voice, stream = self._make_piper(echo_canceller=_RaisingEchoCanceller())
        voice.synthesize.return_value = [self._chunk(), self._chunk()]

        tts.say("must keep playing")

        assert stream.write.call_count == 2

    def test_no_echo_canceller_behaves_exactly_as_before(self):
        tts, voice, stream = self._make_piper(echo_canceller=None)
        voice.synthesize.return_value = [self._chunk()]

        tts.say("normal playback")

        assert stream.write.call_count == 1


@pytest.mark.unit
class TestComponentFactoryEchoCanceller:
    """Patches ``component_factory``'s own bound ``Config`` name (not a
    freshly re-imported ``jarvis.config.Config``) -- ``test_config_direct.py``
    does an ``importlib.reload(jarvis.config)`` elsewhere in the suite that
    mints a second, distinct ``Config`` class object, so a fresh import here
    can silently patch the wrong one depending on test run order."""

    def test_disabled_by_config_returns_none(self):
        from jarvis.core import component_factory as cf

        with patch.object(cf.Config, "AEC_ENABLED", False):
            assert cf.ComponentFactory.create_echo_canceller_optional(Mock()) is None

    def test_no_tts_returns_none_even_when_enabled(self):
        from jarvis.core import component_factory as cf

        with patch.object(cf.Config, "AEC_ENABLED", True):
            assert cf.ComponentFactory.create_echo_canceller_optional(None) is None

    def test_enabled_with_tts_builds_webrtc_aec_matching_tts_sample_rate(self):
        from jarvis.core import component_factory as cf

        fake_tts = Mock()
        fake_tts.sample_rate = 22050

        with (
            patch.object(cf.Config, "AEC_ENABLED", True),
            patch.object(cf.Config, "AEC_STREAM_DELAY_MS", 42),
        ):
            aec = cf.ComponentFactory.create_echo_canceller_optional(fake_tts)

        assert isinstance(aec, WebRtcAEC)
        assert aec.sample_rate == 16000
        assert aec.reference_sample_rate == 22050
        assert aec.stream_delay_ms == 42

    def test_missing_native_dependency_is_non_fatal(self):
        """WebRtcAEC raises AudioUnavailableError when aec-audio-processing
        isn't installed -- the factory must swallow it and return None
        rather than take the whole daemon down."""
        from jarvis.core import component_factory as cf
        from jarvis.voice.audio import AudioUnavailableError

        with (
            patch.object(cf.Config, "AEC_ENABLED", True),
            patch(
                "jarvis.voice.aec.create_echo_canceller",
                side_effect=AudioUnavailableError("aec-audio-processing not installed"),
            ),
        ):
            assert cf.ComponentFactory.create_echo_canceller_optional(Mock()) is None


def _run_loop_until_drained(loop_fn, buffer, running_flag, timeout=2.0):
    """Run the real `loop_fn` (e.g. _process_loop/_listen_loop) in a
    background thread against the pre-populated `buffer`, then stop it once
    drained -- exercises the actual production loop, not a reimplementation."""
    import threading
    import time

    thread = threading.Thread(target=loop_fn, daemon=True)
    thread.start()

    deadline = time.monotonic() + timeout
    while not buffer.empty() and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.05)  # let the last dequeued item finish processing

    running_flag.clear()
    thread.join(timeout=2.0)
    assert not thread.is_alive(), "loop thread did not stop in time"
