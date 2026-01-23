from typing import Dict, Any, Optional
from ..config import Config
from .logger import get_logger

logger = get_logger(__name__)


class OutputManager:
    """Manages output formatting and delivery"""

    def __init__(self, tts: Optional[Any] = None):
        """
        Initialize OutputManager
        
        Args:
            tts: Optional TextToSpeech instance. If None, voice output will fallback to text
        """
        self.tts = tts
        self._has_tts = tts is not None
    
    def handle_response(self, response: Dict[str, Any]) -> None:
        """
        Handle LLM response output based on configured mode
        
        Args:
            response: Response dictionary with 'output' key
        """
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