"""Wake-word voice activation loop (runs in a daemon thread)."""

from __future__ import annotations

import asyncio
import time
from logging import Logger
from typing import Any

from ..config import Config


def _broadcast_gui_state(app: Any, state: str) -> None:
    """Thread-safe: schedule a GUI-socket state broadcast onto the event loop."""
    events = getattr(app, "events", None)
    if events is None:
        return

    from .io import set_gui_state

    events.call_soon_threadsafe(lambda: asyncio.create_task(set_gui_state(app, state)))


def process_voice_command_inject(app: Any, logger: Logger) -> None:
    """Process voice command and inject into event loop (no direct ask).

    Gives up and returns to wake-word mode if the user hasn't started
    speaking within ``VOICE_ACTIVATION_TIMEOUT`` seconds -- ``iter_results()``
    never yields during pure silence, so a plain ``for`` loop over it can't
    detect "nothing was said" (see Project-JARVIS#137). ``read()`` polls with
    a bounded wait instead, so the deadline is actually checked.
    """
    try:
        app.voice_manager.activation.stop_listening()
        app.voice_manager.stt.start()
        _broadcast_gui_state(app, "listening")
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
        _broadcast_gui_state(app, "idle")
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
                process_voice_command_inject(app, logger)
            time.sleep(0.3)
    except Exception as e:
        logger.error(f"JARVIS: Voice thread error: {e}", exc_info=True)
    finally:
        if hasattr(app.voice_manager, "activation"):
            app.voice_manager.activation.cleanup()
