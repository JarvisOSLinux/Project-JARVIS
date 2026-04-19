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
    Ctrl+L  — focus chat log (scroll with arrows / PgUp)
    Ctrl+I  — focus message input
    F1      — help (slash commands + keys)
    Enter   — submit message
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
      via main.py's handler; ``/help`` is handled in the TUI only (modal).
      The sidebar refreshes after every turn to stay in sync.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, RichLog, Static

from ..config import Config
from ..core.logger import get_logger
from ..sessions.model import Session
from .help_screen import HelpScreen

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
        Binding("ctrl+l", "focus_chat", "Log", show=True),
        Binding("ctrl+i", "focus_input", "Input", show=True),
        Binding("f1", "help", "Help", show=True),
    ]

    status_text: reactive[str] = reactive("starting…")

    def __init__(self) -> None:
        super().__init__()
        self.jarvis = None  # type: ignore[assignment]
        self._jarvis_task: Optional[asyncio.Task] = None
        self._output_cb = None

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
                yield Input(
                    placeholder="Message, /help, /sessions, /new, /switch <id>…",
                    id="input",
                )
        yield Static(self.status_text, id="status-bar")
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_mount(self) -> None:
        self.title = "JARVIS"
        self.sub_title = "interactive chat"

        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.write("[dim]Booting JARVIS engine…[/dim]")

        # Defer engine start off the Textual mount path so an Ollama
        # connect or contextor spawn doesn't block the first paint.
        self.run_worker(self._start_jarvis(), exclusive=True, name="jarvis-boot")

    async def _start_jarvis(self) -> None:
        """Create the Jarvis engine and start its event loop."""
        chat_log = self.query_one("#chat-log", RichLog)

        # Lazy import — avoids pulling engine deps when someone only
        # introspects the tui module.
        from ..main import Jarvis

        try:
            jarvis = Jarvis(tui_mode=True)
        except Exception as e:  # pragma: no cover - surfaced to the user
            logger.error(f"TUI: Failed to construct Jarvis: {e}", exc_info=True)
            chat_log.write(f"[red]Failed to start JARVIS: {e}[/red]")
            self._set_status(f"startup error: {e}")
            return

        self.jarvis = jarvis
        self._output_cb = self._on_jarvis_output
        jarvis.output_manager.add_output_callback(self._output_cb)

        # Kick off the engine's event loop as an async task.
        self._jarvis_task = asyncio.create_task(self._run_engine(), name="jarvis-run")

        # Wait a beat for dispatch/contextor to come up, then seed the UI.
        await asyncio.sleep(0.1)
        await self._refresh_sidebar()
        self._update_status()
        chat_log.write("[green]Ready.[/green] Type below or use Ctrl+N for a new chat.")
        self.query_one("#input", Input).focus()

    async def _run_engine(self) -> None:
        try:
            await self.jarvis.run()
        except asyncio.CancelledError:
            pass
        except Exception as e:  # pragma: no cover
            logger.error(f"TUI: Engine crashed: {e}", exc_info=True)
            chat_log = self.query_one("#chat-log", RichLog)
            chat_log.write(f"[red]Engine crashed: {e}[/red]")

    async def on_unmount(self) -> None:
        if self.jarvis is not None:
            try:
                if self._output_cb is not None:
                    self.jarvis.output_manager.remove_output_callback(self._output_cb)
            except Exception:
                pass
            try:
                self.jarvis.stop()
            except Exception:
                pass
        if self._jarvis_task is not None and not self._jarvis_task.done():
            self._jarvis_task.cancel()
            try:
                await self._jarvis_task
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------------
    # Input / output wiring
    # ------------------------------------------------------------------

    @on(Input.Submitted, "#input")
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        event.input.value = ""
        if not text:
            return

        low = text.lower()
        if low in ("/help", "/?"):
            self.push_screen(HelpScreen())
            return

        if self.jarvis is None:
            self._append_log("[yellow]JARVIS is still starting up — try again in a second.[/yellow]")
            return

        self._append_log(f"[bold cyan]you[/bold cyan] > {self._escape(text)}")

        # Inject via the same path voice and sockets use.  Slash
        # commands still work — main.py's handler catches them.
        self.jarvis.events.inject_user_input(text)

        # Refresh sidebar shortly after — covers /new, /rename, /delete,
        # /switch and auto-created sessions.
        self.set_timer(0.3, self._refresh_sidebar_sync)

    def _on_jarvis_output(self, response: Dict[str, Any]) -> None:
        """Output callback — called from the main asyncio loop by OutputManager."""
        text = response.get("output", "")
        if not text:
            return
        self._append_log(f"[bold magenta]jarvis[/bold magenta] > {self._escape(text)}")
        # Session might have been auto-created on first message; refresh.
        self.set_timer(0.05, self._refresh_sidebar_sync)

    def _append_log(self, markup: str) -> None:
        try:
            chat_log = self.query_one("#chat-log", RichLog)
        except Exception:
            return
        chat_log.write(markup)

    @staticmethod
    def _escape(text: str) -> str:
        """Escape Rich markup meta-characters in user/LLM text."""
        return text.replace("[", r"\[")

    # ------------------------------------------------------------------
    # Session sidebar
    # ------------------------------------------------------------------

    def _refresh_sidebar_sync(self) -> None:
        """set_timer can't await — schedule the async refresh."""
        asyncio.create_task(self._refresh_sidebar())

    async def _refresh_sidebar(self) -> None:
        if self.jarvis is None:
            return
        try:
            sessions: List[Session] = self.jarvis.sessions.list(limit=50)
        except Exception as e:
            logger.debug(f"TUI: session list failed: {e}")
            sessions = []

        current_id = self.jarvis.sessions.current_id
        try:
            list_view = self.query_one("#session-list", ListView)
        except Exception:
            return

        await list_view.clear()
        if not sessions:
            await list_view.append(ListItem(Label("[dim](no sessions yet)[/dim]")))
        else:
            for s in sessions:
                item = SessionItem(s, is_current=(s.id == current_id))
                if item.is_current:
                    item.add_class("-current")
                await list_view.append(item)
        self._update_status()

    @on(ListView.Selected, "#session-list")
    async def on_session_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, SessionItem) or self.jarvis is None:
            return
        if item.session_id == self.jarvis.sessions.current_id:
            return
        session = self.jarvis.sessions.switch(item.session_id)
        if session is None:
            self._append_log(f"[red]Could not switch to {item.session_id[:8]}[/red]")
            return
        self._append_log(
            f"[dim]— switched to {session.short_id()} "
            f"('{session.title or 'untitled'}') —[/dim]"
        )
        await self._refresh_sidebar()

    # ------------------------------------------------------------------
    # Actions (keybindings)
    # ------------------------------------------------------------------

    async def action_new_session(self) -> None:
        if self.jarvis is None:
            return
        if not self.jarvis.sessions.available:
            self._append_log("[yellow]Memory is disabled — sessions unavailable.[/yellow]")
            return
        session = self.jarvis.sessions.new_session()
        if session:
            self._append_log(
                f"[dim]— started new session {session.short_id()} —[/dim]"
            )
            await self._refresh_sidebar()
        else:
            self._append_log("[red]Could not create a new session.[/red]")

    def action_focus_chat(self) -> None:
        """Move focus to the transcript for keyboard scrolling (arrows / PgUp)."""
        try:
            self.query_one("#chat-log", RichLog).focus()
        except Exception:
            pass

    def action_focus_input(self) -> None:
        """Move focus back to the message line."""
        try:
            self.query_one("#input", Input).focus()
        except Exception:
            pass

    def action_help(self) -> None:
        """Open the help modal (Esc / F1 closes while help is focused)."""
        self.push_screen(HelpScreen())

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _update_status(self) -> None:
        parts = []
        if self.jarvis is not None and self.jarvis.sessions.current:
            parts.append(f"session: {self.jarvis.sessions.current.short_id()}")
        else:
            parts.append("session: (none)")

        model = getattr(Config, "LLM_MODEL", None) or "(unset)"
        provider = getattr(Config, "LLM_PROVIDER", "?")
        parts.append(f"model: {model}")
        parts.append(f"provider: {provider}")
        parts.append("Ctrl+N new · Ctrl+Q quit · Ctrl+L log · Ctrl+I input · F1 help")

        self.status_text = "  |  ".join(parts)

    def watch_status_text(self, value: str) -> None:
        try:
            self.query_one("#status-bar", Static).update(value)
        except Exception:
            pass

    def _set_status(self, text: str) -> None:
        self.status_text = text


def run_tui() -> None:
    """Entry point invoked by ``jarvis tui`` in the CLI."""
    app = JarvisTUI()
    app.run()
