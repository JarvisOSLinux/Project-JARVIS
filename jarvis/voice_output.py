import time
from typing import Optional
from .core.audio_detection import check_audio_output_available, get_default_output_device, AudioUnavailableError
from .core.logger import get_logger

logger = get_logger(__name__)


class TextToSpeech:
    def __init__(self, model_path: str, config_path: str):
        """
        Initialize Text-to-Speech with lazy imports
        
        Args:
            model_path: Path to Piper TTS ONNX model file
            config_path: Path to Piper TTS JSON config file
            
        Raises:
            AudioUnavailableError: If audio packages or devices unavailable
            FileNotFoundError: If model files not found
        """
        # Lazy import audio packages
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
        
        # Check audio output availability
        if not check_audio_output_available():
            raise AudioUnavailableError(
                "No audio output devices available. Cannot initialize TTS."
            )
        
        # Load TTS model
        try:
            logger.info(f"Loading TTS model from: {model_path}")
            self.tts = self.PiperVoice.load(model_path=model_path, config_path=config_path)
            logger.info("TTS model loaded successfully")
        except FileNotFoundError as e:
            raise FileNotFoundError(
                f"TTS model files not found. Model: {model_path}, Config: {config_path}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Failed to load TTS model: {e}") from e
        
        # Get default output device
        self.device_index = get_default_output_device()
        if self.device_index is None:
            logger.warning("No default output device found, using system default")
            self.device_index = self.sd.default.device[1]

    def say(self, text: str):
        """
        Synthesize and speak text
        
        Args:
            text: Text to speak
            
        Raises:
            AudioUnavailableError: If audio output fails
        """
        if not text.strip():
            return
        
        try:
            sr = self.tts.config.sample_rate
            # RawOutputStream expects int16 PCM bytes
            with self.sd.RawOutputStream(samplerate=sr, channels=1, dtype="int16",
                                        device=self.device_index, blocksize=0) as stream:
                for chunk in self.tts.synthesize(text):
                    stream.write(chunk.audio_int16_bytes)
                    # Note: Removed sleep delay - was causing choppy audio
        except Exception as e:
            logger.error(f"Error during TTS synthesis: {e}")
            raise AudioUnavailableError(f"Failed to output audio: {e}") from e