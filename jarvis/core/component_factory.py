import os
from typing import Optional
from ..config import Config
from ..llm import LLM
from ..llm.providers import create_provider as create_llm_provider
from ..dispatch import DispatchAdapter, GoalManager, EventMerger
from ..contextor import ContextorAdapter
from ..kernel_client import KernelClient, provider_from_config
from .system_info import SystemInfo
from .command_parser import TaskParser
from .confirmation_manager import ConfirmationManager
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
            provider_kwargs["base_url"] = Config.LLM_URL
            provider_kwargs["api_key"] = Config.LLM_API_KEY
            provider_kwargs["auto_pull"] = getattr(Config, "LLM_AUTO_PULL", False)
            provider_kwargs["temperature"] = getattr(Config, "LLM_TEMPERATURE", 0.7)
            provider_kwargs["strict_json"] = getattr(Config, "LLM_STRICT_JSON", False)
        elif provider_type == "api":
            if not Config.LLM_URL:
                raise ValueError("LLM_URL must be set when using API provider")
            if not Config.LLM_API_KEY:
                raise ValueError("LLM_API_KEY must be set when using API provider")
            provider_kwargs["api_url"] = Config.LLM_URL
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

        fmt = dict(
            system=system_info['system'],
            release=system_info['release'],
            version=system_info['version'],
            machine=system_info['machine'],
            shell=system_info['shell'],
            data_consent_note=Config.DATA_CONSENT_NOTE,
        )

        root_prompt = (
            Config.LLM_ROOT_PROMPT.format(**fmt)
            if Config.CONTEXTOR_ENABLED
            else Config.LLM_ROOT_PROMPT_NO_CONTEXTOR.format(**fmt)
        )

        # Default dispatch prompt is the keyword variant. main.py swaps
        # in the embedding variant at dispatch entry when the gate selects
        # that backend (see DispatchAdapter.select_discovery_mode).
        default_dispatch_prompt = Config.LLM_DISPATCH_PROMPT_KEYWORD
        if "{system}" in default_dispatch_prompt:
            default_dispatch_prompt = default_dispatch_prompt.format(**fmt)

        prompts = {
            "root": root_prompt,
            "dispatch": default_dispatch_prompt,
        }

        return LLM(
            provider=llm_provider,
            prompts=prompts,
            wrong_json_message=Config.LLM_WRONG_JSON_FORMAT_MESSAGE,
        )

    @staticmethod
    def create_dispatch_adapter() -> DispatchAdapter:
        """Create DispatchAdapter for connecting to the dispatch binary."""
        logger.info("Initiating Dispatch adapter...")
        return DispatchAdapter()

    @staticmethod
    def create_goal_manager() -> GoalManager:
        """Create GoalManager for tracking user goals."""
        logger.info("Initiating Goal manager...")
        return GoalManager()

    @staticmethod
    def create_event_merger() -> EventMerger:
        """Create EventMerger for dual-input event loop."""
        logger.info("Initiating Event merger...")
        return EventMerger()

    @staticmethod
    def create_contextor(embeddings=None) -> ContextorAdapter:
        """
        Create ContextorAdapter — thin stdio client to the Rust binary.

        The binary handles SQLite storage, vector indexing, and cosine
        search.  JARVIS passes pre-computed vectors via the adapter.
        """
        logger.info("Initiating Contextor adapter...")
        adapter = ContextorAdapter(embeddings=embeddings)
        adapter.connect()
        return adapter

    @staticmethod
    def create_task_parser() -> TaskParser:
        """Create TaskParser for validating LLM dispatch responses."""
        return TaskParser()

    @staticmethod
    def create_confirmation_manager() -> ConfirmationManager:
        """Create ConfirmationManager for TLA confirmation gates."""
        logger.info("Initiating Confirmation manager...")
        return ConfirmationManager()

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
    def create_output_manager(
        tts: Optional[any] = None,
        suppress_stdout: bool = False,
    ) -> OutputManager:
        """
        Create OutputManager with optional TTS

        Args:
            tts: Optional TTS instance
        """
        return OutputManager(tts, suppress_stdout=suppress_stdout)

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
    def create_kernel_client() -> KernelClient:
        """
        Create and start the /dev/jarvis kernel client.

        Starts the background poll thread if the kernel module is loaded.
        Always returns a KernelClient — it does nothing if /dev/jarvis is absent.
        """
        logger.info("Initiating kernel client (/dev/jarvis)...")
        client = KernelClient()
        available = client.start()
        if available:
            logger.info("Kernel integration active — /dev/jarvis connected")
        else:
            logger.info("Kernel integration unavailable — running userspace-only mode")
        return client

    @staticmethod
    def create_all_components(
        text_mode: bool = False,
        on_voice_command=None,
        suppress_stdout_output: bool = False,
    ):
        """
        Create all JARVIS components

        Args:
            text_mode: If True, skip voice input components (STT, Voice Activation)
            on_voice_command: Callback for voice commands

        Returns:
            Dictionary of all initialized components
        """
        components = {}

        # Kernel integration client (started first — LLM providers may use keyring)
        components['kernel_client'] = ComponentFactory.create_kernel_client()

        # Shared embeddings instance — used by both contextor (memory)
        # and dispatch (semantic tool discovery). Created once, shared.
        embeddings = None
        if Config.RAG_ENABLED or Config.ALLOW_EMBEDDING_SEARCH:
            try:
                from ..contextor.embeddings import OllamaEmbeddings
                embeddings = OllamaEmbeddings(model=Config.EMBED_MODEL)
                if not embeddings.ensure_model():
                    logger.warning(
                        f"Embedding model '{Config.EMBED_MODEL}' not available. "
                        f"Semantic search disabled."
                    )
                    embeddings = None
            except ImportError as e:
                logger.warning(f"Embeddings unavailable: {e}")
            except Exception as e:
                logger.warning(f"Embeddings init failed (non-fatal): {e}")

        # One-shot warning when embedding tool discovery was requested but
        # no embeddings instance was produced. Dispatch will silently fall
        # back to keyword discovery.
        if Config.ALLOW_EMBEDDING_SEARCH and embeddings is None:
            logger.warning(
                "ALLOW_EMBEDDING_SEARCH is enabled but embeddings are unavailable — "
                "dispatch will use keyword discovery for this session."
            )
        components['embeddings'] = embeddings

        # Contextor — spawns the Rust binary, passes shared embeddings
        if Config.CONTEXTOR_ENABLED:
            try:
                components['contextor'] = ComponentFactory.create_contextor(
                    embeddings=embeddings
                )
            except Exception as e:
                logger.warning(f"Contextor init failed (non-fatal): {e}")
                components['contextor'] = None
        else:
            components['contextor'] = None

        # LLM — no longer needs contextor (RAG is handled in main.py)
        components['llm'] = ComponentFactory.create_llm()

        components['dispatch_adapter'] = ComponentFactory.create_dispatch_adapter()
        components['goal_manager'] = ComponentFactory.create_goal_manager()
        components['event_merger'] = ComponentFactory.create_event_merger()
        components['task_parser'] = ComponentFactory.create_task_parser()
        components['confirmation_manager'] = ComponentFactory.create_confirmation_manager()

        # TTS (optional - only if voice output enabled and available)
        components['tts'] = ComponentFactory.create_tts_optional()

        # Dependent components
        components['output_manager'] = ComponentFactory.create_output_manager(
            components['tts'],
            suppress_stdout=suppress_stdout_output,
        )

        # Voice input components (only if not in text mode and available)
        if not text_mode and on_voice_command:
            components['voice_manager'] = ComponentFactory.create_voice_manager_optional(
                on_voice_command
            )
        else:
            components['voice_manager'] = None

        # Report active LLM provider to kernel sysfs
        kernel_client = components['kernel_client']
        if kernel_client.available:
            k_provider = provider_from_config(Config.LLM_PROVIDER)
            kernel_client.report_provider(k_provider, Config.LLM_MODEL)

        logger.info("Initiations Complete!")
        return components
