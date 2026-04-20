"""
Voice manager for JARVIS AI Assistant.

Orchestrates voice activation (wake word detection) and
voice command processing (speech-to-text) through abstract
provider interfaces.
"""

import time
from typing import Callable, Optional

from ..core.logger import get_logger
from .base import ActivationProvider, STTProvider

logger = get_logger(__name__)


class VoiceManager:
    """Manages voice activation and command processing.

    This class coordinates an :class:`STTProvider` and an
    :class:`ActivationProvider`, ensuring exclusive audio access
    (only one holds the mic at a time).

    It does **not** know which concrete engine (Vosk, Whisper, etc.)
    is behind the providers — that is decided by the caller.
    """

    def __init__(
        self,
        on_command: Callable[[str], None],
        stt: STTProvider,
        activation: ActivationProvider,
    ):
        """
        Args:
            on_command: Callback invoked with the transcribed text.
            stt: A ready-to-use speech-to-text provider.
            activation: A ready-to-use activation (wake-word) provider.
        """
        self.on_command = on_command
        self.stt = stt
        self.activation = activation
        self._wake_word_detected = False

    # -- public API -----------------------------------------------------------

    def listen_once(self, timeout: Optional[float] = None) -> Optional[str]:
        """Start STT, wait for a single final utterance, stop, and return it.

        Args:
            timeout: Maximum seconds to wait (None = indefinite).

        Returns:
            The transcribed text, or None on timeout.
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
        """Run the wake-word → command → repeat loop.

        Returns True on clean exit, False on activation failure.
        """
        try:
            logger.info("Starting JARVIS with voice activation...")
            logger.info("Say 'Jarvis' to activate me!")
            logger.info("Press Ctrl+C to stop.\n")

            # Wire up the wake-word callback
            if hasattr(self.activation, "on_wake_word"):
                self.activation.on_wake_word = self._on_wake_word_detected

            if not self.activation.start_listening():
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
            self.activation.cleanup()

    def start_continuous_listening_mode(self) -> None:
        """Continuously transcribe and dispatch (no wake word)."""
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

    def cleanup(self) -> None:
        """Release all voice resources."""
        if hasattr(self, "activation"):
            self.activation.cleanup()
        if hasattr(self, "stt"):
            self.stt.stop()

    # -- internals ------------------------------------------------------------

    def _on_wake_word_detected(self) -> None:
        logger.debug("Wake word detected! Setting flag...")
        self._wake_word_detected = True

    def _process_voice_command(self) -> None:
        logger.debug("Starting voice processing...")

        self.activation.stop_listening()
        self.stt.start()
        try:
            logger.info("Listening for your command...")
            for text, is_final in self.stt.iter_results():
                if is_final and text.strip():
                    logger.info(f"Final Input: {text}")
                    response = self.on_command(text)

                    if response and isinstance(response, dict):
                        output = response.get("output", "")
                        if output.rstrip().endswith("?"):
                            logger.info(
                                "LLM asked a follow-up question, listening for response (10s timeout)..."
                            )
                            follow_up_start = time.time()
                            got_follow_up = False
                            for follow_text, follow_final in self.stt.iter_results():
                                if time.time() - follow_up_start > 10:
                                    logger.info(
                                        "Follow-up listen timed out after 10 seconds"
                                    )
                                    break
                                if follow_final and follow_text.strip():
                                    logger.info(f"Follow-up Input: {follow_text}")
                                    self.on_command(follow_text)
                                    got_follow_up = True
                                    break
                            if not got_follow_up:
                                logger.info(
                                    "No follow-up received, returning to wake word mode"
                                )

                    break
        except Exception as e:
            logger.error(f"Error processing voice command: {e}")
        finally:
            self.stt.stop()
            logger.debug(
                "Voice processing completed. Restarting wake word detection..."
            )

            if not self.activation.start_listening():
                logger.error("Failed to restart voice activation")
