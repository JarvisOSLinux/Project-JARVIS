"""Tests for the faster-whisper STT provider and STT provider selection
(Project-JARVIS#138).

`faster_whisper`, `vosk`, `sounddevice` and `numpy` aren't installed in this
environment -- and faster-whisper must NOT become a test dependency -- so the
providers are exercised against injected fake modules (real provider
instances, not mocks of the classes themselves). No network, no model
download.
"""

import array
import importlib
import math
import os
import sys
import types
from unittest.mock import Mock, patch

import pytest


def _tone(amplitude: float, n: int = 800) -> bytes:
    """Synthetic int16 PCM sine wave at the given fractional amplitude."""
    return array.array(
        "h", [int(amplitude * 32767 * math.sin(i * 0.3)) for i in range(n)]
    ).tobytes()


QUIET = _tone(0.001)
LOUD = _tone(0.5)


class _FakeSegment:
    def __init__(self, text):
        self.text = text


def _fake_faster_whisper_module(segments=(" hello ", "jarvis ")):
    """Stand-in for the faster_whisper package.

    ``module.created`` records every WhisperModel construction so tests can
    assert on model size and download root without touching the network.
    """
    fake = types.ModuleType("faster_whisper")
    fake.created = []

    class _WhisperModel:
        def __init__(self, model_size_or_path, **kwargs):
            self.model_size_or_path = model_size_or_path
            self.kwargs = kwargs
            self.transcribe_calls = []
            fake.created.append(self)

        def transcribe(self, audio, **kwargs):
            self.transcribe_calls.append((audio, kwargs))
            return (
                iter([_FakeSegment(text) for text in segments]),
                types.SimpleNamespace(language="en"),
            )

    fake.WhisperModel = _WhisperModel
    return fake


class _FakeAudio(list):
    """Stands in for an ndarray across the two ops the provider performs."""

    def astype(self, dtype):
        return self

    def __truediv__(self, other):
        return self


def _fake_numpy_module():
    fake = types.ModuleType("numpy")
    fake.int16 = "int16"
    fake.float32 = "float32"
    fake.frombuffer = lambda data, dtype=None: _FakeAudio([len(data)])
    return fake


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
def audio_modules():
    """Audio stack present, faster_whisper absent (this container's reality)."""
    fake_sd = _fake_sounddevice_module()
    modules = {
        "vosk": _fake_vosk_module(),
        "sounddevice": fake_sd,
        "sd": fake_sd,
        "numpy": _fake_numpy_module(),
    }
    with patch.dict(sys.modules, modules):
        sys.modules.pop("faster_whisper", None)
        yield modules


@pytest.fixture
def whisper_modules(audio_modules):
    """Audio stack plus an importable fake faster_whisper."""
    fake_whisper = _fake_faster_whisper_module()
    with patch.dict(sys.modules, {"faster_whisper": fake_whisper}):
        yield fake_whisper


def _factory_kwargs(**overrides):
    """The kwarg union component_factory hands create_stt()."""
    kwargs = {
        "model_path": "/models/vosk/vosk-model-small-en-us-0.15",
        "model_size": "small",
        "model_dir": "/models/whisper",
        "sample_rate": 16000,
        "chunk_size": 4000,
        "silence_timeout": 1.0,
        "noise_gate_threshold": 150,
        "echo_canceller": None,
    }
    kwargs.update(overrides)
    return kwargs


@pytest.mark.unit
class TestProviderSelection:
    def test_default_provider_is_vosk(self, audio_modules):
        from jarvis.voice.stt import create_stt
        from jarvis.voice.stt.vosk_stt import VoskSTT

        stt = create_stt(**_factory_kwargs())

        assert isinstance(stt, VoskSTT)

    def test_config_default_stt_provider_is_vosk(self):
        with patch.dict(os.environ, {}):
            os.environ.pop("STT_PROVIDER", None)
            import jarvis.config

            importlib.reload(jarvis.config)
            assert jarvis.config.Config.STT_PROVIDER == "vosk"
        importlib.reload(jarvis.config)

    def test_faster_whisper_provider_selects_whisper_stt(
        self, whisper_modules, tmp_path
    ):
        from jarvis.voice.stt import create_stt
        from jarvis.voice.stt.whisper_stt import WhisperSTT

        # create_stt calls ensure_model(), which *creates* model_dir — so it
        # must be a writable temp path. A fixed path like /models/whisper
        # passes only where the suite happens to run as root, and degrades to
        # Vosk everywhere else (which is correct product behavior, and exactly
        # what this test would then mis-report as a selection bug).
        model_dir = tmp_path / "whisper"

        stt = create_stt(
            provider="faster-whisper", **_factory_kwargs(model_dir=str(model_dir))
        )

        assert isinstance(stt, WhisperSTT)
        assert stt.model_size == "small"
        assert stt.model_dir == str(model_dir)

    def test_unknown_provider_still_raises(self, audio_modules):
        from jarvis.voice.stt import create_stt

        with pytest.raises(ValueError) as exc:
            create_stt(provider="whisper-cpp", **_factory_kwargs())

        assert "faster-whisper" in str(exc.value)
        assert "vosk" in str(exc.value)


