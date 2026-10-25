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
    Ctrl+L  — browse the chat's blocks: ↑/↓ move, Enter/Space fold, Esc back
    Esc     — back to the message input (Ctrl+I too, where the terminal tells it from Tab);
              on an empty input while JARVIS works, stop it (/stop)
    Ctrl+Shift+C — clear on-screen transcript (export buffer only; not memory)
    Ctrl+Shift+E — export transcript to ``JARVIS_DATA_DIR/transcripts/``
    F1      — help (keys from ``BINDINGS`` + documented slash commands)
    F4      — what JARVIS is doing: requests, running tasks, timers
    Enter   — submit message, or accept the highlighted slash-command suggestion
    Tab     — complete the ``/command`` being typed; press again to cycle
    Up/Down — walk previously sent messages (the suggestion list when it is open)
    Esc     — dismiss the slash-command suggestions
    Ctrl+W  — delete the word left of the cursor (see ``command_input``)
    Click / arrows — switch session (in sidebar)

Architecture:
    * A ``Jarvis(tui_mode=True)`` engine is created on mount and run as
      an asyncio task alongside the Textual app (same event loop).
    * User input from the ``Input`` widget is routed through
      ``jarvis.events.inject_user_input()`` — the same pipe that voice
      and the Unix socket use.
    * LLM responses are captured by registering a callback on
      ``output_manager``.  The callback runs in the main asyncio loop
      (same as Textual), so it can safely write to widgets.
    * Slash commands (``/new``, ``/switch``, …) still work the old way
      via main.py's handler; ``/help`` and ``/export`` are handled in the
      TUI only.  The sidebar refreshes after every turn to stay in sync.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

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
    Static,
)

