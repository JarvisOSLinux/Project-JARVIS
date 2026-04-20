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
    Ctrl+Shift+C — clear on-screen transcript (export buffer only; not memory)
    Ctrl+Shift+E — export transcript to ``JARVIS_DATA_DIR/transcripts/``
    F1      — help (keys from ``BINDINGS`` + documented slash commands)
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
      via main.py's handler; ``/help`` and ``/export`` are handled in the
      TUI only.  The sidebar refreshes after every turn to stay in sync.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.text import Text

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, RichLog, Static

from ..config import Config
from ..core.logger import JarvisLogger, get_logger
from ..sessions.model import Session
from .help_screen import HelpScreen
from .slash_commands_doc import build_help_markdown

logger = get_logger(__name__)


def _markup_to_plain(markup: str) -> str:
    """Strip Rich markup for export / plain transcript buffer."""
    try:
        return Text.from_markup(markup, emoji=False).plain
    except Exception:
        return markup


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
        Binding("ctrl+d", "delete_selected_session", "Delete", show=True),
        Binding("ctrl+l", "focus_chat", "Log", show=True),
        Binding("ctrl+i", "focus_input", "Input", show=True),
        Binding("f1", "help", "Help", show=True),
        Binding("ctrl+shift+c", "clear_transcript", "Clear log", show=False),
        Binding("ctrl+shift+e", "export_transcript", "Export", show=False),
    ]

    status_text: reactive[str] = reactive("starting…")

    def __init__(self) -> None:
        super().__init__()
        self.jarvis = None  # type: ignore[assignment]
        self._jarvis_task: Optional[asyncio.Task] = None
        self._output_cb = None
        # Plain lines mirroring the RichLog (for Markdown export).
        self._export_lines: list[str] = []
        # Sidebar refresh can be requested from multiple timers/callbacks.
        self._sidebar_refresh_lock = asyncio.Lock()
        # Ctrl+D is a two-step confirmation keyed by session id.
        self._pending_delete_session_id: Optional[str] = None

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
                    placeholder="Message, /help, /export, /sessions, /new…",
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

        self._append_log("[dim]Booting JARVIS engine…[/dim]")

        # Defer engine start off the Textual mount path so an Ollama
        # connect or contextor spawn doesn't block the first paint.
        self.run_worker(self._start_jarvis(), exclusive=True, name="jarvis-boot")

    async def _start_jarvis(self) -> None:
        """Create the Jarvis engine and start its event loop."""
        # Lazy import — avoids pulling engine deps when someone only
        # introspects the tui module.
        from ..main import Jarvis

        try:
            jarvis = Jarvis(tui_mode=True)
        except Exception as e:  # pragma: no cover - surfaced to the user
            logger.error(f"TUI: Failed to construct Jarvis: {e}", exc_info=True)
            self._append_log(f"[red]Failed to start JARVIS: {e}[/red]")
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
        self._append_log(
            "[green]Ready.[/green] Type below or use Ctrl+N for a new chat."
        )
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
            self.push_screen(HelpScreen(build_help_markdown(JarvisTUI.BINDINGS)))
            return

        if low == "/export" or low.startswith("/export "):
            parts = text.split(maxsplit=1)
            name_arg = parts[1].strip() if len(parts) > 1 else None
            self._export_transcript_to_disk(name_arg)
            return

        if self.jarvis is None:
            self._append_log("[yellow]JARVIS is still starting up — try again in a second.[/yellow]")
            return

        self._append_log(f"[bold cyan]you[/bold cyan] > {self._escape(text)}")

        # Inject via the same path voice and sockets use.  Slash
        # commands still work — main.py's handler catches them.
        self.jarvis.events.inject_user_input(text)

    def _on_jarvis_output(self, response: Dict[str, Any]) -> None:
        """Output callback — called from the main asyncio loop by OutputManager."""
        text = response.get("output", "")
        if not text:
            return
        self._append_log(f"[bold magenta]jarvis[/bold magenta] > {self._escape(text)}")
        # Session might have been auto-created on first message; refresh.
        self.schedule_sidebar_refresh()

    def _append_log(self, markup: str) -> None:
        try:
            chat_log = self.query_one("#chat-log", RichLog)
        except Exception:
            return
        chat_log.write(markup)
        self._export_lines.append(_markup_to_plain(markup))

    @staticmethod
    def _escape(text: str) -> str:
        """Escape Rich markup meta-characters in user/LLM text."""
        return text.replace("[", r"\[")

    # ------------------------------------------------------------------
    # Session sidebar
    # ------------------------------------------------------------------

    def schedule_sidebar_refresh(self) -> None:
        """Rebuild the session list on Textual's message pump.

        ``Jarvis.run()`` runs in a separate asyncio task; scheduling DOM work
        with a bare ``asyncio.create_task`` from that stack can leave the
        ListView stale until the next UI event (e.g. focusing the sidebar).
        """

        def kick() -> None:
            asyncio.create_task(self._refresh_sidebar())

        try:
            self.call_next(kick)
        except Exception:
            asyncio.create_task(self._refresh_sidebar())

    async def _refresh_sidebar(self) -> None:
        async with self._sidebar_refresh_lock:
            if self.jarvis is None:
                return
            try:
                sessions: List[Session] = self.jarvis.sessions.list(limit=50)
            except Exception as e:
                logger.debug(f"TUI: session list failed: {e}")
                sessions = []

            # Guard against duplicates in fast concurrent refresh bursts and
            # any backend-level duplicate rows.
            seen_ids: set[str] = set()
            unique_sessions: List[Session] = []
            for s in sessions:
                if s.id in seen_ids:
                    continue
                seen_ids.add(s.id)
                unique_sessions.append(s)
            sessions = unique_sessions

            current_id = self.jarvis.sessions.current_id
            if (
                self._pending_delete_session_id is not None
                and self._pending_delete_session_id not in seen_ids
            ):
                self._pending_delete_session_id = None
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
        self._pending_delete_session_id = None
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
            self._pending_delete_session_id = None
            self._append_log(
                f"[dim]— started new session {session.short_id()} —[/dim]"
            )
            await self._refresh_sidebar()
        else:
            self._append_log("[red]Could not create a new session.[/red]")

    async def action_delete_selected_session(self) -> None:
        """Delete the highlighted (or current) session with two-step confirm."""
        if self.jarvis is None:
            return
        if not self.jarvis.sessions.available:
            self._append_log("[yellow]Memory is disabled — sessions unavailable.[/yellow]")
            return
        target = self._get_delete_target_session()
        if target is None:
            self._append_log("[yellow]No session selected to delete.[/yellow]")
            return

        sid = target.id
        if self._pending_delete_session_id != sid:
            self._pending_delete_session_id = sid
            self._append_log(
                f"[yellow]Press Ctrl+D again to delete {target.short_id()} "
                f"('{target.title or 'untitled'}').[/yellow]"
            )
            return

        self._pending_delete_session_id = None
        if self.jarvis.sessions.delete(sid):
            self._append_log(f"[dim]— deleted session {target.short_id()} —[/dim]")
            await self._refresh_sidebar()
        else:
            self._append_log(f"[red]Delete failed for {target.short_id()}.[/red]")

    def _get_delete_target_session(self) -> Optional[Session]:
        """Prefer highlighted sidebar session; fall back to current session."""
        try:
            list_view = self.query_one("#session-list", ListView)
            highlighted = list_view.highlighted_child
        except Exception:
            highlighted = None

        if isinstance(highlighted, SessionItem) and self.jarvis is not None:
            sessions = self.jarvis.sessions.list(limit=500)
            for s in sessions:
                if s.id == highlighted.session_id:
                    return s
            return None
        if self.jarvis is not None:
            return self.jarvis.sessions.current
        return None

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
        self.push_screen(HelpScreen(build_help_markdown(JarvisTUI.BINDINGS)))

    def action_clear_transcript(self) -> None:
        """Clear the RichLog and export buffer only (does not touch contextor)."""
        try:
            log = self.query_one("#chat-log", RichLog)
            log.clear()
        except Exception:
            return
        self._export_lines.clear()
        self._append_log(
            "[dim]Transcript cleared (on-screen only; session memory unchanged.)[/dim]"
        )

    def action_export_transcript(self) -> None:
        """Write ``_export_lines`` to ``JARVIS_DATA_DIR/transcripts/``."""
        self._export_transcript_to_disk(None)

    def _export_transcript_to_disk(self, filename: Optional[str]) -> None:
        """Save plain transcript as Markdown under ``JARVIS_DATA_DIR/transcripts``."""
        root = Path(Config.JARVIS_DATA_DIR).expanduser().resolve()
        out_dir = root / "transcripts"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._append_log(f"[red]Export failed (mkdir): {e}[/red]")
            return

        if filename:
            base = os.path.basename(filename.strip())
            if not base or base in (".", ".."):
                self._append_log("[red]Export: invalid filename.[/red]")
                return
            if not base.lower().endswith(".md"):
                base = f"{base}.md"
        else:
            sid = "no-session"
            if self.jarvis is not None and self.jarvis.sessions.current:
                sid = self.jarvis.sessions.current.short_id()
            base = f"{sid}_{int(time.time())}.md"

        out_resolved = out_dir.resolve()
        path = (out_resolved / base).resolve()
        try:
            path.relative_to(out_resolved)
        except ValueError:
            self._append_log("[red]Export: path must stay under transcripts/.[/red]")
            return

        sid2 = "none"
        model = getattr(Config, "LLM_MODEL", None) or "(unset)"
        if self.jarvis is not None and self.jarvis.sessions.current:
            sid2 = self.jarvis.sessions.current.id

        header = "\n".join(
            [
                "# JARVIS transcript export",
                "",
                f"- Exported (UTC): {datetime.now(timezone.utc).isoformat()}",
                f"- Session id: `{sid2}`",
                f"- Model: `{model}`",
                "",
                "---",
                "",
            ]
        )
        body_lines = self._export_lines if self._export_lines else ["_(no transcript lines yet)_"]
        body = "\n".join(body_lines)
        text_out = header + body + "\n"

        try:
            path.write_text(text_out, encoding="utf-8")
        except OSError as e:
            self._append_log(f"[red]Export failed: {e}[/red]")
            return

        self._append_log(f"[green]Exported transcript to[/green] {path}")

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
        parts.append(
            "Ctrl+N new · Ctrl+D delete · Ctrl+Q quit · Ctrl+L log · Ctrl+I input · F1 help · "
            "Ctrl+Shift+C clear · Ctrl+Shift+E export"
        )

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
    # Before Textual paints, detach stdio handlers from the root logger so
    # third-party INFO lines cannot corrupt the alternate screen.
    JarvisLogger.apply_tui_root_mitigation()
    app = JarvisTUI()
    app.run()
