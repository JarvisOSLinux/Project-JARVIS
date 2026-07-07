"""Wake-word voice activation loop (runs in a daemon thread)."""

from __future__ import annotations

import asyncio
import time
from logging import Logger
from typing import Any

from ..config import Config
from ..core.voice_state import VoiceState
from ..voice.chime import play_chime


def _broadcast_gui_state(app: Any, state: VoiceState, meta: dict | None = None) -> None:
    """Thread-safe: schedule a GUI-socket state broadcast onto the event loop."""
    events = getattr(app, "events", None)
    if events is None:
        return

    from .io import set_gui_state

    events.call_soon_threadsafe(
        lambda: asyncio.create_task(set_gui_state(app, state, meta))
    )


def _broadcast_wake_word_detected(app: Any) -> None:
    """Thread-safe: unconditionally notify GUI clients a wake word fired.

    Distinct from the WOKEN VoiceState broadcast -- this is the dedicated,
    always-emitted signal jarvisos-app's ipc.rs already parses
    (IpcEvent::WakeWordDetected); the daemon just never sent it before now.
    """
    events = getattr(app, "events", None)
    if events is None:
        return

    from .io import broadcast_to_gui_clients

    events.call_soon_threadsafe(
        lambda: asyncio.create_task(
            broadcast_to_gui_clients(app, {"type": "wake_word_detected"})
        )
    )


def process_voice_command_inject(app: Any, logger: Logger) -> None:
    """Process voice command and inject into event loop (no direct ask).

    Gives up and returns to wake-word mode if the user hasn't started
    speaking within ``VOICE_ACTIVATION_TIMEOUT`` seconds -- ``iter_results()``
    never yields during pure silence, so a plain ``for`` loop over it can't
    detect "nothing was said" (see Project-JARVIS#137). ``read()`` polls with
    a bounded wait instead, so the deadline is actually checked.
    """
    idle_meta: dict | None = None
    try:
        app.voice_manager.activation.stop_listening()
        app.voice_manager.stt.start()
        _broadcast_gui_state(app, VoiceState.CAPTURING)
        try:
            timeout = Config.VOICE_ACTIVATION_TIMEOUT
            deadline = time.monotonic() + timeout if timeout > 0 else None
            speech_started = False

            while app.voice_manager.stt.is_running():
                poll_timeout = 0.2
                if deadline is not None and not speech_started:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        logger.info(
                            "JARVIS: No speech detected after wake word, "
                            "returning to wake-word mode"
                        )
                        idle_meta = {
                            "reason": "discard",
                            "detail": "no speech detected within timeout",
                        }
                        return
                    poll_timeout = min(poll_timeout, remaining)

                result = app.voice_manager.stt.read(timeout=poll_timeout)
                if result is None:
                    continue

                text, is_final = result
                if text.strip():
                    speech_started = True
                if is_final and text.strip():
                    logger.info(f"Voice input: {text}")
                    app.events.inject_user_input(text.strip())
                    return
        finally:
            app.voice_manager.stt.stop()
    except Exception as e:
        logger.error(f"JARVIS: Voice processing error: {e}")
    finally:
        _broadcast_gui_state(app, VoiceState.IDLE, idle_meta)
        if app._running and hasattr(app.voice_manager, "activation"):
            app.voice_manager.activation.start_listening()


def run_voice_activation(app: Any, logger: Logger) -> None:
    """Run voice activation in a thread; commands are injected into the event loop."""
    vm = app.voice_manager
    vm._wake_word_detected = False
    try:
        if hasattr(vm.activation, "on_wake_word"):
            vm.activation.on_wake_word = lambda: setattr(
                vm, "_wake_word_detected", True
            )
        if not app.voice_manager.activation.start_listening():
            logger.error("JARVIS: Failed to start voice activation")
            return
        while app._running:
            if getattr(app.voice_manager, "_wake_word_detected", False):
                app.voice_manager._wake_word_detected = False
                woken_meta = None
                output_manager = getattr(app, "output_manager", None)
                if output_manager is not None and output_manager.is_speaking():
                    # Barge-in: the wake word interrupts JARVIS mid-reply,
                    # exactly like commercial assistants (Project-JARVIS#142).
                    logger.info("JARVIS: Barge-in — stopping TTS for new command")
                    output_manager.stop_speaking()
                    woken_meta = {"barge_in": True}
                _broadcast_wake_word_detected(app)
                _broadcast_gui_state(app, VoiceState.WOKEN, woken_meta)
                # Blocking is intentional: the chime is the audible cue to
                # start talking, so it plays out before the mic reopens for
                # capture rather than racing the user's own speech.
                play_chime(Config.WAKE_CHIME_PATH)
                process_voice_command_inject(app, logger)
            time.sleep(0.3)
    except Exception as e:
        logger.error(f"JARVIS: Voice thread error: {e}", exc_info=True)
    finally:
        if hasattr(app.voice_manager, "activation"):
            app.voice_manager.activation.cleanup()
