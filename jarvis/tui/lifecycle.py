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
        app._append_log(f"[red]Failed to start JARVIS: {e}[/red]")
        app._set_status(f"startup error: {e}")
        return

    app.jarvis = jarvis
    app._output_cb = app._on_jarvis_output
    app._activity_cb = app._on_jarvis_activity
    jarvis.output_manager.add_output_callback(app._output_cb)
    jarvis.output_manager.add_activity_callback(app._activity_cb)

    # Kick off the engine's event loop as an async task.
    app._jarvis_task = asyncio.create_task(app._run_engine(), name="jarvis-run")

    # Wait a beat for dispatch/contextor to come up, then seed the UI.
    await asyncio.sleep(0.1)
    await app._refresh_sidebar()
    app._update_status()
    app._append_log("[green]Ready.[/green] Type below or use Ctrl+N for a new chat.")
    app.query_one("#input", Input).focus()


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
