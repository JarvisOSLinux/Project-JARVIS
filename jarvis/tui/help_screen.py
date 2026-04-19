"""Modal help for the JARVIS Textual TUI (keybindings + session slash-commands)."""

from __future__ import annotations

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static

# Shown inside the modal. Keep in sync with jarvis/main.py _handle_slash_command.
HELP_MARKDOWN = """\
# JARVIS — help

## Keyboard

| Key | Action |
| --- | --- |
| **Enter** | Send the message in the input line |
| **Ctrl+N** | New chat session |
| **Ctrl+Q** | Quit |
| **Ctrl+L** | Focus transcript (scroll with arrows / PgUp / PgDn) |
| **Ctrl+I** | Focus message input |
| **F1** | Open or close this help |
| **Esc** | Close this help (when help has focus) |

Click a session in the sidebar or use **/switch** to change the active session.

## Slash commands (input line)

Handled locally (no LLM). Unknown `/…` text is still sent to the model.

| Command | Meaning |
| --- | --- |
| `/help` or `/?` | Open this help (TUI only) |
| `/new` | New session; optional title: `/new My title` |
| `/sessions` | List sessions (current marked with `*`) |
| `/switch` *id* | Switch session by short id prefix |
| `/rename` *title* | Rename the **current** session |
| `/delete` *id* | Delete session by **unique** id prefix |
"""


class HelpScreen(ModalScreen[None]):
    """Centered modal; dismiss with Esc or F1."""

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
    #help-md {
        height: 1fr;
        margin: 0 1 0 1;
    }
    #help-hint {
        height: 1;
        padding: 0 1 1 1;
        color: $text-muted;
        text-align: center;
    }
    """

    def compose(self):
        with Vertical(id="help-panel"):
            yield Markdown(HELP_MARKDOWN, id="help-md")
            yield Static("Esc  ·  F1  —  close", id="help-hint")

    def action_dismiss(self) -> None:
        self.dismiss(None)
