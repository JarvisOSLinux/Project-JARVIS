"""TUI confirmation modal — blocks for user approval when JARVIS needs to
execute a tool in CONFIRMATION_MODE=smart or ask_all.

In CLI / voice mode the ConfirmationManager uses desktop notifications or
stdin prompts.  In TUI mode Textual owns the terminal so those channels
are unavailable.  Instead, tui.lifecycle.start_jarvis registers
``app._tui_confirm`` as the TUI callback on the ConfirmationManager;
that callback pushes this modal onto the Textual screen stack and awaits
the user's choice via ``push_screen_wait``.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class ConfirmModal(ModalScreen[bool]):
    """Modal dialog for tool-execution confirmation.

    Dismissed with ``True`` (Allow) or ``False`` (Deny).
    """

    BINDINGS = [
        Binding("y", "allow", "Allow", show=True),
        Binding("n", "deny", "Deny", show=True),
        Binding("escape", "deny", "Deny", show=False),
    ]

    CSS = """
    ConfirmModal {
        align: center middle;
    }

    #confirm-dialog {
        width: 64;
        height: auto;
        border: round $warning;
        background: $surface;
        padding: 1 2;
    }

    #confirm-title {
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }

    #confirm-tools {
        margin-bottom: 1;
    }

    #confirm-hint {
        color: $text-muted;
        margin-bottom: 1;
    }

    #confirm-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    #btn-allow {
        margin-right: 4;
    }
    """

    def __init__(self, request_id: str, tools_detail: list) -> None:
        super().__init__()
        self._request_id = request_id
        # Each entry is a {tool_name, task, params, ...} dict; render the actual
        # command so the user sees what will run, not just the tool name (#186).
        from ..core.confirmation_manager import confirmation_line

        self._tool_lines = [confirmation_line(d) for d in tools_detail]

    def compose(self) -> ComposeResult:
        tools_text = "\n".join(f"  • {line}" for line in self._tool_lines)
        with Vertical(id="confirm-dialog"):
            yield Label("⚠  JARVIS — Confirmation Required", id="confirm-title")
            yield Static(
                f"[bold]The following will run:[/bold]\n{tools_text}",
                id="confirm-tools",
                markup=True,
            )
            yield Static(
                "[dim]Press Y / Allow to proceed, N / Deny or Esc to cancel.[/dim]",
                id="confirm-hint",
                markup=True,
            )
            with Horizontal(id="confirm-buttons"):
                yield Button("Allow  [Y]", id="btn-allow", variant="success")
                yield Button("Deny   [N]", id="btn-deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-allow")

    def action_allow(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)
