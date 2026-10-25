"""Modal help for the JARVIS Textual TUI (keybindings + session slash-commands)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Markdown, Static

from .slash_commands_doc import build_help_markdown


class HelpScreen(ModalScreen[None]):
    """Centered modal with a search line; dismiss with Esc or F1.

    The text is ``slash_commands_doc.build_help_markdown(bindings, query)``, so
    the key table tracks ``BINDINGS``; session slash rows still live in
    ``slash_commands_doc.SESSION_SLASH_HELP`` (must match ``main.py``). Typing
    keeps the rows that mention every word typed; ``query`` starts it
    (``/help stop``).
    """

    # The search line has the focus; the arrows and pages scroll the help.
    BINDINGS = [
        Binding("escape", "clear_or_close", "", show=False),
        Binding("f1", "dismiss", "", show=False),
        Binding("up", "scroll(-1)", "", show=False),
        Binding("down", "scroll(1)", "", show=False),
        Binding("pageup", "scroll(-10)", "", show=False),
        Binding("pagedown", "scroll(10)", "", show=False),
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
    #help-search {
        margin: 0 1;
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
    /* One line of text: with height 1, a bottom padding left it none. */
    #help-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(
        self,
        bindings: Sequence[Any],
        query: str = "",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._keys = bindings
        self._query = query.strip()

    def compose(self):
        with Vertical(id="help-panel"):
            yield Input(
                value=self._query,
                placeholder="Search keys and commands…",
                id="help-search",
            )
            with VerticalScroll(id="help-scroll"):
                yield Markdown(
                    build_help_markdown(self._keys, self._query), id="help-md"
                )
            yield Static(
                "Type to search  ·  Esc clears, then closes  ·  F1 closes",
                id="help-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#help-search", Input).focus()

    async def on_input_changed(self, event: Input.Changed) -> None:
        self._query = event.value.strip()
        markdown = self.query_one("#help-md", Markdown)
        await markdown.update(build_help_markdown(self._keys, self._query))
        self.query_one("#help-scroll", VerticalScroll).scroll_home(animate=False)

    def action_scroll(self, lines: int) -> None:
        scroll = self.query_one("#help-scroll", VerticalScroll)
        scroll.scroll_relative(y=lines, animate=False)

    def action_clear_or_close(self) -> None:
        search = self.query_one("#help-search", Input)
        if search.value:
            search.value = ""
        else:
            self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)
