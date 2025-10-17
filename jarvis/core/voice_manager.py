"""
Voice activation management for JARVIS AI Assistant

This module handles voice activation coordination, wake word detection,
and voice command processing.
"""

import time
from typing import Callable, Optional
from ..voice_input import SpeechToText
from ..voice_activation import VoiceActivation
from ..config import Config
from .logger import get_logger

logger = get_logger(__name__)


class VoiceManager:
    """Manages voice activation and command processing"""
    
    def __init__(self, on_command: Callable[[str], None]):
        """
        Initialize voice manager
        
        Args:
            on_command: Callback function called when a voice command is received
        """
        self.on_command = on_command
        self._wake_word_detected = False
        
        # Initialize voice components
        self.stt = SpeechToText(
            model_path=Config.VOSK_MODEL_PATH,
            sample_rate=16000,
            chunk_size=4000,
            phrase_timeout=3.0,
            silence_timeout=1.0,
            device_index=None
        )
        
        self.voice_activation = VoiceActivation(
            wake_words=Config.WAKE_WORDS,
            model_path=Config.VOSK_MODEL_PATH,
            sample_rate=16000,
            chunk_size=4000,
            sensitivity=Config.VOICE_ACTIVATION_SENSITIVITY,
            on_wake_word=self._on_wake_word_detected
        )
    
    def start_voice_activation_mode(self) -> bool:
        """
        Start voice activation mode (wake word detection)
        
        Returns:
            True if started successfully, False otherwise
        """
        try:
            logger.info("Starting JARVIS with voice activation...")
            logger.info("Say 'Jarvis' to activate me!")
            logger.info("Press Ctrl+C to stop.\n")
            
            # Start voice activation
            if not self.voice_activation.start_listening():
                logger.error("Failed to start voice activation")
                return False
            
            # Main loop - check for wake word detection
            while True:
                if self._wake_word_detected:
                    self._wake_word_detected = False  # Reset flag
                    self._process_voice_command()
                time.sleep(0.5)  # Small delay to avoid busy waiting
                    
        except KeyboardInterrupt:
            logger.info("\nShutting down...")
            return True
        finally:
            self.voice_activation.cleanup()
    
    def start_continuous_listening_mode(self) -> None:
        """
        Start continuous listening mode (legacy mode)
        """
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
        """Callback when wake word is detected"""
        logger.debug("Wake word detected! Setting flag...")
        self._wake_word_detected = True
    
    def _process_voice_command(self) -> None:
        """Process voice command after wake word detection"""
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
                    self.on_command(text)
                    break  # Exit after processing one command
        except Exception as e:
            logger.error(f"Error processing voice command: {e}")
        finally:
            self.stt.stop()
            logger.debug("Voice processing completed. Restarting wake word detection...")
            
            # Restart voice activation
            if not self.voice_activation.start_listening():
                logger.error("Failed to restart voice activation")
    
    def cleanup(self) -> None:
        """Clean up voice resources"""
        if hasattr(self, 'voice_activation'):
            self.voice_activation.cleanup()
        if hasattr(self, 'stt'):
            self.stt.stop()