@pytest.mark.unit
class TestLazyImportUnavailability:
    def test_is_available_is_false_and_does_not_raise(self, audio_modules, tmp_path):
        from jarvis.voice.stt.whisper_stt import WhisperSTT

        stt = WhisperSTT(model_size="small", model_dir=str(tmp_path / "whisper"))

        assert stt.is_available() is False

    def test_ensure_model_is_false_without_the_package(self, audio_modules, tmp_path):
        from jarvis.voice.stt.whisper_stt import WhisperSTT

        stt = WhisperSTT(model_size="small", model_dir=str(tmp_path / "whisper"))

        assert stt.ensure_model() is False

    def test_missing_package_falls_back_to_vosk_and_logs(self, audio_modules, caplog):
        from jarvis.voice.stt import create_stt
        from jarvis.voice.stt.vosk_stt import VoskSTT

        with caplog.at_level("ERROR"):
            stt = create_stt(provider="faster-whisper", **_factory_kwargs())

        assert isinstance(stt, VoskSTT)
        assert stt.model_path == "/models/vosk/vosk-model-small-en-us-0.15"
        assert "falling back to Vosk" in caplog.text
        assert "voice-whisper" in caplog.text

    def test_unloadable_model_falls_back_to_vosk(self, whisper_modules, caplog):
        from jarvis.voice.stt import create_stt
        from jarvis.voice.stt.vosk_stt import VoskSTT

        def _explode(*args, **kwargs):
            raise RuntimeError("model weights unavailable")

        whisper_modules.WhisperModel = _explode

        with caplog.at_level("ERROR"):
            stt = create_stt(provider="faster-whisper", **_factory_kwargs())

        assert isinstance(stt, VoskSTT)
        assert "falling back to Vosk" in caplog.text


@pytest.mark.unit
class TestModelConfiguration:
    def test_whisper_model_size_default(self):
        with patch.dict(os.environ, {}):
            os.environ.pop("WHISPER_MODEL_SIZE", None)
            import jarvis.config

            importlib.reload(jarvis.config)
            assert jarvis.config.Config.WHISPER_MODEL_SIZE == "small"
        importlib.reload(jarvis.config)

    def test_whisper_model_size_env_override(self):
        with patch.dict(os.environ, {"WHISPER_MODEL_SIZE": "medium"}):
            import jarvis.config

            importlib.reload(jarvis.config)
            assert jarvis.config.Config.WHISPER_MODEL_SIZE == "medium"
        importlib.reload(jarvis.config)

    def test_model_dir_defaults_under_models_dir(self, tmp_path):
        with patch.dict(os.environ, {"MODELS_DIR": str(tmp_path)}):
            os.environ.pop("WHISPER_MODEL_DIR", None)
            import jarvis.config

            importlib.reload(jarvis.config)
            Config = jarvis.config.Config

            assert Config.WHISPER_MODEL_DIR == os.path.join(str(tmp_path), "whisper")
            assert Config.WHISPER_MODEL_DIR.startswith(Config.MODELS_DIR)
        importlib.reload(jarvis.config)

    def test_ensure_model_downloads_into_model_dir(self, whisper_modules, tmp_path):
        from jarvis.voice.stt.whisper_stt import WhisperSTT

        model_dir = tmp_path / "models" / "whisper"
        stt = WhisperSTT(model_size="medium", model_dir=str(model_dir))

        assert stt.ensure_model() is True
        assert model_dir.is_dir()

        created = whisper_modules.created[-1]
        assert created.model_size_or_path == "medium"
        assert created.kwargs["download_root"] == str(model_dir)

    def test_ensure_model_loads_only_once(self, whisper_modules, tmp_path):
        from jarvis.voice.stt.whisper_stt import WhisperSTT

        stt = WhisperSTT(model_size="small", model_dir=str(tmp_path))

        assert stt.ensure_model() is True
        assert stt.ensure_model() is True
        assert len(whisper_modules.created) == 1


