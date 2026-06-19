"""TUI settings viewer — F2 opens a read-only panel of the active runtime config.

Provider configuration (model, URL, API key) is managed via /providers.
These settings cover everything else: confirmation mode, memory, RAG, etc.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label

_SETTINGS_KEYS: list[tuple[str, str]] = [
    ("CONFIRMATION_MODE", "Confirmation mode"),
    ("OUTPUT_MODE", "Output mode"),
    ("CONTEXTOR_ENABLED", "Contextor (memory)"),
    ("RAG_ENABLED", "RAG"),
    ("RAG_TOP_K", "RAG top-k"),
    ("EMBED_MODEL", "Embed model"),
    ("ALLOW_EMBEDDING_SEARCH", "Embedding tool search"),
    ("JARVIS_SUDO_ENABLED", "Sudo enabled"),
    ("RESET_HISTORY_AFTER_RESPONSE", "Reset history"),
    ("DATA_CONSENT", "Data consent"),
    ("MEMORY_RETENTION_DAYS", "Memory retention (days)"),
    ("DISPATCH_TIMEOUT", "Dispatch timeout (s)"),
    ("LOG_LEVEL", "Log level"),
]


class SettingsModal(ModalScreen[None]):
    """Read-only settings panel.  Esc or Close to dismiss."""

    BINDINGS = [
        Binding("escape", "dismiss_modal", "Close", show=True),
        Binding("f2", "dismiss_modal", "Close", show=False),
    ]

    CSS = """
    SettingsModal {
        align: center middle;
    }

    #settings-dialog {
        width: 72;
        height: auto;
        max-height: 38;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }

    #settings-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #settings-table {
        height: auto;
        max-height: 28;
        margin-bottom: 1;
    }

    #settings-footer {
        color: $text-muted;
        margin-bottom: 1;
    }

    #settings-close-row {
        height: 3;
        align: center middle;
    }
    """

    def compose(self) -> ComposeResult:
        from ..config import Config

        with Vertical(id="settings-dialog"):
            yield Label(
                "⚙  JARVIS — Active Settings  (Esc to close)",
                id="settings-title",
            )

            table = DataTable(id="settings-table", show_cursor=False)
            table.add_columns("Setting", "Value")

            for attr, label in _SETTINGS_KEYS:
                raw = getattr(Config, attr, "(not set)")
                value = str(raw)
                # Flag dangerous values so they stand out.
                if attr == "CONFIRMATION_MODE" and value == "allow_all":
                    value = f"[bold red]{value} ⚠[/bold red]"
                elif attr == "JARVIS_SUDO_ENABLED" and value.lower() == "true":
                    value = f"[bold red]{value} ⚠[/bold red]"
                table.add_row(label, value)

            yield table
            yield Label(
                "[dim]Providers: [bold]/providers[/bold]  |  "
                "Other settings: [bold]jarvis/.env[/bold][/dim]",
                id="settings-footer",
                markup=True,
            )
            with Horizontal(id="settings-close-row"):
                yield Button("Close  (Esc)", id="btn-close")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss()

    def action_dismiss_modal(self) -> None:
        self.dismiss()
