"""
Voice I/O and activation package for JARVIS.

Provides abstract provider interfaces, concrete implementations
(Vosk STT, Piper TTS, Vosk activation), sub-package factories,
audio device detection, and the VoiceManager orchestrator.

Quick usage::

    from jarvis.voice.stt import create_stt
    from jarvis.voice.tts import create_tts
    from jarvis.voice.activation import create_activation

    stt = create_stt("vosk", model_path="...")
    tts = create_tts("piper", model_path="...", config_path="...")
    act = create_activation("vosk", wake_words=["jarvis"])
"""

from .activation import create_activation

# Audio utilities
from .audio import (
    AudioUnavailableError,
    check_audio_input_available,
    check_audio_output_available,
    get_default_input_device,
    get_default_output_device,
    list_audio_devices,
)

# Abstract interfaces
from .base import ActivationProvider, STTProvider, TTSProvider

# Orchestrator
from .manager import VoiceManager

# Sub-package factories
from .stt import create_stt
from .tts import create_tts

__all__ = [
    # Abstract interfaces
    "STTProvider",
    "TTSProvider",
    "ActivationProvider",
    # Audio utilities
    "AudioUnavailableError",
    "check_audio_input_available",
    "check_audio_output_available",
    "list_audio_devices",
    "get_default_input_device",
    "get_default_output_device",
    # Factories
    "create_stt",
    "create_tts",
    "create_activation",
    # Orchestrator
    "VoiceManager",
]
