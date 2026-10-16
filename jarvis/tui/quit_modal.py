"""Ask before quitting while work is still running."""

from __future__ import annotations

from typing import List

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class QuitConfirmModal(ModalScreen[bool]):
    """Lists what quitting would stop. Dismissed with True to quit anyway.

    Ctrl+Q again also confirms, the same as a second Ctrl+C in `jarvis chat`.
    """

    BINDINGS = [
        Binding("escape", "stay", "Stay", show=False),
        Binding("ctrl+q", "quit_anyway", "Quit anyway", show=False),
    ]

    CSS = """
    QuitConfirmModal {
        align: center middle;
    }

    #quit-dialog {
        width: 76;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        border: round $warning;
        background: $surface;
        padding: 1 2;
    }

    #quit-title {
        text-style: bold;
        color: $warning;
    }

    #quit-list {
        height: auto;
        max-height: 14;
        margin: 1 0;
        border: tall $panel;
    }

    #quit-buttons {
        height: 3;
        align: right middle;
    }

    #btn-stay {
        margin-right: 2;
    }
    """

    def __init__(self, pending: List[str]) -> None:
        super().__init__()
        self._pending = pending

    def compose(self) -> ComposeResult:
        with Vertical(id="quit-dialog"):
            yield Label("Quit JARVIS?", id="quit-title")
            yield Static("These are still running. Quitting stops them now:")
            with VerticalScroll(id="quit-list"):
                for line in self._pending:
                    # Server, tool and goal text are not ours: no markup.
                    yield Static(f"• {line}", markup=False)
            with Horizontal(id="quit-buttons"):
                yield Button("Stay", id="btn-stay", variant="default")
                yield Button("Quit anyway", id="btn-quit", variant="error")

    def on_mount(self) -> None:
        self.query_one("#btn-stay", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-quit")

    def action_stay(self) -> None:
        self.dismiss(False)

    def action_quit_anyway(self) -> None:
        self.dismiss(True)
