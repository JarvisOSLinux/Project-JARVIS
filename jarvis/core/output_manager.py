from typing import Dict, Any
from ..config import Config
from ..voice_output import TextToSpeech
from .logger import get_logger

logger = get_logger(__name__)


class OutputManager:
    """Manages output formatting and delivery"""

    def __init__(self, tts: TextToSpeech):
        self.tts = tts
    
    def handle_response(self, response: Dict[str, Any]) -> None:
        if Config.OUTPUT_MODE == "voice":
            self._output_voice(response["output"])
        elif Config.OUTPUT_MODE == "text":
            self._output_text(response["output"])
        else:
            # Default to text if unknown mode
            logger.warning(f"Unknown output mode '{Config.OUTPUT_MODE}', using text")
            self._output_text(response["output"])
    
    def _output_voice(self, text: str) -> None:
        self.tts.say(text)
    
    def _output_text(self, text: str) -> None:
        # Output text is intentionally printed to stdout for user visibility
        print(text)
    
    def get_current_mode(self) -> str:
        return Config.OUTPUT_MODE
    
    def is_voice_mode(self) -> bool:
        return Config.OUTPUT_MODE == "voice"
