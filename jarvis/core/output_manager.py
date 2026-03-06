from typing import Dict, Any, Optional, Callable, List
from ..config import Config
from .logger import get_logger

logger = get_logger(__name__)


class OutputManager:
    """Manages output formatting and delivery.

    Supports pluggable output sinks for app integration. Register callbacks
    via add_output_callback() to receive responses (e.g. for streaming to UI).
    """

    def __init__(self, tts: Optional[Any] = None):
        """
        Initialize OutputManager

        Args:
            tts: Optional TextToSpeech instance. If None, voice output will fallback to text
        """
        self.tts = tts
        self._has_tts = tts is not None
        self._output_callbacks: List[Callable[[Dict[str, Any]], None]] = []

    def add_output_callback(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        """Register a callback to receive every response (for app/stream integration)."""
        self._output_callbacks.append(cb)

    def remove_output_callback(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        """Unregister an output callback."""
        if cb in self._output_callbacks:
            self._output_callbacks.remove(cb)

    def handle_response(self, response: Dict[str, Any]) -> None:
        """
        Handle LLM response output based on configured mode.

        Also notifies registered output callbacks for app integration.
        """
        for cb in self._output_callbacks:
            try:
                cb(response)
            except Exception as e:
                logger.warning(f"Output callback error: {e}")

        if Config.OUTPUT_MODE == "voice":
            self._output_voice(response["output"])
        elif Config.OUTPUT_MODE == "text":
            self._output_text(response["output"])
        else:
            # Default to text if unknown mode
            logger.warning(f"Unknown output mode '{Config.OUTPUT_MODE}', using text")
            self._output_text(response["output"])
    
    def _output_voice(self, text: str) -> None:
        """
        Output text via voice (TTS)
        
        Falls back to text output if TTS unavailable
        
        Args:
            text: Text to speak
        """
        if self._has_tts and self.tts is not None:
            try:
                self.tts.say(text)
            except Exception as e:
                logger.warning(f"TTS failed, falling back to text output: {e}")
                self._output_text(text)
        else:
            logger.info("Voice output requested but TTS unavailable, using text output")
            self._output_text(text)
    
    def _output_text(self, text: str) -> None:
        """
        Output text to stdout
        
        Args:
            text: Text to print
        """
        # Output text is intentionally printed to stdout for user visibility
        print(text)
    
    def get_current_mode(self) -> str:
        """Get current output mode"""
        return Config.OUTPUT_MODE
    
    def is_voice_mode(self) -> bool:
        """Check if voice mode is configured"""
        return Config.OUTPUT_MODE == "voice"
    
    def has_tts(self) -> bool:
        """Check if TTS is available"""
        return self._has_tts