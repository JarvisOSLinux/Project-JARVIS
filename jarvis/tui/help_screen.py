"""Modal help for the JARVIS Textual TUI (keybindings + session slash-commands)."""

from __future__ import annotations

from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static


class HelpScreen(ModalScreen[None]):
    """Centered modal; dismiss with Esc or F1.

    ``body_markdown`` is usually produced by
    ``slash_commands_doc.build_help_markdown(JarvisTUI.BINDINGS)`` so the key
    table tracks ``BINDINGS``; session slash rows still live in
    ``slash_commands_doc.SESSION_SLASH_HELP`` (must match ``main.py``).
    """

    BINDINGS = [
        Binding("escape", "dismiss", "", show=False),
        Binding("f1", "dismiss", "", show=False),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-panel {
        width: 78;
        max-width: 95%;
        height: 70%;
        max-height: 28;
        border: tall $primary;
        background: $surface;
    }
    #help-scroll {
        height: 1fr;
        margin: 0 1;
        overflow-y: auto;
        border: round $surface-lighten-2;
    }
    #help-md {
        width: 1fr;
    }
    #help-hint {
        height: 1;
        padding: 0 1 1 1;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, body_markdown: str, *, name: str | None = None, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._body_markdown = body_markdown

    def compose(self):
        with Vertical(id="help-panel"):
            with VerticalScroll(id="help-scroll"):
                yield Markdown(self._body_markdown, id="help-md")
            yield Static("Esc  ·  F1  —  close", id="help-hint")

    def on_mount(self) -> None:
        # Focus the scroll container so arrows / PgUp / PgDn work immediately.
        self.query_one("#help-scroll", VerticalScroll).focus()

    def action_dismiss(self) -> None:
        self.dismiss(None)
