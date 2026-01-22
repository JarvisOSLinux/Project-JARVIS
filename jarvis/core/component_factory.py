from typing import Optional
from ..config import Config
from ..llm import LLM
from ..supermcp_client import SuperMCPWrapper
from .system_info import SystemInfo
from .command_parser import SuperMCPCommandParser
from .output_manager import OutputManager
from .audio_detection import check_audio_output_available, AudioUnavailableError
from .logger import get_logger

logger = get_logger(__name__)


class ComponentFactory:
    @staticmethod
    def create_llm() -> LLM:
        logger.info("Getting system information...")
        system_info = SystemInfo.get_system_info()
        
        logger.info("Initiating LLM...")
        return LLM(
            system=system_info['system'],
            release=system_info['release'],
            version=system_info['version'],
            machine=system_info['machine'],
            shell=system_info['shell']
        )
    
    @staticmethod
    def create_tts_optional() -> Optional[Any]:
        """
        Create TTS instance if voice output is enabled and audio is available
        
        Returns:
            TextToSpeech instance or None if unavailable
        """
        # Only create TTS if voice output is requested
        if Config.OUTPUT_MODE != "voice":
            logger.debug("TTS not needed (OUTPUT_MODE != 'voice')")
            return None
        
        # Check audio output availability
        if not check_audio_output_available():
            logger.warning("Audio output devices not available, TTS disabled")
            return None
        
        # Lazy import to avoid import errors when not needed
        try:
            from ..voice_output import TextToSpeech
        except ImportError as e:
            logger.warning(f"TTS dependencies not available: {e}")
            return None
        
        try:
            logger.info("Initiating TTS...")
            tts = TextToSpeech(
                model_path=f"models/piper/{Config.TTS_MODEL_ONNX}",
                config_path=f"models/piper/{Config.TTS_MODEL_JSON}",
            )
            logger.info("TTS initialized successfully")
            return tts
        except AudioUnavailableError as e:
            logger.warning(f"TTS unavailable: {e}")
            return None
        except FileNotFoundError as e:
            logger.warning(f"TTS model files not found: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize TTS: {e}", exc_info=True)
            return None
    
    @staticmethod
    def create_supermcp() -> SuperMCPWrapper:
        logger.info("Initiating SuperMCP...")
        return SuperMCPWrapper()
    
    @staticmethod
    def create_command_parser(supermcp: SuperMCPWrapper) -> SuperMCPCommandParser:
        return SuperMCPCommandParser(supermcp)
    
    @staticmethod
    def create_output_manager(tts: Optional[Any] = None) -> OutputManager:
        """
        Create OutputManager with optional TTS
        
        Args:
            tts: Optional TTS instance
        """
        return OutputManager(tts)
    
    @staticmethod
    def create_voice_manager_optional(on_command) -> Optional[Any]:
        """
        Create VoiceManager if voice input is enabled and audio is available
        
        Args:
            on_command: Callback for voice commands
            
        Returns:
            VoiceManager instance or None if unavailable
        """
        # Check audio input availability
        from .audio_detection import check_audio_input_available
        
        if not check_audio_input_available():
            logger.warning("Audio input devices not available, voice manager disabled")
            return None
        
        # Lazy import to avoid import errors when not needed
        try:
            from ..voice_manager import VoiceManager
        except ImportError as e:
            logger.warning(f"Voice manager dependencies not available: {e}")
            return None
        
        try:
            logger.info("Initiating Voice Activation...")
            vm = VoiceManager(on_command)
            logger.info("Voice manager initialized successfully")
            return vm
        except Exception as e:
            logger.error(f"Failed to initialize voice manager: {e}", exc_info=True)
            return None
    
    @staticmethod
    def create_voice_manager(on_command) -> Optional[Any]:
        """
        Legacy method for backward compatibility
        """
        return ComponentFactory.create_voice_manager_optional(on_command)
    
    @staticmethod
    def create_all_components(text_mode: bool = False, on_voice_command=None):
        """
        Create all JARVIS components

        Args:
            text_mode: If True, skip voice input components (STT, Voice Activation)
            on_voice_command: Callback for voice commands

        Returns:
            Dictionary of all initialized components
        """
        components = {}
        
        # Core components (always needed)
        components['llm'] = ComponentFactory.create_llm()
        components['supermcp'] = ComponentFactory.create_supermcp()
        
        # TTS (optional - only if voice output enabled and available)
        components['tts'] = ComponentFactory.create_tts_optional()
        
        # Dependent components
        components['command_parser'] = ComponentFactory.create_command_parser(
            components['supermcp']
        )
        components['output_manager'] = ComponentFactory.create_output_manager(
            components['tts']
        )
        
        # Voice input components (only if not in text mode and available)
        if not text_mode and on_voice_command:
            components['voice_manager'] = ComponentFactory.create_voice_manager_optional(
                on_voice_command
            )
        else:
            components['voice_manager'] = None
        
        logger.info("Initiations Complete!")
        return components
