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
        text = None
        try:
            logger.info("Listening for your command...")
            for t, is_final in self.stt.iter_results():
                if is_final and t.strip():
                    text = t.strip()
                    break
        except Exception as e:
            logger.error(f"Error capturing voice command: {e}")
        finally:
            self.stt.stop()

        # Wake-word listening resumes the moment capture ends -- NOT after
        # the full LLM/TTS turn -- so "Hey Jarvis, do B" while A is still
        # being worked on queues the next command (Project-JARVIS#142).
        if not self.activation.start_listening():
            logger.error("Failed to restart voice activation")

        if text is None:
            return

        logger.info(f"Final Input: {text}")
        try:
            response = self.on_command(text)
        except Exception as e:
            logger.error(f"Error processing voice command: {e}")
            return

        if response and isinstance(response, dict):
            output = response.get("output", "")
            if output.rstrip().endswith("?"):
                self._listen_for_follow_up()

    def _listen_for_follow_up(self, timeout: float = 10.0) -> None:
        """Reopen capture for a reply to an LLM follow-up question.

        Polls via read() with a bounded wait -- iter_results() never yields
        during pure silence, so a plain loop over it could never enforce
        the timeout (same pitfall as Project-JARVIS#137).
        """
        logger.info(
            f"LLM asked a follow-up question, listening for response "
            f"({timeout:.0f}s timeout)..."
        )
        self.stt.start()
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                result = self.stt.read(timeout=0.2)
                if result is None:
                    continue
                text, is_final = result
                if is_final and text.strip():
                    logger.info(f"Follow-up Input: {text}")
                    self.on_command(text)
                    return
            logger.info("No follow-up received, returning to wake word mode")
        except Exception as e:
            logger.error(f"Error capturing follow-up: {e}")
        finally:
            self.stt.stop()
