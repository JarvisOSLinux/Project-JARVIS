"""Tests for the audio noise gate and finals-only wake-word matching
(Project-JARVIS#140).

`vosk`/`sounddevice` aren't installed in this environment, so VoskSTT/
VoskActivation are exercised against injected fake modules (real instances,
not mocks of the classes themselves) -- this drives the actual
`_process_loop`/`_listen_loop` code paths being changed.
"""

import array
import json
import math
import sys
import types
from unittest.mock import Mock, patch

import pytest

from jarvis.voice.audio import passes_noise_gate


def _tone(amplitude: float, n: int = 800) -> bytes:
    """Synthetic int16 PCM sine wave at the given fractional amplitude."""
    return array.array(
        "h", [int(amplitude * 32767 * math.sin(i * 0.3)) for i in range(n)]
    ).tobytes()


QUIET = _tone(0.001)
LOUD = _tone(0.5)


@pytest.mark.unit
class TestPassesNoiseGate:
    def test_quiet_chunk_is_gated(self):
        assert passes_noise_gate(QUIET, 150) is False

    def test_loud_chunk_passes(self):
        assert passes_noise_gate(LOUD, 150) is True

    def test_empty_chunk_is_gated(self):
        assert passes_noise_gate(b"", 150) is False

    def test_odd_length_chunk_does_not_crash(self):
        assert passes_noise_gate(b"\x01", 150) is False


class _FakeRecognizer:
    """Scripted stand-in for vosk.KaldiRecognizer. `script` has one
    (accept, payload_dict) entry per expected AcceptWaveform call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def AcceptWaveform(self, data):
        self.calls.append(data)
        if not self.script:
            return False
        accept, payload = self.script.pop(0)
        self._payload = payload
        return accept

    def Result(self):
        return json.dumps(getattr(self, "_payload", {}))

    def PartialResult(self):
        return json.dumps(getattr(self, "_payload", {}))


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


@pytest.mark.unit
class TestVoskSttNoiseGate:
    def test_quiet_chunks_never_reach_accept_waveform(self, fake_audio_modules):
        from jarvis.voice.stt.vosk_stt import VoskSTT

        stt = VoskSTT(noise_gate_threshold=150)
        recognizer = _FakeRecognizer([(True, {"text": "hello jarvis"})])
        stt._recognizer = recognizer
        stt._running.set()

        for chunk in (QUIET, QUIET, LOUD):
            stt._audio_buffer.put(chunk)

        _run_loop_until_drained(stt._process_loop, stt._audio_buffer, stt._running)

        assert recognizer.calls == [LOUD]
        assert stt._result_q.get_nowait() == ("hello jarvis", True)

    def test_phrase_timeout_constructor_param_is_gone(self, fake_audio_modules):
        from jarvis.voice.stt.vosk_stt import VoskSTT

        with pytest.raises(TypeError):
            VoskSTT(phrase_timeout=3.0)


@pytest.mark.unit
class TestVoskActivationNoiseGateAndFinalsOnly:
    def test_quiet_chunks_never_reach_accept_waveform(self, fake_audio_modules):
        from jarvis.voice.activation.vosk_activation import VoskActivation

        activation = VoskActivation(noise_gate_threshold=150)
        recognizer = _FakeRecognizer([(True, {"text": "hey jarvis"})])
        activation._recognizer = recognizer
        activation._running.set()

        for chunk in (QUIET, QUIET, LOUD):
            activation._audio_buffer.put(chunk)

        _run_loop_until_drained(
            activation._listen_loop, activation._audio_buffer, activation._running
        )

        assert recognizer.calls == [LOUD]

    def test_wake_word_in_partial_result_does_not_trigger(self, fake_audio_modules):
        from jarvis.voice.activation.vosk_activation import VoskActivation

        on_wake = Mock()
        activation = VoskActivation(noise_gate_threshold=150, on_wake_word=on_wake)
        # accept=False -> Vosk has NOT finalized; "hey jarvis" only ever
        # appears as a partial hypothesis here.
        recognizer = _FakeRecognizer([(False, {"partial": "hey jarvis"})])
        activation._recognizer = recognizer
        activation._running.set()
        activation._audio_buffer.put(LOUD)

        _run_loop_until_drained(
            activation._listen_loop, activation._audio_buffer, activation._running
        )

        on_wake.assert_not_called()

    def test_wake_word_in_final_result_triggers(self, fake_audio_modules):
        from jarvis.voice.activation.vosk_activation import VoskActivation

        on_wake = Mock()
        activation = VoskActivation(noise_gate_threshold=150, on_wake_word=on_wake)
        recognizer = _FakeRecognizer([(True, {"text": "hey jarvis do a thing"})])
        activation._recognizer = recognizer
        activation._running.set()
        activation._audio_buffer.put(LOUD)

        _run_loop_until_drained(
            activation._listen_loop, activation._audio_buffer, activation._running
        )

        on_wake.assert_called_once()


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
