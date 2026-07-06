"""Vosk-based speech-to-text provider."""

import json
import threading
from datetime import datetime, timedelta
from queue import Empty, Queue
from typing import Any, Callable, Generator, Optional, Tuple

from ...core.logger import get_logger
from ..audio import (
    AudioUnavailableError,
    check_audio_input_available,
    passes_noise_gate,
)
from ..base import STTProvider

logger = get_logger(__name__)


class VoskSTT(STTProvider):
    """Real-time, offline speech-to-text using Vosk.

    Usage::

        stt = VoskSTT(model_path="vosk-model-small-en-us-0.15")
        stt.start()
        try:
            for text, is_final in stt.iter_results():
                print(("FINAL: " if is_final else "PARTIAL: ") + text)
        finally:
            stt.stop()
    """

    def __init__(
        self,
        model_path: str = "vosk-model-small-en-us-0.15",
        sample_rate: int = 16000,
        chunk_size: int = 4000,
        silence_timeout: float = 1.0,
        noise_gate_threshold: int = 150,
        device_index: Optional[int] = None,
    ):
        try:
            import sounddevice as sd

            self.sd = sd
        except ImportError:
            raise AudioUnavailableError(
                "sounddevice package not installed. Install with: pip install sounddevice"
            )

        try:
            import vosk

            self.vosk = vosk
        except ImportError:
            raise AudioUnavailableError(
                "vosk package not installed. Install with: pip install vosk"
            )

        if not check_audio_input_available():
            raise AudioUnavailableError(
                "No audio input devices available. Cannot initialize STT."
            )

        self.model_path = model_path
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.silence_timeout = silence_timeout
        self.noise_gate_threshold = noise_gate_threshold
        self.device_index = device_index

        self._result_q: Queue[Tuple[str, bool]] = Queue()
        self._model: Optional[Any] = None
        self._recognizer: Optional[Any] = None
        self._stream: Optional[Any] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._last_speech_time: Optional[datetime] = None
        self._last_emitted_text = ""
        self._current_phrase = ""
        self._on_update: Optional[Callable[[str, bool], None]] = None
        self._audio_buffer: Queue = Queue()

    # -- STTProvider interface ------------------------------------------------

    def start(self) -> None:
        if self._running.is_set():
            return

        try:
            logger.info(f"Loading Vosk model from: {self.model_path}")
            self._model = self.vosk.Model(self.model_path)
            self._recognizer = self.vosk.KaldiRecognizer(self._model, self.sample_rate)
            logger.info("Vosk model loaded successfully")

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

        self._recognizer = None
        self._model = None
        self._drain_queue(self._result_q)
        self._drain_queue(self._audio_buffer)
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

    # -- Vosk-specific extras -------------------------------------------------

    @staticmethod
    def list_audio_devices() -> None:
        """List available audio input devices."""
        try:
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice package not available")
            return
        logger.info("Available audio input devices:")
        devices = sd.query_devices()
        for i, device in enumerate(devices):
            if device["max_input_channels"] > 0:
                logger.info(
                    f"[{i}] {device['name']} (channels: {device['max_input_channels']})"
                )

    def on_update(self, cb: Callable[[str, bool], None]) -> None:
        """Register a callback called as ``cb(text, is_final)``."""
        self._on_update = cb

    def read(self, timeout: Optional[float] = None) -> Optional[Tuple[str, bool]]:
        """Pop one result. Returns None on timeout."""
        try:
            return self._result_q.get(timeout=timeout)
        except Empty:
            return None

    def get_stats(self) -> dict:
        return {
            "is_running": self.is_running(),
            "model_path": self.model_path,
            "sample_rate": self.sample_rate,
            "chunk_size": self.chunk_size,
            "current_phrase": self._current_phrase,
            "last_emitted_text": self._last_emitted_text,
        }

    # -- internals ------------------------------------------------------------

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
                    continue

                if not passes_noise_gate(data, self.noise_gate_threshold):
                    continue

                if self._recognizer.AcceptWaveform(data):
                    result = json.loads(self._recognizer.Result())
                    text = result.get("text", "").strip()
                    if text:
                        self._current_phrase = text
                        self._last_speech_time = datetime.utcnow()
                        self._emit(text, is_final=True)
                        self._last_emitted_text = text
                        logger.debug(f"FINAL: {text}")
                else:
                    partial = json.loads(self._recognizer.PartialResult())
                    partial_text = partial.get("partial", "").strip()
                    if partial_text and partial_text != self._last_emitted_text:
                        self._last_speech_time = datetime.utcnow()
                        self._emit(partial_text, is_final=False)
                        self._last_emitted_text = partial_text
                        logger.debug(f"PARTIAL: {partial_text}")

                if self._last_speech_time:
                    silence_duration = datetime.utcnow() - self._last_speech_time
                    if silence_duration > timedelta(seconds=self.silence_timeout):
                        if (
                            self._current_phrase
                            and self._current_phrase != self._last_emitted_text
                        ):
                            self._emit(self._current_phrase, is_final=True)
                            self._last_emitted_text = self._current_phrase
                            logger.debug(f"FINAL (silence): {self._current_phrase}")
                        self._current_phrase = ""
                        self._last_speech_time = None

            except Exception as e:
                if self._running.is_set():
                    logger.error(f"Error in processing loop: {e}")
                break

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
