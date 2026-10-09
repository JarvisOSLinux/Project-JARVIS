"""TUI lifecycle: connect to the daemon, and stop cleanly."""

from __future__ import annotations

from typing import Any

from textual.widgets import Input

from . import messages
from .daemon_client import DaemonClient, start_daemon

CAPABILITIES = ("config_form", "confirmations")


def on_mount(app: Any) -> None:
    app.title = "JARVIS"
    app.sub_title = "interactive chat"
    app._append_log("[dim]Looking for the JARVIS daemon...[/dim]")

    app.client = DaemonClient(
        lambda msg: messages.on_message(app, msg),
        lambda up, why: messages.on_connection(app, up, why),
        capabilities=CAPABILITIES,
    )
    app.run_worker(app.client.run(), name="daemon-client")
    app.query_one("#input", Input).focus()


async def start_daemon_command(app: Any) -> None:
    """``/start``: launch the daemon, then attach as soon as it is up.

    UI chrome, not conversation: it is never sent to the daemon or recorded.
    """
    if app.connected:
        app._append_log("[dim]The daemon is already running.[/dim]")
        return
    app._append_log("[dim]Starting the daemon...[/dim]")
    ok, detail = await start_daemon()
    if not ok:
        app._append_log(
            f"[red]Could not start the daemon:[/red]\n{app._escape(detail)}"
        )
        return
    app.client.nudge()


async def on_unmount(app: Any) -> None:
    if app.client is not None:
        app.client.stop()
    await app.offline.close()
