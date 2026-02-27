import os
from typing import Optional
from ..config import Config
from ..llm import LLM
from ..llm.providers import create_provider as create_llm_provider
from ..supermcp_client import SuperMCPWrapper
from .system_info import SystemInfo
from .command_parser import SuperMCPCommandParser
from .output_manager import OutputManager
from ..voice.audio import check_audio_output_available, check_audio_input_available, AudioUnavailableError
from .logger import get_logger

logger = get_logger(__name__)


class ComponentFactory:
    @staticmethod
    def create_llm() -> LLM:
        """Create LLM with provider selected by ``Config.LLM_PROVIDER``."""
        import json as _json

        logger.info("Getting system information...")
        system_info = SystemInfo.get_system_info()

        logger.info(f"Initiating LLM (provider: {Config.LLM_PROVIDER})...")

        # Build provider-specific kwargs
        provider_kwargs = {}
        provider_type = Config.LLM_PROVIDER.lower()

        if provider_type == "ollama":
            provider_kwargs["base_url"] = Config.LLM_OLLAMA_URL
            provider_kwargs["auto_pull"] = getattr(Config, "LLM_AUTO_PULL", False)
        elif provider_type == "api":
            if not Config.LLM_API_URL:
                raise ValueError("LLM_API_URL must be set when using API provider")
            if not Config.LLM_API_KEY:
                raise ValueError("LLM_API_KEY must be set when using API provider")
            provider_kwargs["api_url"] = Config.LLM_API_URL
            provider_kwargs["api_key"] = Config.LLM_API_KEY
            if Config.LLM_API_HEADERS:
                try:
                    provider_kwargs["headers"] = _json.loads(Config.LLM_API_HEADERS)
                except _json.JSONDecodeError:
                    logger.warning(f"Invalid LLM_API_HEADERS JSON, ignoring")

        llm_provider = create_llm_provider(
            provider=provider_type,
            model=Config.LLM_MODEL,
            **provider_kwargs,
        )

        # For Ollama, ensure model is available
        if provider_type == "ollama":
            if not llm_provider.is_available():
                logger.warning("Ollama provider configured but not available.")
            else:
                logger.info(f"Checking if model '{Config.LLM_MODEL}' is available...")
                if not llm_provider.ensure_model():
                    raise RuntimeError(
                        f"Model '{Config.LLM_MODEL}' is not available. "
                        "Please install it or enable auto-pull."
                    )

        # Build system prompt from template + system info
        system_prompt = Config.LLM_RULE.format(
            system=system_info['system'],
            release=system_info['release'],
            version=system_info['version'],
            machine=system_info['machine'],
            shell=system_info['shell'],
        )

        return LLM(
            provider=llm_provider,
            system_prompt=system_prompt,
            wrong_json_message=Config.LLM_WRONG_JSON_FORMAT_MESSAGE,
        )
    
    @staticmethod
    def create_tts_optional() -> Optional[any]:
        """
        Create TTS provider if voice output is enabled and audio is available.

        The concrete provider is selected by ``Config.TTS_PROVIDER``.

        Returns:
            A TTSProvider instance or None if unavailable.
        """
        if Config.OUTPUT_MODE != "voice":
            logger.debug("TTS not needed (OUTPUT_MODE != 'voice')")
            return None

        if not check_audio_output_available():
            logger.warning("Audio output devices not available, TTS disabled")
            return None

        try:
            from ..voice.tts import create_tts
        except ImportError as e:
            logger.warning(f"TTS dependencies not available: {e}")
            return None

        try:
            logger.info(f"Initiating TTS (provider: {Config.TTS_PROVIDER})...")
            tts = create_tts(
                provider=Config.TTS_PROVIDER,
                model_path=os.path.join(Config.MODELS_DIR, "piper", Config.TTS_MODEL_ONNX),
                config_path=os.path.join(Config.MODELS_DIR, "piper", Config.TTS_MODEL_JSON),
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
    def create_output_manager(tts: Optional[any] = None) -> OutputManager:
        """
        Create OutputManager with optional TTS
        
        Args:
            tts: Optional TTS instance
        """
        return OutputManager(tts)
    
    @staticmethod
    def create_voice_manager_optional(on_command) -> Optional[any]:
        """
        Create VoiceManager if voice input is enabled and audio is available.

        Providers are selected by ``Config.STT_PROVIDER`` and
        ``Config.ACTIVATION_PROVIDER``.

        Args:
            on_command: Callback for voice commands.

        Returns:
            VoiceManager instance or None if unavailable.
        """
        if not check_audio_input_available():
            logger.warning("Audio input devices not available, voice manager disabled")
            return None

        try:
            from ..voice.stt import create_stt
            from ..voice.activation import create_activation
            from ..voice.manager import VoiceManager
        except ImportError as e:
            logger.warning(f"Voice manager dependencies not available: {e}")
            return None

        try:
            logger.info(
                f"Initiating Voice (STT: {Config.STT_PROVIDER}, "
                f"Activation: {Config.ACTIVATION_PROVIDER})..."
            )

            stt = create_stt(
                provider=Config.STT_PROVIDER,
                model_path=Config.VOSK_MODEL_PATH,
                sample_rate=16000,
                chunk_size=4000,
                phrase_timeout=3.0,
                silence_timeout=1.0,
            )

            activation = create_activation(
                provider=Config.ACTIVATION_PROVIDER,
                wake_words=Config.WAKE_WORDS,
                model_path=Config.VOSK_MODEL_PATH,
                sensitivity=Config.VOICE_ACTIVATION_SENSITIVITY,
            )

            vm = VoiceManager(
                on_command=on_command,
                stt=stt,
                activation=activation,
            )
            logger.info("Voice manager initialized successfully")
            return vm
        except Exception as e:
            logger.error(f"Failed to initialize voice manager: {e}", exc_info=True)
            return None
    
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
