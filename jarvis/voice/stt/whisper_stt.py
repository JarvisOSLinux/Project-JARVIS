"""faster-whisper speech-to-text provider (Project-JARVIS#138)."""

import threading
from datetime import datetime, timedelta
from queue import Empty, Queue
from typing import Any, Callable, Generator, List, Optional, Tuple

from ...core.logger import get_logger
from ...platform.ownership import make_dirs
from ..audio import (
    AudioUnavailableError,
    check_audio_input_available,
    passes_noise_gate,
)
from ..base import EchoCanceller, STTProvider

logger = get_logger(__name__)

INSTALL_HINT = (
    "faster-whisper package not installed. "
    "Install with: pip install 'project-jarvis[voice-whisper]'"
)


class WhisperSTT(STTProvider):
    """Offline speech-to-text using faster-whisper (Whisper via CTranslate2).

    Whisper transcribes a whole utterance at once instead of streaming word-by-
    word hypotheses, so this provider buffers gated audio and transcribes when
    the speaker pauses: it emits final results only, never partials. That is
    also why it covers the *utterance* phase alone -- wake-word detection stays
    on :class:`~jarvis.voice.activation.vosk_activation.VoskActivation`, which
    needs a continuously running, low-latency recognizer.

    Usage::

        stt = WhisperSTT(model_size="small", model_dir="/…/models/whisper")
        if stt.is_available() and stt.ensure_model():
            stt.start()
    """

    def __init__(
        self,
        model_size: str = "small",
        model_dir: Optional[str] = None,
        sample_rate: int = 16000,
        chunk_size: int = 4000,
        silence_timeout: float = 1.0,
        noise_gate_threshold: int = 150,
        device_index: Optional[int] = None,
        echo_canceller: Optional[EchoCanceller] = None,
        # "auto" picks CUDA when present; int8 keeps the CPU path fast and
        # inside the RAM budget Vosk users expect, and CTranslate2 falls back
        # on its own where int8 is unsupported.
        device: str = "auto",
        compute_type: str = "int8",
        language: Optional[str] = None,
        beam_size: int = 5,
    ):
        try:
            import sounddevice as sd

            self.sd = sd
        except ImportError:
            raise AudioUnavailableError(
                "sounddevice package not installed. Install with: pip install sounddevice"
            )

        if not check_audio_input_available():
            raise AudioUnavailableError(
                "No audio input devices available. Cannot initialize STT."
            )

        self.model_size = model_size
        self.model_dir = model_dir
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.silence_timeout = silence_timeout
        self.noise_gate_threshold = noise_gate_threshold
        self.device_index = device_index
        self.echo_canceller = echo_canceller
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size

        self._result_q: Queue[Tuple[str, bool]] = Queue()
        self._faster_whisper: Optional[Any] = None
        self._model: Optional[Any] = None
        self._stream: Optional[Any] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._last_speech_time: Optional[datetime] = None
        self._last_emitted_text = ""
        self._current_phrase = ""
        self._utterance: List[bytes] = []
        self._on_update: Optional[Callable[[str, bool], None]] = None
        self._audio_buffer: Queue = Queue()

    # -- availability ---------------------------------------------------------

    def is_available(self) -> bool:
        """Return True when the faster-whisper package can be imported.

        A capability probe -- callers ask precisely so they can fall back to
        Vosk -- so a missing package is "not available", not an exception.
        """
        try:
            self._ensure_package()
            return True
        except Exception as e:
            logger.debug(f"faster-whisper not available: {e}")
            return False

    def ensure_model(self) -> bool:
        """Load the model, downloading it into ``model_dir`` on first use.

        Returns False (never raises) so a missing or undownloadable model is a
        fallback decision for the caller rather than a dead voice input.
        """
        if self._model is not None:
            return True

        try:
            self._ensure_package()
        except ImportError as e:
            logger.debug(f"faster-whisper not available: {e}")
            return False

        try:
            if self.model_dir:
                make_dirs(self.model_dir)
            logger.info(
                f"Loading faster-whisper model '{self.model_size}' "
                f"(download root: {self.model_dir or 'default cache'})"
            )
            self._model = self._faster_whisper.WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root=self.model_dir,
            )
            logger.info("faster-whisper model loaded successfully")
            return True
        except Exception as e:
            logger.error(
                f"Failed to load faster-whisper model '{self.model_size}': {e}"
            )
            return False

    # -- STTProvider interface ------------------------------------------------

    def start(self) -> None:
        if self._running.is_set():
            return

        try:
            if not self.ensure_model():
                raise AudioUnavailableError(
                    f"faster-whisper model '{self.model_size}' is unavailable"
                )

            stream_params = {
                "samplerate": self.sample_rate,
                "channels": 1,
                "dtype": "int16",
                "blocksize": self.chunk_size,
                "callback": self._audio_callback,
            }
            if self.device_index is not None:
                stream_params["device"] = self.device_index

            self._stream = self.sd.InputStream(**stream_params)
            self._stream.start()
            logger.info("Audio stream initialized")

            self._running.set()
            self._worker_thread = threading.Thread(
                target=self._process_loop, daemon=True
            )
            self._worker_thread.start()
            logger.info("Speech-to-text processing started")

        except Exception as e:
            logger.error(f"Failed to start speech-to-text: {e}")
            self.stop()
            raise

    def stop(self) -> None:
        if not self._running.is_set():
            return

        self._running.clear()

        if self._worker_thread:
            self._worker_thread.join(timeout=2.0)
            self._worker_thread = None

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        # The model deliberately stays resident across start/stop cycles: a
        # wake word arrives every few seconds and reloading Whisper weights
        # each time would cost more than the transcription itself.
        self._drain_queue(self._result_q)
        self._drain_queue(self._audio_buffer)
        self._utterance = []
        self._last_speech_time = None
        self._last_emitted_text = ""
        self._current_phrase = ""
        logger.info("Speech-to-text stopped")

    def iter_results(self) -> Generator[Tuple[str, bool], None, None]:
        while self._running.is_set():
            try:
                yield self._result_q.get(timeout=0.1)
            except Empty:
                continue

    def is_running(self) -> bool:
        return self._running.is_set()

    def read(self, timeout: Optional[float] = None) -> Optional[Tuple[str, bool]]:
        """Pop one result. Returns None on timeout."""
        try:
            return self._result_q.get(timeout=timeout)
        except Empty:
            return None

    # -- Whisper-specific extras ----------------------------------------------

    def on_update(self, cb: Callable[[str, bool], None]) -> None:
        """Register a callback called as ``cb(text, is_final)``."""
        self._on_update = cb

    def get_stats(self) -> dict:
        return {
            "is_running": self.is_running(),
            "model_size": self.model_size,
            "model_dir": self.model_dir,
            "sample_rate": self.sample_rate,
            "chunk_size": self.chunk_size,
            "current_phrase": self._current_phrase,
            "last_emitted_text": self._last_emitted_text,
        }

    # -- internals ------------------------------------------------------------

    def _ensure_package(self) -> None:
        """Import faster_whisper on first use."""
        if self._faster_whisper is not None:
            return
        try:
            import faster_whisper as _faster_whisper

            self._faster_whisper = _faster_whisper
        except ImportError:
            raise ImportError(INSTALL_HINT)

    def _audio_callback(self, indata, frames, time, status):
        if status:
            logger.warning(f"Audio callback status: {status}")
        self._audio_buffer.put(indata.tobytes())

    def _drain_queue(self, q: Queue) -> None:
        try:
            while True:
                q.get_nowait()
        except Empty:
            pass

    def _process_loop(self) -> None:
        while self._running.is_set():
            try:
                try:
                    data = self._audio_buffer.get(timeout=0.1)
                except Empty:
                    self._flush_if_silent()
                    continue

                if self.echo_canceller is not None:
                    try:
                        data = self.echo_canceller.process(data)
                    except Exception as e:
                        logger.warning(
                            f"Echo cancellation failed, using raw audio: {e}"
                        )

                # The noise gate doubles as the endpointer here: a chunk loud
                # enough to keep is speech to transcribe, a quiet one is the
                # pause that closes the utterance.
                if passes_noise_gate(data, self.noise_gate_threshold):
                    self._utterance.append(data)
                    self._last_speech_time = datetime.utcnow()
                else:
                    self._flush_if_silent()

            except Exception as e:
                if self._running.is_set():
                    logger.error(f"Error in processing loop: {e}")
                break

    def _flush_if_silent(self) -> None:
        if not self._utterance or self._last_speech_time is None:
            return
        silence_duration = datetime.utcnow() - self._last_speech_time
        if silence_duration <= timedelta(seconds=self.silence_timeout):
            return

        pcm = b"".join(self._utterance)
        self._utterance = []
        self._last_speech_time = None

        text = self._transcribe(pcm)
        if text:
            self._current_phrase = text
            self._last_emitted_text = text
            self._emit(text, is_final=True)
            logger.debug(f"FINAL: {text}")

    def _transcribe(self, pcm: bytes) -> str:
        """Transcribe one buffered utterance into a single line of text."""
        try:
            import numpy as np
        except ImportError:
            logger.error(
                "numpy package not installed. "
                "Install with: pip install 'project-jarvis[voice-input]'"
            )
            return ""

        try:
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            segments, _info = self._model.transcribe(
                audio,
                language=self.language,
                beam_size=self.beam_size,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as e:
            logger.error(f"faster-whisper transcription failed: {e}")
            return ""

    def _emit(self, text: str, is_final: bool) -> None:
        try:
            self._result_q.put_nowait((text, is_final))
        except Exception:
            pass
        if self._on_update:
            try:
                self._on_update(text, is_final)
            except Exception:
                pass
