"""
Abstract base classes for voice providers.

Any new STT engine, TTS engine, or activation engine must implement
the corresponding ABC defined here. The VoiceManager and the rest of
JARVIS only depend on these interfaces, never on concrete providers.
"""

from abc import ABC, abstractmethod
from typing import Callable, Generator, Optional, Tuple


class STTProvider(ABC):
    """Abstract speech-to-text provider.

    Implementations must handle their own audio capture and model
    loading. The lifecycle is: construct -> start() -> iter_results()
    -> stop(), and this cycle can repeat.
    """

    @abstractmethod
    def start(self) -> None:
        """Load model (if needed), open mic, begin background transcription."""

    @abstractmethod
    def stop(self) -> None:
        """Stop transcription and release audio resources."""

    @abstractmethod
    def iter_results(self) -> Generator[Tuple[str, bool], None, None]:
        """Yield ``(text, is_final)`` tuples as speech is recognised.

        Must block between results and stop iterating when the provider
        is no longer running.
        """

    @abstractmethod
    def is_running(self) -> bool:
        """Return True while the provider is actively transcribing."""


class TTSProvider(ABC):
    """Abstract text-to-speech provider.

    Implementations handle model loading in ``__init__`` and audio
    playback in ``say()``.
    """

    @abstractmethod
    def say(self, text: str) -> None:
        """Synthesise *text* and play it through the default audio output."""


class ActivationProvider(ABC):
    """Abstract wake-word / activation provider.

    Implementations listen for a trigger (wake word, hotkey, etc.)
    and invoke a callback when one is detected.
    """

    @abstractmethod
    def start_listening(self) -> bool:
        """Begin listening for the activation trigger.

        Returns True on success, False on failure.
        """

    @abstractmethod
    def stop_listening(self) -> None:
        """Stop listening and release the microphone."""

    @abstractmethod
    def is_listening(self) -> bool:
        """Return True while actively listening for the trigger."""

    @abstractmethod
    def cleanup(self) -> None:
        """Release all resources (model, audio, threads)."""
