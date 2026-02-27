"""
Voice manager for JARVIS AI Assistant

Orchestrates voice activation (wake word detection) and
voice command processing (speech-to-text).
"""

import time
from typing import Callable, Optional
from ..core.logger import get_logger

logger = get_logger(__name__)


class VoiceManager:
    """Manages voice activation and command processing.

    This class coordinates wake word detection and speech-to-text,
    ensuring exclusive audio access (only one holds the mic at a time).
    """

    def __init__(
        self,
        on_command: Callable[[str], None],
        model_path: str = "vosk-model-small-en-us-0.15",
        wake_words: Optional[list] = None,
        sensitivity: float = 0.8,
        sample_rate: int = 16000,
        chunk_size: int = 4000,
        phrase_timeout: float = 3.0,
        silence_timeout: float = 1.0,
    ):
        """
        Initialize voice manager.

        Args:
            on_command: Callback called with transcribed text when a voice command is received.
            model_path: Path to Vosk model directory.
            wake_words: List of wake words to detect.
            sensitivity: Wake word detection sensitivity (0.0 to 1.0).
            sample_rate: Audio sample rate in Hz.
            chunk_size: Audio chunk size for processing.
            phrase_timeout: Timeout for phrase completion in seconds.
            silence_timeout: Timeout for silence detection in seconds.

        Raises:
            AudioUnavailableError: If audio packages or devices are unavailable.
        """
        from .audio import check_audio_input_available, AudioUnavailableError

        self.on_command = on_command
        self._wake_word_detected = False

        if not check_audio_input_available():
            raise AudioUnavailableError(
                "No audio input devices available. Cannot initialize voice manager."
            )

        try:
            from .stt import SpeechToText
            from .activation import VoiceActivation
        except ImportError as e:
            raise AudioUnavailableError(
                f"Voice components dependencies not available: {e}. "
                "Install with: pip install vosk sounddevice"
            ) from e

        try:
            self.stt = SpeechToText(
                model_path=model_path,
                sample_rate=sample_rate,
                chunk_size=chunk_size,
                phrase_timeout=phrase_timeout,
                silence_timeout=silence_timeout,
                device_index=None,
            )

            self.voice_activation = VoiceActivation(
                wake_words=wake_words or ["jarvis", "hey jarvis"],
                model_path=model_path,
                sample_rate=sample_rate,
                chunk_size=chunk_size,
                sensitivity=sensitivity,
                on_wake_word=self._on_wake_word_detected,
            )
        except AudioUnavailableError:
            raise
        except Exception as e:
            raise AudioUnavailableError(
                f"Failed to initialize voice components: {e}"
            ) from e

    def listen_once(self, timeout: Optional[float] = None) -> Optional[str]:
        """
        Start STT, wait for a single final utterance, stop STT, and return the text.

        This is the proper way for external code to get voice input without
        reaching into internal STT state.

        Args:
            timeout: Maximum seconds to wait. None means wait indefinitely.

        Returns:
            The transcribed text, or None if timed out or nothing was captured.
        """
        self.stt.start()
        try:
            start_time = time.time()
            for text, is_final in self.stt.iter_results():
                if timeout is not None and (time.time() - start_time) > timeout:
                    return None
                if is_final and text.strip():
                    return text.strip()
        finally:
            self.stt.stop()
        return None

    def start_voice_activation_mode(self) -> bool:
        """
        Start voice activation mode (wake word detection).

        Returns:
            True if exited normally, False on failure.
        """
        try:
            logger.info("Starting JARVIS with voice activation...")
            logger.info("Say 'Jarvis' to activate me!")
            logger.info("Press Ctrl+C to stop.\n")

            if not self.voice_activation.start_listening():
                logger.error("Failed to start voice activation")
                return False

            while True:
                if self._wake_word_detected:
                    self._wake_word_detected = False
                    self._process_voice_command()
                time.sleep(0.5)

        except KeyboardInterrupt:
            logger.info("\nShutting down...")
            return True
        finally:
            self.voice_activation.cleanup()

    def start_continuous_listening_mode(self) -> None:
        """Start continuous listening mode (no wake word, always transcribing)."""
        try:
            self.stt.start()
            logger.info("I am listening.")
            logger.info("Listening... Ctrl+C to stop.\n")

            for text, is_final in self.stt.iter_results():
                if is_final:
                    logger.info(text)
                    self.on_command(text)

        except KeyboardInterrupt:
            pass
        finally:
            self.stt.stop()

    def _on_wake_word_detected(self) -> None:
        """Callback when wake word is detected."""
        logger.debug("Wake word detected! Setting flag...")
        self._wake_word_detected = True

    def _process_voice_command(self) -> None:
        """Process voice command after wake word detection."""
        logger.debug("Starting voice processing...")

        # Stop voice activation to free up audio resources
        self.voice_activation.stop_listening()

        # Start STT processing
        self.stt.start()
        try:
            logger.info("Listening for your command...")
            for text, is_final in self.stt.iter_results():
                if is_final and text.strip():
                    logger.info(f"Final Input: {text}")
                    response = self.on_command(text)

                    # Check if LLM response ends with a question — keep listening
                    if response and isinstance(response, dict):
                        output = response.get('output', '')
                        if output.rstrip().endswith('?'):
                            logger.info("LLM asked a follow-up question, listening for response (10s timeout)...")
                            follow_up_start = time.time()
                            got_follow_up = False
                            for follow_text, follow_final in self.stt.iter_results():
                                if time.time() - follow_up_start > 10:
                                    logger.info("Follow-up listen timed out after 10 seconds")
                                    break
                                if follow_final and follow_text.strip():
                                    logger.info(f"Follow-up Input: {follow_text}")
                                    self.on_command(follow_text)
                                    got_follow_up = True
                                    break
                            if not got_follow_up:
                                logger.info("No follow-up received, returning to wake word mode")

                    break  # Exit after processing one command
        except Exception as e:
            logger.error(f"Error processing voice command: {e}")
        finally:
            self.stt.stop()
            logger.debug("Voice processing completed. Restarting wake word detection...")

            if not self.voice_activation.start_listening():
                logger.error("Failed to restart voice activation")

    def cleanup(self) -> None:
        """Clean up voice resources."""
        if hasattr(self, 'voice_activation'):
            self.voice_activation.cleanup()
        if hasattr(self, 'stt'):
            self.stt.stop()
