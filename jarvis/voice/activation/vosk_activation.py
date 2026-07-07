"""Vosk-based wake-word activation provider."""

import threading
import time
from queue import Empty, Queue
from typing import Callable, List, Optional

from ...core.logger import get_logger
from ..audio import passes_noise_gate
from ..base import ActivationProvider, EchoCanceller

logger = get_logger(__name__)


class VoskActivation(ActivationProvider):
    """Wake word detection using Vosk's real-time speech recognition."""

    def __init__(
        self,
        wake_words: Optional[List[str]] = None,
        model_path: str = "vosk-model-small-en-us-0.15",
        sample_rate: int = 16000,
        chunk_size: int = 4000,
        sensitivity: float = 0.8,
        noise_gate_threshold: int = 150,
        on_wake_word: Optional[Callable[[], None]] = None,
        echo_canceller: Optional[EchoCanceller] = None,
    ):
        self.wake_words = [w.lower() for w in (wake_words or ["jarvis", "hey jarvis"])]
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.sensitivity = sensitivity
        self.noise_gate_threshold = noise_gate_threshold
        self.on_wake_word = on_wake_word
        self.echo_canceller = echo_canceller

        try:
            import json as _json

            import sounddevice as sd
            import vosk

            self.vosk = vosk
            self.sounddevice = sd
            self.json = _json
        except ImportError as e:
            raise ImportError(
                f"Required dependencies not found: {e}. "
                "Please install: pip install vosk sounddevice"
            )

        self._model = None
        self._recognizer = None
        self._stream = None
        self._listening_thread = None
        self._running = threading.Event()
        self._activation_queue: Queue = Queue()
        self._detection_count = 0
        self._last_detection_time = 0.0
        self._audio_buffer: Queue = Queue()

    # -- ActivationProvider interface -----------------------------------------

    def start_listening(self) -> bool:
        if self._running.is_set():
            return True

        if not self._model:
            if not self._initialize():
                return False

        try:
            stream_params = {
                "samplerate": self.sample_rate,
                "channels": 1,
                "dtype": "int16",
                "blocksize": self.chunk_size,
                "callback": self._audio_callback,
            }
            self._stream = self.sounddevice.InputStream(**stream_params)
            self._stream.start()

            self._running.set()
            self._listening_thread = threading.Thread(
                target=self._listen_loop, daemon=True
            )
            self._listening_thread.start()

            logger.info("Voice activation listening started")
            return True
        except Exception as e:
            logger.error(f"Failed to start listening: {e}")
            self.stop_listening()
            return False

    def stop_listening(self) -> None:
        if not self._running.is_set():
            return

        self._running.clear()

        if self._listening_thread:
            self._listening_thread.join(timeout=2.0)
            self._listening_thread = None

        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        logger.info("Voice activation stopped")

    def is_listening(self) -> bool:
        return self._running.is_set()

    def cleanup(self) -> None:
        self.stop_listening()
        self._recognizer = None
        self._model = None
        while not self._activation_queue.empty():
            try:
                self._activation_queue.get_nowait()
            except Empty:
                break

    # -- Vosk-specific extras -------------------------------------------------

    def get_activation(self, timeout: Optional[float] = None) -> Optional[dict]:
        """Get the next activation event (non-blocking by default)."""
        try:
            return self._activation_queue.get(timeout=timeout)
        except Empty:
            return None

    def get_stats(self) -> dict:
        return {
            "detection_count": self._detection_count,
            "last_detection_time": self._last_detection_time,
            "is_listening": self.is_listening(),
            "wake_words": self.wake_words.copy(),
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    # -- internals ------------------------------------------------------------

    def _initialize(self) -> bool:
        try:
            logger.info(f"Loading Vosk model from: {self.model_path}")
            self._model = self.vosk.Model(self.model_path)
            self._recognizer = self.vosk.KaldiRecognizer(self._model, self.sample_rate)

            logger.info(f"Voice Activation initialized:")
            logger.info(f"   Wake words: {', '.join(self.wake_words)}")
            logger.info(f"   Sample rate: {self.sample_rate} Hz")
            logger.info(f"   Chunk size: {self.chunk_size} samples")
            logger.info(f"   Sensitivity: {self.sensitivity}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize voice activation: {e}")
            return False

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            logger.warning(f"Audio callback status: {status}")
        self._audio_buffer.put(indata.tobytes())

    def _listen_loop(self) -> None:
        try:
            while self._running.is_set():
                try:
                    data = self._audio_buffer.get(timeout=0.1)
                except Empty:
                    continue

                if self.echo_canceller is not None:
                    try:
                        data = self.echo_canceller.process(data)
                    except Exception as e:
                        logger.warning(
                            f"Echo cancellation failed, using raw audio: {e}"
                        )

                if not passes_noise_gate(data, self.noise_gate_threshold):
                    continue

                if self._recognizer.AcceptWaveform(data):
                    result = self.json.loads(self._recognizer.Result())
                    text = result.get("text", "").lower().strip()
                    if text:
                        self._check_for_wake_word(text)
                # Partial hypotheses are Vosk's least reliable output --
                # only a finalized result triggers a wake word (Project-JARVIS#140).
        except Exception as e:
            logger.error(f"Error in listening loop: {e}")
        finally:
            self._running.clear()

    def _check_for_wake_word(self, text: str) -> None:
        current_time = time.time()
        if current_time - self._last_detection_time < 2.0:
            return
        for wake_word in self.wake_words:
            if wake_word in text:
                self._handle_detection(wake_word, text, current_time)
                break

    def _handle_detection(
        self, wake_word: str, full_text: str, current_time: float
    ) -> None:
        self._detection_count += 1
        self._last_detection_time = current_time

        logger.info(f"   WAKE WORD DETECTED!")
        logger.info(f"   Word: '{wake_word}'")
        logger.info(f"   Full text: '{full_text}'")
        logger.info(f"   Detection #{self._detection_count}")
        logger.info(f"   Time: {time.strftime('%H:%M:%S')}")

        self._activation_queue.put(
            {
                "wake_word": wake_word,
                "full_text": full_text,
                "timestamp": current_time,
                "count": self._detection_count,
            }
        )

        if self.on_wake_word:
            try:
                self.on_wake_word()
            except Exception as e:
                logger.error(f"Error in wake word callback: {e}")
