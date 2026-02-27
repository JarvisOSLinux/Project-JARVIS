"""
Voice I/O and activation package for JARVIS.

Contains speech-to-text, text-to-speech, wake word activation,
audio device detection, and the voice manager that orchestrates them.
"""

from .audio import (
    AudioUnavailableError,
    check_audio_input_available,
    check_audio_output_available,
    list_audio_devices,
    get_default_input_device,
    get_default_output_device,
)
from .stt import SpeechToText
from .tts import TextToSpeech
from .activation import VoiceActivation
from .manager import VoiceManager

__all__ = [
    "AudioUnavailableError",
    "check_audio_input_available",
    "check_audio_output_available",
    "list_audio_devices",
    "get_default_input_device",
    "get_default_output_device",
    "SpeechToText",
    "TextToSpeech",
    "VoiceActivation",
    "VoiceManager",
]
