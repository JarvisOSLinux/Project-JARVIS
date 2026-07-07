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
        is no longer running. Yields nothing during pure silence -- callers
        that need to detect "no speech at all" (e.g. a post-wake-word
        timeout) must use ``read()`` instead, since a silent ``for`` loop
        over this generator never re-enters its body to check a deadline.
        """

    @abstractmethod
    def read(self, timeout: Optional[float] = None) -> Optional[Tuple[str, bool]]:
        """Pop the next ``(text, is_final)`` result, waiting up to ``timeout``
        seconds. Returns None if none arrives in time, so callers can bound
        how long they wait through stretches of silence.
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
        """Synthesise *text* and play it through the default audio output.

        Must return early (without raising) if ``stop()`` is called from
        another thread mid-playback -- barge-in interrupts playback at
        chunk granularity.
        """

    @abstractmethod
    def stop(self) -> None:
        """Request that any in-progress ``say()`` stop as soon as possible.

        Thread-safe; a no-op when nothing is playing.
        """


class EchoCanceller(ABC):
    """Abstract acoustic echo canceller (AEC).

    Removes JARVIS's own TTS output from the live microphone signal so
    the wake word ("Jarvis" -- the assistant's own name) and STT don't
    mistake JARVIS's own voice for user speech during barge-in
    (Project-JARVIS#143). Implementations see two independent int16 mono
    PCM streams -- the near-end microphone and the far-end reference
    (JARVIS's own TTS output) -- and must keep them aligned internally.
    """

    @abstractmethod
    def process(self, mic_frame: bytes) -> bytes:
        """Return an echo-cancelled copy of a near-end (mic) chunk.

        ``mic_frame`` may be any length; implementations that require
        fixed-size internal frames buffer partial input and may return
        fewer bytes than they were given, catching up on the next call.
        """

    @abstractmethod
    def feed_reference(self, reference_frame: bytes) -> None:
        """Feed a chunk of far-end audio (JARVIS's own TTS output) as it plays.

        Must be called from the TTS output path in real time -- the AEC
        needs this to know what echo to expect on the mic.
        """

    @abstractmethod
    def reset(self) -> None:
        """Clear internal adaptive-filter/buffer state, e.g. between turns."""


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
