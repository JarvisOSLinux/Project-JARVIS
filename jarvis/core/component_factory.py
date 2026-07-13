import json
import os
from typing import Optional

from ..config import Config
from ..contextor import ContextorAdapter
from ..dispatch import DispatchAdapter, EventMerger, GoalManager
from ..kernel_client import KernelClient, provider_from_config
from ..llm import LLM
from ..llm.provider_pool import ProviderEntry, ProviderPool
from ..llm.providers import create_provider as create_llm_provider
from ..voice.audio import (
    AudioUnavailableError,
    check_audio_input_available,
    check_audio_output_available,
)
from .command_parser import TaskParser
from .confirmation_manager import ConfirmationManager
from .logger import get_logger
from .output_manager import OutputManager
from .system_info import SystemInfo

logger = get_logger(__name__)


class ComponentFactory:
    @staticmethod
    def _build_provider_pool() -> ProviderPool:
        """Build a ProviderPool from providers.json or legacy env vars."""
        providers_file = Config.PROVIDERS_FILE

        if os.path.isfile(providers_file):
            logger.info(f"Loading provider pool from {providers_file}")
            with open(providers_file) as f:
                data = json.load(f)

            entries = []
            for spec in data.get("providers", []):
                name = spec.get("name", f"provider-{len(entries)}")
                ptype = spec.get("type", "ollama")
                model = spec.get("model", "")
                if not model:
                    logger.warning(f"Provider '{name}' has no model, skipping")
                    continue

                kwargs = {}
                if ptype == "ollama":
                    kwargs["base_url"] = spec.get("url", "http://localhost:11434")
                    if spec.get("api_key"):
                        kwargs["api_key"] = spec["api_key"]
                    kwargs["auto_pull"] = spec.get("auto_pull", False)
                    kwargs["temperature"] = spec.get(
                        "temperature",
                        getattr(Config, "LLM_TEMPERATURE", 0.7),
                    )
                    kwargs["strict_json"] = spec.get("strict_json", False)
                    llm_think = getattr(Config, "LLM_THINK", None)
                    if spec.get("think") is not None:
                        kwargs["think"] = spec["think"]
                    elif llm_think is not None:
                        kwargs["think"] = llm_think
                elif ptype in ("api", "lmstudio"):
                    default_url = (
                        "http://localhost:1234/v1" if ptype == "lmstudio" else ""
                    )
                    kwargs["api_url"] = spec.get("url") or default_url
                    kwargs["api_key"] = spec.get("api_key", "")
                    if spec.get("headers"):
                        kwargs["headers"] = spec["headers"]

                try:
                    provider = create_llm_provider(
                        provider=ptype, model=model, **kwargs
                    )
                    entries.append(ProviderEntry(provider=provider, name=name))
                    logger.info(f"  [{len(entries)}] {name}: {ptype}/{model}")
                except Exception as e:
                    logger.warning(f"Failed to create provider '{name}': {e}")

            if entries:
                return ProviderPool(entries)

        return None

    @staticmethod
    def create_llm() -> Optional[LLM]:
        """Create LLM with provider pool (failover-capable).

        Returns None if no providers are configured.
        """
        logger.info("Getting system information...")
        system_info = SystemInfo.get_system_info()

        pool = ComponentFactory._build_provider_pool()
        if pool is None:
            logger.warning("No LLM providers configured — running without LLM")
            return None
        logger.info(
            f"Provider pool ready: {len(pool.entries)} provider(s), "
            f"active: {pool.active_provider_name}"
        )

        fmt = dict(
            system=system_info["system"],
            release=system_info["release"],
            version=system_info["version"],
            machine=system_info["machine"],
            shell=system_info["shell"],
            data_consent_note=Config.DATA_CONSENT_NOTE,
        )

        # Unified root prompt: handles tool discovery, install, dispatch, memory
        # all in a single LLM loop without a separate dispatch sub-chain.
        root_prompt = (
            Config.LLM_ROOT_PROMPT_UNIFIED.format(**fmt)
            if Config.CONTEXTOR_ENABLED
            else Config.LLM_ROOT_PROMPT_UNIFIED_NO_CONTEXTOR.format(**fmt)
        )

        # Keep the legacy dispatch prompt available for backwards-compat
        # (confirmation-resume flow, any code still referencing dispatch mode).
        default_dispatch_prompt = Config.LLM_DISPATCH_PROMPT_KEYWORD
        if "{system}" in default_dispatch_prompt:
            default_dispatch_prompt = default_dispatch_prompt.format(**fmt)

        prompts = {
            "root": root_prompt,
            "dispatch": default_dispatch_prompt,
        }

        return LLM(
            provider=pool,
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
                model_path=os.path.join(
                    Config.MODELS_DIR, "piper", Config.TTS_MODEL_ONNX
                ),
                config_path=os.path.join(
                    Config.MODELS_DIR, "piper", Config.TTS_MODEL_JSON
                ),
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
    def create_echo_canceller_optional(tts: Optional[any]) -> Optional[any]:
        """
        Create the shared acoustic echo canceller (AEC) if enabled and available.

        Requires an active TTS provider -- it supplies the far-end reference
        signal, and with no TTS there is nothing for the mic to echo. The
        returned instance must be shared between the TTS output path and
        every mic-reading STT/activation provider: they need to agree on
        what "JARVIS's own voice" sounds like right now (Project-JARVIS#143).
        """
        if not Config.AEC_ENABLED:
            return None
        if tts is None:
            logger.debug("AEC disabled: no TTS provider active (nothing to echo)")
            return None

        try:
            from ..voice.aec import create_echo_canceller
        except ImportError as e:
            logger.warning(f"AEC dependencies not available: {e}")
            return None

        try:
            logger.info("Initiating acoustic echo canceller (webrtc)...")
            aec = create_echo_canceller(
                provider="webrtc",
                sample_rate=16000,
                reference_sample_rate=getattr(tts, "sample_rate", 22050),
                stream_delay_ms=Config.AEC_STREAM_DELAY_MS,
            )
            logger.info("Echo canceller initialized successfully")
            return aec
        except Exception as e:
            logger.warning(f"Failed to initialize echo canceller (non-fatal): {e}")
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
    def create_voice_manager_optional(
        on_command, echo_canceller: Optional[any] = None
    ) -> Optional[any]:
        """
        Create VoiceManager if voice input is enabled and audio is available.

        Providers are selected by ``Config.STT_PROVIDER`` and
        ``Config.ACTIVATION_PROVIDER``.

        Args:
            on_command: Callback for voice commands.
            echo_canceller: Optional shared AEC instance (Project-JARVIS#143).

        Returns:
            VoiceManager instance or None if unavailable.
        """
        if not check_audio_input_available():
            logger.warning("Audio input devices not available, voice manager disabled")
            return None

        try:
            from ..voice.activation import create_activation
            from ..voice.manager import VoiceManager
            from ..voice.stt import create_stt
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
                silence_timeout=1.0,
                noise_gate_threshold=Config.NOISE_GATE_RMS_THRESHOLD,
                echo_canceller=echo_canceller,
            )

            activation = create_activation(
                provider=Config.ACTIVATION_PROVIDER,
                wake_words=Config.WAKE_WORDS,
                model_path=Config.VOSK_MODEL_PATH,
                sensitivity=Config.VOICE_ACTIVATION_SENSITIVITY,
                noise_gate_threshold=Config.NOISE_GATE_RMS_THRESHOLD,
                echo_canceller=echo_canceller,
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
        components["kernel_client"] = ComponentFactory.create_kernel_client()

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
        components["embeddings"] = embeddings

        # Contextor — spawns the Rust binary, passes shared embeddings
        if Config.CONTEXTOR_ENABLED:
            try:
                components["contextor"] = ComponentFactory.create_contextor(
                    embeddings=embeddings
                )
            except Exception as e:
                logger.warning(f"Contextor init failed (non-fatal): {e}")
                components["contextor"] = None
        else:
            components["contextor"] = None

        # LLM — no longer needs contextor (RAG is handled in main.py)
        components["llm"] = ComponentFactory.create_llm()

        components["dispatch_adapter"] = ComponentFactory.create_dispatch_adapter()
        components["goal_manager"] = ComponentFactory.create_goal_manager()
        components["event_merger"] = ComponentFactory.create_event_merger()
        components["task_parser"] = ComponentFactory.create_task_parser()
        components["confirmation_manager"] = (
            ComponentFactory.create_confirmation_manager()
        )

        # TTS (optional - only if voice output enabled and available)
        components["tts"] = ComponentFactory.create_tts_optional()

        # Shared echo canceller (Project-JARVIS#143) -- built after TTS so it
        # can match the reference signal's sample rate, then handed to TTS
        # (reference feed) and STT/activation (mic-side cancellation) alike.
        components["echo_canceller"] = ComponentFactory.create_echo_canceller_optional(
            components["tts"]
        )
        if components["echo_canceller"] is not None and components["tts"] is not None:
            components["tts"].echo_canceller = components["echo_canceller"]

        # Dependent components
        components["output_manager"] = ComponentFactory.create_output_manager(
            components["tts"],
            suppress_stdout=suppress_stdout_output,
        )

        # Voice input components (only if not in text mode and available)
        if not text_mode and on_voice_command:
            components["voice_manager"] = (
                ComponentFactory.create_voice_manager_optional(
                    on_voice_command, echo_canceller=components["echo_canceller"]
                )
            )
        else:
            components["voice_manager"] = None

        # Report active LLM provider to kernel sysfs
        kernel_client = components["kernel_client"]
        if kernel_client.available and components["llm"] is not None:
            pool = components["llm"].provider
            pname = pool.active_provider_name or "unknown"
            model = getattr(pool, "model", "") or ""
            k_provider = provider_from_config(pname)
            kernel_client.report_provider(k_provider, model)

        logger.info("Initiations Complete!")
        return components
