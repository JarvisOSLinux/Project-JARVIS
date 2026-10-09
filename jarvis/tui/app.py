"""
Textual TUI app for JARVIS.

Layout:

    ┌─ JARVIS ──────────────────────────────────────────┐
    │ Sessions         │ Chat                           │
    │ ──────────────── │ ─────────────────────────────  │
    │ > a1b2  Bug fix  │ you> …                         │
    │   c3d4  Refactor │ jarvis> …                      │
    │                  │                                │
    │                  │ ─────────────────────────────  │
    │                  │ ❯ …                            │
    ├───────────────────────────────────────────────────┤
    │ ● model: qwen3:4b   session: a1b2   Ctrl+Q quit   │
    └───────────────────────────────────────────────────┘

Keybindings:
    Ctrl+N  — new session
    Ctrl+Q  — quit
    Ctrl+,  — settings (read-only config viewer)
    Ctrl+L  — focus chat log (scroll with arrows / PgUp)
    Ctrl+I  — focus message input
    Ctrl+Shift+C — clear on-screen transcript (export buffer only; not memory)
    Ctrl+Shift+E — export transcript to ``JARVIS_DATA_DIR/transcripts/``
    F1      — help (keys from ``BINDINGS`` + documented slash commands)
    Enter   — submit message, or accept the highlighted slash-command suggestion
    Tab     — complete the ``/command`` being typed; press again to cycle
    Up/Down — walk previously sent messages (the suggestion list when it is open)
    Esc     — dismiss the slash-command suggestions
    Ctrl+W  — delete the word left of the cursor (see ``command_input``)
    Click / arrows — switch session (in sidebar)

Architecture:
    * The TUI is a client of the daemon (#236). ``tui.daemon_client`` keeps one
      connection to its GUI socket and retries every two seconds, so a daemon
      started elsewhere is picked up; ``/start`` launches one from here.
    * Everything shown arrives as a protocol message (``tui.messages``). With
      no daemon the sidebar and pending confirmations come from disk
      (``tui.offline``) and the status bar says so.
    * User input goes to the daemon as ``{"type": "message"}``, the same pipe
      the desktop app uses; slash commands such as ``/new`` are handled there.
      ``/help``, ``/export``, ``/start`` and the settings commands are handled
      in the TUI only.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    OptionList,
    RichLog,
    Static,
)

from ..core.logger import JarvisLogger, get_logger
from ..sessions.model import Session
from . import actions as tui_actions
from . import lifecycle as tui_lifecycle
from . import output as tui_output
from . import status_bar as tui_status_bar
from .command_input import CommandDropdown, CommandInput
from .config_modal import ConfigModal, ConfigModalResult
from .daemon_client import DaemonClient
from .local_input import export_transcript_to_disk, handle_local_input
from .offline import OfflineStore
from .session_sidebar import on_session_selected as handle_session_selected
from .session_sidebar import refresh_sidebar
from .session_sidebar import schedule_sidebar_refresh as queue_sidebar_refresh

logger = get_logger(__name__)


class SessionItem(ListItem):
    """A row in the session sidebar.  Carries the session id."""

    def __init__(self, session: Session, is_current: bool = False) -> None:
        marker = "●" if is_current else " "
        label = f"{marker} {session.short_id()}  {session.display_label()}"
        super().__init__(Label(label))
        self.session_id: str = session.id
        self.is_current: bool = is_current


class JarvisTUI(App):
    """Textual app — the OpenClaw-style chat UI for JARVIS."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
    }

    #sidebar {
        width: 32;
        border-right: solid $primary;
        padding: 0 1;
    }

    #sidebar-title {
        color: $accent;
        text-style: bold;
        padding: 0 0 1 0;
    }

    #session-list {
        height: 1fr;
    }

    #chat-pane {
        padding: 0 1;
        layers: base dropdown;
    }

    #chat-log {
        height: 1fr;
        border: round $primary 30%;
        padding: 0 1;
    }

    #input {
        dock: bottom;
        margin: 1 0 0 0;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }

    SessionItem {
        padding: 0 1;
    }
    SessionItem.-current {
        background: $boost;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("ctrl+n", "new_session", "New", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+d", "delete_selected_session", "Delete", show=True),
        Binding("ctrl+l", "focus_chat", "Log", show=True),
        Binding("ctrl+i", "focus_input", "Input", show=True),
        Binding("f1", "help", "Help", show=True, priority=True),
        Binding("f2", "settings", "Settings", show=True, priority=True),
        Binding("ctrl+shift+c", "clear_transcript", "Clear log", show=False),
        Binding("ctrl+shift+e", "export_transcript", "Export", show=False),
    ]

    status_text: reactive[str] = reactive("starting…")

    def __init__(self) -> None:
        super().__init__()
        self.client: Optional[DaemonClient] = None
        self.connected = False
        self.offline_reason = "connecting…"
        self.offline = OfflineStore()
        # Last "status" message from the daemon, and its voice/response state.
        self.daemon_status: Dict[str, Any] = {}
        self.daemon_state = "idle"
        self.sessions: List[Dict[str, Any]] = []
        self.current_session_id: Optional[str] = None
        self.pending_confirmations: List[Dict[str, Any]] = []
        # Whether the transcript holds conversation (not just notices).
        self.has_conversation = False
        # request id -> the ConfirmModal showing it, so a request resolved
        # elsewhere can close its modal.
        self.confirm_screens: Dict[str, Any] = {}
        # Plain lines mirroring the RichLog (for Markdown export).
        self._export_lines: list[str] = []
        # Sidebar refresh can be requested from multiple timers/callbacks.
        self._sidebar_refresh_lock = asyncio.Lock()
        # Ctrl+D is a two-step confirmation keyed by session id.
        self._pending_delete_session_id: Optional[str] = None
        # First user message in a session — used to auto-name default titles.
        self._pending_autoname_text: Optional[str] = None

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("Sessions  (Ctrl+N new)", id="sidebar-title")
                yield ListView(id="session-list")
            with Vertical(id="chat-pane"):
                yield RichLog(id="chat-log", markup=True, wrap=True, highlight=True)
                yield CommandDropdown(id="command-dropdown")
                yield CommandInput(
                    placeholder="Message, /help, /export, /sessions, /new…",
                    id="input",
                )
        yield Static(self.status_text, id="status-bar")
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_mount(self) -> None:
        tui_lifecycle.on_mount(self)

    async def on_unmount(self) -> None:
        await tui_lifecycle.on_unmount(self)

    # ------------------------------------------------------------------
    # Input / output wiring
    # ------------------------------------------------------------------

    @on(Input.Submitted, "#input")
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        event.input.value = ""
        if not text:
            return

        if handle_local_input(self, text, JarvisTUI.BINDINGS):
            return

        if not self.connected:
            self._append_log(
                f"[yellow]{self.offline_reason or 'not connected'}[/yellow]"
            )
            return

        self.has_conversation = True
        self._append_log(f"[bold cyan]you[/bold cyan] > {tui_output.escape(text)}")
        self._pending_autoname_text = text
        await self.client.send({"type": "message", "content": text})

    @on(OptionList.OptionSelected, "#command-dropdown")
    def on_command_suggested(self, event: OptionList.OptionSelected) -> None:
        """Mouse click on a suggestion — Enter is handled inside the input."""
        event.stop()
        command_input = self.query_one("#input", CommandInput)
        command_input.accept_command(event.option.id)
        command_input.focus()

    def _append_log(self, markup: str) -> None:
        tui_output.append_log(self, markup)

    def clear_log(self) -> None:
        """Empty the on-screen transcript and its export buffer."""
        try:
            self.query_one("#chat-log", RichLog).clear()
        except Exception:
            return
        self._export_lines.clear()

    @staticmethod
    def _escape(text: str) -> str:
        """Escape Rich markup meta-characters in user/LLM text."""
        return tui_output.escape(text)

    # ------------------------------------------------------------------
    # Session sidebar
    # ------------------------------------------------------------------

    def schedule_sidebar_refresh(self) -> None:
        """Rebuild the session list on Textual's message pump.

        ``Jarvis.run()`` runs in a separate asyncio task; scheduling DOM work
        with a bare ``asyncio.create_task`` from that stack can leave the
        ListView stale until the next UI event (e.g. focusing the sidebar).
        """

        queue_sidebar_refresh(self)

    async def _refresh_sidebar(self) -> None:
        await refresh_sidebar(self, SessionItem)

    @on(ListView.Selected, "#session-list")
    async def on_session_selected(self, event: ListView.Selected) -> None:
        await handle_session_selected(self, event)

    # ------------------------------------------------------------------
    # Actions (keybindings) — see ``tui_actions``
    # ------------------------------------------------------------------

    async def action_new_session(self) -> None:
        await tui_actions.new_session(self)

    async def action_delete_selected_session(self) -> None:
        """Delete the highlighted (or current) session with two-step confirm."""
        await tui_actions.delete_selected_session(self)

    def action_focus_chat(self) -> None:
        """Move focus to the transcript for keyboard scrolling (arrows / PgUp)."""
        tui_actions.focus_chat(self)

    def action_focus_input(self) -> None:
        """Move focus back to the message line."""
        tui_actions.focus_input(self)

    def action_help(self) -> None:
        """Open the help modal (Esc / F1 closes while help is focused)."""
        if len(self.screen_stack) <= 1:
            tui_actions.open_help(self, JarvisTUI.BINDINGS)

    def action_clear_transcript(self) -> None:
        """Clear the RichLog and export buffer only (does not touch contextor)."""
        tui_actions.clear_transcript(self)

    def action_export_transcript(self) -> None:
        """Write ``_export_lines`` to ``JARVIS_DATA_DIR/transcripts/``."""
        tui_actions.export_transcript(self)

    def action_settings(self) -> None:
        if len(self.screen_stack) <= 1:
            self._open_config("settings")

    def _open_config(self, tab: str = "settings") -> None:
        from .local_input import _apply_in_memory

        def _on_config(result: ConfigModalResult) -> None:
            if result.settings_changes:
                from ..cli import _update_env_setting

                applied = []
                for key, value in result.settings_changes.items():
                    try:
                        _update_env_setting(key, value)
                        _apply_in_memory(key, value)
                        applied.append(f"{key}={value}")
                    except Exception as e:
                        self._append_log(f"[red]Error setting {key}: {e}[/red]")
                    if key == "CONFIRMATION_MODE" and self.connected:
                        asyncio.ensure_future(
                            self.client.send(
                                {"type": "set_confirmation_mode", "mode": value}
                            )
                        )
                if applied:
                    self._append_log(
                        f"[green]Settings updated: {', '.join(applied)}[/green] "
                        "[dim](the daemon applies all but the confirmation mode "
                        "at its next start)[/dim]"
                    )
            if result.providers_changed:
                self._append_log("[green]Provider config updated.[/green]")

        self.push_screen(ConfigModal(initial_tab=tab), _on_config)

    def _export_transcript_to_disk(self, filename: Optional[str]) -> None:
        """Save plain transcript as Markdown under ``JARVIS_DATA_DIR/transcripts``."""
        export_transcript_to_disk(self, filename)

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _update_status(self) -> None:
        tui_status_bar.update_status(self)

    def watch_status_text(self, value: str) -> None:
        tui_status_bar.watch_status_text(self, value)

    def _set_status(self, text: str) -> None:
        tui_status_bar.set_status(self, text)


def run_tui() -> None:
    """Entry point invoked by ``jarvis tui`` in the CLI."""
    # Before Textual paints, detach stdio handlers from the root logger so
    # third-party INFO lines cannot corrupt the alternate screen.
    JarvisLogger.apply_tui_root_mitigation()
    app = JarvisTUI()
    app.run()