@pytest.mark.unit
class TestTranscriptionPath:
    def test_segments_are_joined_into_a_vosk_shaped_final_result(
        self, whisper_modules, tmp_path
    ):
        from jarvis.voice.stt.whisper_stt import WhisperSTT

        stt = WhisperSTT(
            model_size="small",
            model_dir=str(tmp_path),
            silence_timeout=0.0,
            noise_gate_threshold=150,
        )
        assert stt.ensure_model() is True

        updates = []
        stt.on_update(lambda text, is_final: updates.append((text, is_final)))
        stt._running.set()
        for chunk in (LOUD, LOUD, QUIET):
            stt._audio_buffer.put(chunk)

        _run_loop_until_drained(stt._process_loop, stt._audio_buffer, stt._running)

        assert stt._result_q.get_nowait() == ("hello jarvis", True)
        assert updates == [("hello jarvis", True)]

    def test_quiet_only_audio_never_reaches_the_model(self, whisper_modules, tmp_path):
        from jarvis.voice.stt.whisper_stt import WhisperSTT

        stt = WhisperSTT(
            model_size="small",
            model_dir=str(tmp_path),
            silence_timeout=0.0,
            noise_gate_threshold=150,
        )
        assert stt.ensure_model() is True
        stt._running.set()
        for chunk in (QUIET, QUIET):
            stt._audio_buffer.put(chunk)

        _run_loop_until_drained(stt._process_loop, stt._audio_buffer, stt._running)

        assert whisper_modules.created[-1].transcribe_calls == []
        assert stt._result_q.empty()

    def test_read_returns_the_same_tuple_shape_as_vosk(self, whisper_modules, tmp_path):
        from jarvis.voice.stt.whisper_stt import WhisperSTT

        stt = WhisperSTT(model_size="small", model_dir=str(tmp_path))
        stt._emit("hello jarvis", is_final=True)

        assert stt.read(timeout=0.1) == ("hello jarvis", True)
        assert stt.read(timeout=0.01) is None

    def test_a_failing_transcript_callback_is_logged(
        self, whisper_modules, tmp_path, caplog
    ):
        from jarvis.voice.stt.whisper_stt import WhisperSTT

        stt = WhisperSTT(model_size="small", model_dir=str(tmp_path))
        stt.on_update(Mock(side_effect=RuntimeError("panel gone")))
        with caplog.at_level("WARNING"):
            stt._emit("hello jarvis", is_final=True)
        assert stt.read(timeout=0.1) == ("hello jarvis", True)
        assert "panel gone" in caplog.text


@pytest.mark.unit
class TestWakeWordStaysOnVosk:
    def test_activation_is_vosk_even_when_stt_is_faster_whisper(self, audio_modules):
        import jarvis.voice.activation as activation_pkg
        import jarvis.voice.stt as stt_pkg
        from jarvis.core import component_factory as cf

        # The Config object component_factory itself reads -- a config reload
        # in an earlier test rebinds jarvis.config.Config without rebinding
        # the reference this module already holds.
        Config = cf.Config
        recorded = {}

        def _record_activation(**kwargs):
            recorded.update(kwargs)
            return Mock()

        with (
            patch.object(Config, "STT_PROVIDER", "faster-whisper"),
            patch.object(cf, "check_audio_input_available", lambda: True),
            patch.object(activation_pkg, "create_activation", _record_activation),
            patch.object(stt_pkg, "create_stt", lambda **kwargs: Mock()),
        ):
            vm = cf.ComponentFactory.create_voice_manager_optional(on_command=Mock())

        assert vm is not None
        assert recorded["provider"] == Config.ACTIVATION_PROVIDER == "vosk"
        assert recorded["model_path"] == Config.VOSK_MODEL_PATH

    def test_create_activation_builds_a_vosk_listener(self, whisper_modules):
        from jarvis.voice.activation import create_activation
        from jarvis.voice.activation.vosk_activation import VoskActivation

        activation = create_activation(
            provider="vosk",
            wake_words=["jarvis"],
            model_path="/models/vosk/vosk-model-small-en-us-0.15",
        )

        assert isinstance(activation, VoskActivation)
        assert activation.model_path == "/models/vosk/vosk-model-small-en-us-0.15"


def _run_loop_until_drained(loop_fn, buffer, running_flag, timeout=2.0):
    """Run the real `loop_fn` in a background thread against the pre-populated
    `buffer`, then stop it once drained -- exercises the actual production
    loop, not a reimplementation."""
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
