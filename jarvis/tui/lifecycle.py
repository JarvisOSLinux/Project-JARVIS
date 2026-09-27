"""TUI lifecycle and engine boot/shutdown helpers."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.widgets import Input, RichLog


def on_mount(app: Any) -> None:
    app.title = "JARVIS"
    app.sub_title = "interactive chat"

    app._append_log("[dim]Booting JARVIS engine...[/dim]")

    # Defer engine start off the Textual mount path so an Ollama
    # connect or contextor spawn doesn't block the first paint.
    app.run_worker(app._start_jarvis(), exclusive=True, name="jarvis-boot")


async def start_jarvis(app: Any, logger: Any) -> None:
    """Create the Jarvis engine and start its event loop."""
    # Lazy import avoids pulling engine deps when only introspecting TUI.
    from ..main import Jarvis

    try:
        jarvis = Jarvis(tui_mode=True)
    except Exception as e:  # pragma: no cover - surfaced to the user
        logger.error(f"TUI: Failed to construct Jarvis: {e}", exc_info=True)
        await enter_offline_mode(app, logger, str(e))
        return

    app.jarvis = jarvis
    app._output_cb = app._on_jarvis_output
    app._activity_cb = app._on_jarvis_activity
    jarvis.output_manager.add_output_callback(app._output_cb)
    jarvis.output_manager.add_activity_callback(app._activity_cb)
    jarvis.config_modal_callback = app._open_config_modal

    if hasattr(jarvis, "confirmation"):
        jarvis.confirmation.set_tui_callback(app._tui_confirm)

    # Kick off the engine's event loop as an async task.
    app._jarvis_task = asyncio.create_task(app._run_engine(), name="jarvis-run")

    # Wait a beat for dispatch/contextor to come up, then seed the UI.
    await asyncio.sleep(0.1)
    await app._refresh_sidebar()
    app._update_status()

    if jarvis.llm is None:
        app._append_log(
            "[yellow]No LLM provider configured.[/yellow]\n"
            "  Use [bold]/providers add[/bold] or open "
            "[bold]Settings (F2)[/bold] to get started.\n"
            "  After adding a provider, restart the TUI."
        )
    else:
        app._append_log(
            "[green]Ready.[/green] Type below or use Ctrl+N for a new chat."
        )
    app.query_one("#input", Input).focus()


async def enter_offline_mode(app: Any, logger: Any, reason: str) -> None:
    """Keep the TUI useful when the engine could not start (#236).

    An engine-less TUI used to be a dead shell: an error line, an empty
    sidebar, and an input that answered "still starting up" forever. The two
    things that do not need an engine still work here — reading the session
    history that lives in contextor, and reviewing the confirmation store,
    whose decisions queue for the next start exactly as ``jarvis confirm``
    queues them.

    There is deliberately no ``/start``: this TUI *embeds* its engine rather
    than attaching to a separate daemon, so there is no second process to
    launch. What failed here is this process's own construction, and the fix
    is whatever the reason line names.
    """
    app.engine_error = reason
    app._append_log(
        f"[red]JARVIS engine did not start:[/red] {app._escape(reason)}\n"
        "[yellow]Running without an engine.[/yellow] Chat is unavailable, but "
        "you can review confirmations ([bold]F3[/bold]) — decisions are queued "
        "and applied the next time the engine starts."
    )
    await _attach_offline_sessions(app, logger)
    await app._refresh_sidebar()
    app._update_status()


async def _attach_offline_sessions(app: Any, logger: Any) -> None:
    """Open a read-only view of session history without a full engine.

    Sessions live in the contextor binary, not a JSON file, so this spawns
    just that one sidecar — no LLM, no dispatch, no sockets. If contextor is
    itself missing, the sidebar simply says so; the confirmations panel does
    not depend on any of this.
    """
    try:
        from ..contextor.adapter import ContextorAdapter
        from ..sessions.manager import SessionManager

        contextor = ContextorAdapter(embeddings=None)
        if not contextor.connect():
            logger.info("TUI: contextor unavailable; sessions hidden in offline mode")
            return
        app._offline_sessions = SessionManager(contextor)
        app._offline_contextor = contextor
    except Exception as e:
        logger.info(f"TUI: could not open offline session view: {e}")


async def run_engine(app: Any, logger: Any) -> None:
    try:
        await app.jarvis.run()
    except asyncio.CancelledError:
        pass
    except Exception as e:  # pragma: no cover
        logger.error(f"TUI: Engine crashed: {e}", exc_info=True)
        chat_log = app.query_one("#chat-log", RichLog)
        chat_log.write(f"[red]Engine crashed: {e}[/red]")


async def on_unmount(app: Any) -> None:
    contextor = getattr(app, "_offline_contextor", None)
    if contextor is not None:
        try:
            contextor.disconnect()
        except Exception:
            pass
    if app.jarvis is not None:
        try:
            if app._output_cb is not None:
                app.jarvis.output_manager.remove_output_callback(app._output_cb)
            if app._activity_cb is not None:
                app.jarvis.output_manager.remove_activity_callback(app._activity_cb)
        except Exception:
            pass
        try:
            app.jarvis.stop()
        except Exception:
            pass
    if app._jarvis_task is not None and not app._jarvis_task.done():
        app._jarvis_task.cancel()
        try:
            await app._jarvis_task
        except (asyncio.CancelledError, Exception):
            pass
