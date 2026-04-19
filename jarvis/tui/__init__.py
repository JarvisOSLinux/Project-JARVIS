"""
Interactive TUI frontend for JARVIS (Textual-based).

This is the OpenClaw-style chat surface: a session sidebar, a scrollable
chat log, an input box, and a status line, all in the terminal.  It is
a *frontend* — it drives the same ``Jarvis`` engine used by ``jarvis chat``
and ``jarvis run``.

Install with:
    pip install "jarvis-ai[tui]"

Launch with:
    jarvis tui

Design notes:
  * The TUI owns stdin.  ``Jarvis(tui_mode=True)`` skips the stdin event
    source; user input is fed in via ``events.inject_user_input()``.
  * Output is rendered by registering a callback on ``output_manager``.
  * Sessions are driven directly through ``SessionManager`` — no slash
    commands required (though typed ``/new``, ``/switch`` etc. still work
    via the existing handlers in ``main.py``).
"""

from .app import JarvisTUI, run_tui

__all__ = ["JarvisTUI", "run_tui"]
