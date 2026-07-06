"""Formal voice/response session state machine (Project-JARVIS#141).

Single source of truth for the daemon's voice + response lifecycle:

    IDLE (wake-word listening)
      -> WOKEN       (wake word fired, chime plays)
        -> CAPTURING (STT active; silence timeout in voice_activation_thread.py)
          -> IDLE       (nothing usable was said -- discarded)
          -> PROCESSING (LLM + dispatch running)
            -> SPEAKING (TTS playing the reply)
              -> IDLE

Every transition is broadcast over the GUI socket via
``jarvis.runtime.io.set_gui_state`` as a structured ``{"type": "state", ...}``
event so TUI, jarvisos-app, and any future widget render the same states
without guessing from ad hoc strings.

The manual ``start_listening``/``stop_listening`` GUI messages are a
separate, orthogonal concept (whether the wake-word listener is enabled at
all) and are not part of this enum.
"""

from enum import Enum


class VoiceState(str, Enum):
    IDLE = "idle"
    WOKEN = "woken"
    CAPTURING = "capturing"
    PROCESSING = "processing"
    SPEAKING = "speaking"
