"""Piper-based text-to-speech provider."""

import threading
from typing import Optional

from ...core.logger import get_logger
from ..audio import (
    AudioUnavailableError,
    check_audio_output_available,
    get_default_output_device,
)
from ..base import EchoCanceller, TTSProvider

logger = get_logger(__name__)


class PiperTTS(TTSProvider):
    """Offline text-to-speech using Piper (ONNX)."""

    def __init__(
        self,
        model_path: str,
        config_path: str,
        echo_canceller: Optional[EchoCanceller] = None,
    ):
        """
        Args:
            model_path: Path to Piper TTS ONNX model file.
            config_path: Path to Piper TTS JSON config file.
            echo_canceller: Optional AEC fed this provider's own output as
                the far-end reference signal, so the mic doesn't pick up
                JARVIS's own voice during barge-in (Project-JARVIS#143).

        Raises:
            AudioUnavailableError: If audio packages or devices unavailable.
            FileNotFoundError: If model files not found.
        """
        try:
            import sounddevice as sd

            self.sd = sd
        except ImportError:
            raise AudioUnavailableError(
                "sounddevice package not installed. Install with: pip install sounddevice"
            )

        try:
            from piper.voice import PiperVoice

            self.PiperVoice = PiperVoice
        except ImportError:
            raise AudioUnavailableError(
                "piper-tts package not installed. Install with: pip install piper-tts"
            )

        if not check_audio_output_available():
            raise AudioUnavailableError(
                "No audio output devices available. Cannot initialize TTS."
            )

        try:
            logger.info(f"Loading TTS model from: {model_path}")
            self.tts = self.PiperVoice.load(
                model_path=model_path, config_path=config_path
            )
            logger.info("TTS model loaded successfully")
        except FileNotFoundError as e:
            raise FileNotFoundError(
                f"TTS model files not found. Model: {model_path}, Config: {config_path}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Failed to load TTS model: {e}") from e

        self.device_index = get_default_output_device()
        if self.device_index is None:
            logger.warning("No default output device found, using system default")
            self.device_index = self.sd.default.device[1]

        self.sample_rate = self.tts.config.sample_rate
        self.echo_canceller = echo_canceller
        self._stop_requested = threading.Event()

    # -- TTSProvider interface ------------------------------------------------

    def say(self, text: str) -> None:
        if not text.strip():
            return

        self._stop_requested.clear()
        try:
            with self.sd.RawOutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                device=self.device_index,
                blocksize=0,
            ) as stream:
                for chunk in self.tts.synthesize(text):
                    if self._stop_requested.is_set():
                        logger.info("TTS playback interrupted (barge-in)")
                        stream.abort()
                        return
                    if self.echo_canceller is not None:
                        try:
                            self.echo_canceller.feed_reference(chunk.audio_int16_bytes)
                        except Exception as e:
                            logger.warning(f"Echo reference feed failed: {e}")
                    stream.write(chunk.audio_int16_bytes)
        except Exception as e:
            logger.error(f"Error during TTS synthesis: {e}")
            raise AudioUnavailableError(f"Failed to output audio: {e}") from e

    def stop(self) -> None:
        self._stop_requested.set()
