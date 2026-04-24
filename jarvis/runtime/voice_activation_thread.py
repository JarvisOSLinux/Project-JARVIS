"""Wake-word voice activation loop (runs in a daemon thread)."""

from __future__ import annotations

import time
from logging import Logger
from typing import Any


def process_voice_command_inject(app: Any, logger: Logger) -> None:
    """Process voice command and inject into event loop (no direct ask)."""
    try:
        app.voice_manager.activation.stop_listening()
        app.voice_manager.stt.start()
        try:
            for text, is_final in app.voice_manager.stt.iter_results():
                if is_final and text.strip():
                    logger.info(f"Voice input: {text}")
                    app.events.inject_user_input(text.strip())
                    break
        finally:
            app.voice_manager.stt.stop()
    except Exception as e:
        logger.error(f"JARVIS: Voice processing error: {e}")
    finally:
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