from ..core.logger import JarvisLogger, get_logger
from ..sessions.model import Session
from . import actions as tui_actions
from . import lifecycle as tui_lifecycle
from . import output as tui_output
from . import status_bar as tui_status_bar
from .chat_view import ChatView
from .command_input import CommandDropdown, CommandInput
from .config_modal import ConfigModal, ConfigModalResult
from .confirm_modal import ConfirmModal
from .local_input import export_transcript_to_disk, handle_local_input
from .session_sidebar import on_session_selected as handle_session_selected
from .session_sidebar import refresh_sidebar
from .session_sidebar import schedule_sidebar_refresh as queue_sidebar_refresh
from .session_sidebar import show_session

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

    #chat-log > .line {
        margin: 0 0 0 0;
    }

    Block {
        height: auto;
    }

    BlockTitle {
        width: 1fr;
        height: 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
        color: $text-muted;
    }

    BlockTitle:hover {
        background: $boost;
    }

    BlockTitle:focus {
        background: $accent 30%;
        color: $text;
    }

    Block.block-command > BlockTitle {
        color: $text;
    }

    .block-body {
        height: auto;
        max-height: 16;
        padding: 0 0 0 2;
        border-left: vkey $primary 40%;
        margin: 0 0 0 1;
    }

    Block.-collapsed > .block-body {
        display: none;
    }

    #activity {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }

    #activity.-idle {
        display: none;
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
        Binding("ctrl+l", "focus_chat", "Browse", show=True),
        # Most terminals send Ctrl+I as Tab, so Esc is the key that works.
        Binding("ctrl+i", "focus_input", "Input", show=False),
        Binding("escape", "focus_input", "Back to typing", show=False),
        Binding("f1", "help", "Help", show=True, priority=True),
        Binding("f2", "settings", "Settings", show=True, priority=True),
        Binding("f3", "setups", "Setups", show=True, priority=True),
        Binding("f4", "tasks", "Tasks", show=True, priority=True),
        Binding("ctrl+shift+c", "clear_transcript", "Clear log", show=False),
        Binding("ctrl+shift+e", "export_transcript", "Export", show=False),
    ]

    status_text: reactive[str] = reactive("starting…")

    def __init__(self) -> None:
        super().__init__()
        self.jarvis = None  # type: ignore[assignment]
        self._setup_forms: set = set()
        self._jarvis_task: Optional[asyncio.Task] = None
        self._output_cb = None
        self._activity_cb = None
        self._action_cb = None
        # Action id -> kind, for the actions still running (the activity line).
        self._running_actions: Dict[str, Any] = {}
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
                yield ChatView(id="chat-log")
                yield Static("", id="activity", classes="-idle")
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

    async def _start_jarvis(self) -> None:
        """Create the Jarvis engine and start its event loop."""
        await tui_lifecycle.start_jarvis(self, logger)

    async def _run_engine(self) -> None:
        await tui_lifecycle.run_engine(self, logger)

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

        # Echoed first, TUI-only commands included, so every line typed stays
        # visible above whatever it produced. A command is the title of a block
        # that holds its output (#271).
        chat = self.query_one("#chat-log", ChatView)
        echo = f"[bold cyan]you[/bold cyan] > {tui_output.escape(text)}"
        chat.end_command()
        if text.startswith("/"):
            chat.begin_command(echo)
            self._export_lines.append(tui_output.markup_to_plain(echo))
        else:
            self._append_log(echo)

        if handle_local_input(self, text, JarvisTUI.BINDINGS):
            return

        if self.jarvis is None:
            self._append_log(
                "[yellow]JARVIS is still starting up — try again in a second.[/yellow]"
            )
            return

        # A session is named after the first thing said in it, never after a
        # command: "/switch 110" became the title of the session it switched to.
        if not text.startswith("/"):
            self._pending_autoname_text = text
        # Inject via the same path voice and sockets use.  Slash
        # commands still work — main.py's handler catches them.
        self.jarvis.events.inject_user_input(text)

    @on(OptionList.OptionSelected, "#command-dropdown")
    def on_command_suggested(self, event: OptionList.OptionSelected) -> None:
        """Mouse click on a suggestion — Enter is handled inside the input."""
        event.stop()
        command_input = self.query_one("#input", CommandInput)
        command_input.accept_command(event.option.id)
        command_input.focus()

    def _on_jarvis_output(self, response: Dict[str, Any]) -> None:
        """Output callback — called from the main asyncio loop by OutputManager."""
        tui_output.on_jarvis_output(self, response)

    def _on_jarvis_activity(self, event: Dict[str, Any]) -> None:
        """Internal runtime narrative (LLM/dispatch status), not chat content."""
        tui_output.on_jarvis_activity(self, event)

    def _on_jarvis_action(self, event: Dict[str, Any]) -> None:
        """An action JARVIS took, as a block in the chat (#271)."""
        tui_output.on_jarvis_action(self, event)

    async def _offer_setup(self, setup: Any) -> None:
        """Open the form for a pending setup (#254) without blocking the engine.

        Saving writes the values through dmcp and resumes the goal, the same
        path `jarvis setups fill` takes; closing the form leaves it pending.
        """
        from ..core.params_store import ParamsStore
        from ..core.setup_queue import form_spec
        from .setup_form import SetupFormScreen

        manifest = await self.jarvis.dispatch.get_server_manifest(setup.server_id)
        spec = form_spec(
            setup.to_dict(),
            (manifest or {}).get("config"),
            ParamsStore(setup.server_id).get(),
        )

        def done(values: Optional[Dict[str, str]]) -> None:
            task = asyncio.create_task(self._finish_setup_form(setup, values))
            self._setup_forms.add(task)
            task.add_done_callback(self._setup_forms.discard)

        self.push_screen(SetupFormScreen(spec), done)

    async def _finish_setup_form(
        self, setup: Any, values: Optional[Dict[str, str]]
    ) -> None:
        from ..core.params_store import ParamsStore

        later = (
            f"Setup of {self._escape(setup.server_id)} is still pending: "
            f"F3, or jarvis setups fill {setup.setup_id}"
        )
        if values is None or self.jarvis is None:
            self._append_log(f"[dim]{later}[/dim]")
            return
        try:
            await self.jarvis.dispatch.set_server_config(setup.server_id, values)
        except Exception as e:
            logger.warning(f"TUI: saving the setup of {setup.server_id} failed: {e}")
            self._append_log(
                f"[red]Could not save the settings: {self._escape(str(e))}[/red]"
            )
            self._append_log(f"[dim]{later}[/dim]")
            return
        secret = {f["key"] for f in setup.fields if f.get("sensitive")}
        plain = {k: v for k, v in values.items() if k not in secret}
        if plain:
            ParamsStore(setup.server_id).set_many(plain)
        self.jarvis.events.inject_setup_result(
            {"type": "setup_completed", "id": setup.setup_id}
        )

    def _open_setups(self) -> None:
        """The pending setups (F3, /setups): fill one in or cancel it."""
        from .setups_view import SetupsScreen

        jarvis = self.jarvis
        setups = jarvis.setups.list() if jarvis is not None else []

        def chosen(choice: Optional[tuple]) -> None:
            if not choice or jarvis is None:
                return
            what, setup_id = choice
            setup = jarvis.setups.find(setup_id)
            if setup is None:
                self._append_log("[dim]That setup is no longer pending.[/dim]")
            elif what == "fill":
                self.call_later(self._offer_setup, setup)
            else:
                jarvis.events.inject_setup_result(
                    {"type": "cancel_setup", "id": setup.setup_id}
                )
                self._append_log(
                    f"[dim]Cancelled the setup of {self._escape(setup.server_id)}.[/dim]"
                )

        self.push_screen(SetupsScreen(setups), chosen)

    def _append_log(self, markup: str) -> None:
        tui_output.append_log(self, markup)

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
        await refresh_sidebar(self, SessionItem, logger)

    def _on_session_switch(self, session: Any) -> None:
        show_session(self, session)

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
        """Browse the chat's blocks (or scroll it, when it has none)."""
        tui_actions.focus_chat(self)

    def action_focus_input(self) -> None:
        """Move focus back to the message line."""
        tui_actions.focus_input(self)

    def _close_if_showing(self, kind: type) -> bool:
        """F1-F4 open their window and close it again: they have priority,
        so the window's own binding for the same key never sees it."""
        if isinstance(self.screen, kind):
            self.screen.dismiss(None)
            return True
        return False

    def action_help(self) -> None:
        """Open the help modal; F1 or Esc closes it."""
        from .help_screen import HelpScreen

        if not self._close_if_showing(HelpScreen) and len(self.screen_stack) <= 1:
            tui_actions.open_help(self, JarvisTUI.BINDINGS)

    def action_clear_transcript(self) -> None:
        """Clear the RichLog and export buffer only (does not touch contextor)."""
        tui_actions.clear_transcript(self)

    def action_export_transcript(self) -> None:
        """Write ``_export_lines`` to ``JARVIS_DATA_DIR/transcripts/``."""
        tui_actions.export_transcript(self)

    def action_settings(self) -> None:
        if isinstance(self.screen, ConfigModal):
            self.screen.action_cancel()
        elif len(self.screen_stack) <= 1:
            self._open_config("settings")

    def action_setups(self) -> None:
        from .setups_view import SetupsScreen

        if not self._close_if_showing(SetupsScreen) and len(self.screen_stack) <= 1:
            self._open_setups()

    def action_stop(self) -> None:
        """Esc on an empty input: stop JARVIS's work in this session (#270).

        Like Esc in Claude Code. Does nothing while JARVIS is idle, so a
        stray Esc while typing costs nothing.
        """
        from ..runtime.stop import _session_goals

        jarvis = self.jarvis
        if jarvis is None:
            return
        thinking = any(not t.done() for t in getattr(jarvis, "_event_tasks", ()))
        if not thinking and not _session_goals(jarvis):
            return
        chat = self.query_one("#chat-log", ChatView)
        chat.end_command()
        chat.begin_command("[bold cyan]you[/bold cyan] > /stop  [dim](Esc)[/dim]")
        jarvis.events.inject_user_input("/stop")

    def action_tasks(self) -> None:
        from .tasks_view import TasksScreen

        if not self._close_if_showing(TasksScreen) and len(self.screen_stack) <= 1:
            self._open_tasks()

    def _open_tasks(self) -> None:
        """What JARVIS is doing now (F4, /tasks)."""
        from ..runtime.work import collect_work
        from .tasks_view import TasksScreen

        jarvis = self.jarvis

        async def collect() -> Dict[str, Any]:
            if jarvis is None:
                return {}
            return await collect_work(jarvis)

        async def stop_one(pid: Any) -> str:
            """x in F4: `/stop <number>`, echoed in the chat like Esc's /stop."""
            from ..runtime.session_commands import session_reply
            from ..runtime.stop import stop_task

            if jarvis is None:
                return "JARVIS is still starting up."
            reply = await stop_task(jarvis, logger, pid)
            chat = self.query_one("#chat-log", ChatView)
            chat.end_command()
            chat.begin_command(
                f"[bold cyan]you[/bold cyan] > /stop {pid}  [dim](F4)[/dim]"
            )
            session_reply(jarvis, reply)
            return reply

        self.push_screen(TasksScreen(collect, stop_one))

    async def action_quit(self) -> None:
        """Quit, asking first when quitting would stop work that is running."""
        from ..runtime.lifecycle import force_stop, pending_work
        from .quit_modal import QuitConfirmModal

        if isinstance(self.screen, QuitConfirmModal):
            # Asked already: a second Ctrl+Q that reaches the app confirms.
            self.screen.dismiss(True)
            return

        jarvis = self.jarvis
        pending = await pending_work(jarvis) if jarvis is not None else []
        if not pending:
            self.exit()
            return

        async def quit_anyway() -> None:
            await force_stop(jarvis, logger)
            self.exit()

        def decided(confirmed: Optional[bool]) -> None:
            if confirmed:
                self.call_later(quit_anyway)

        self.push_screen(QuitConfirmModal(pending), decided)

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
                if applied:
                    self._append_log(
                        f"[green]Settings updated: {', '.join(applied)}[/green]"
                    )
            if result.providers_changed:
                self._append_log("[green]Provider config updated.[/green]")

        self.push_screen(ConfigModal(initial_tab=tab), _on_config)

    async def _tui_confirm(self, request_id: str, tools_detail: list) -> bool:
        return await self.push_screen_wait(ConfirmModal(request_id, tools_detail))

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
