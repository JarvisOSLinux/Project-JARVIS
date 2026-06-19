"""Provider add/edit modal — triggered by /providers add (no flags) and /providers edit <name>."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static


@dataclass
class ProviderModalResult:
    confirmed: bool
    ptype: str = ""
    model: str = ""
    name: str = ""
    url: str = ""
    api_key: str = ""
    temperature: Optional[float] = None


_OLLAMA_DEFAULT_URL = "http://localhost:11434"


class ProviderModal(ModalScreen[ProviderModalResult]):
    """Modal form for adding or editing a provider.

    Dismissed with a ProviderModalResult.
    mode="add"  → all fields blank/defaulted
    mode="edit" → fields pre-filled from existing provider dict
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+s", "confirm", "Save", show=True),
    ]

    CSS = """
    ProviderModal {
        align: center middle;
    }

    #provider-dialog {
        width: 60;
        height: auto;
        max-height: 90vh;
        border: round $primary;
        background: $surface;
        padding: 0;
    }

    #form-scroll {
        padding: 1 2 1 2;
    }

    #dialog-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }

    .field-label {
        text-style: bold;
        margin-top: 1;
    }

    #type-row {
        height: 3;
        margin-bottom: 0;
    }

    #key-row {
        height: 3;
    }

    #key-row Input {
        width: 1fr;
    }

    #btn-toggle-key {
        width: 8;
        min-width: 8;
    }

    Select {
        width: 1fr;
    }

    #error-label {
        color: $error;
        margin-top: 1;
        display: none;
    }

    #error-label.visible {
        display: block;
    }

    #footer {
        height: 3;
        align: right middle;
        margin-top: 1;
        padding: 0 2;
    }

    #btn-cancel {
        margin-right: 2;
    }
    """

    def __init__(
        self,
        mode: str = "add",
        existing: Optional[dict] = None,
    ) -> None:
        super().__init__()
        self._mode = mode
        self._existing = existing or {}
        self._ptype = self._existing.get("type", "ollama")
        self._key_revealed = False

    def compose(self) -> ComposeResult:
        ex = self._existing
        title = (
            "Add Provider"
            if self._mode == "add"
            else f"Edit Provider: {ex.get('name', '')}"
        )
        default_url = ex.get(
            "url", _OLLAMA_DEFAULT_URL if self._ptype == "ollama" else ""
        )

        with Vertical(id="provider-dialog"):
            with VerticalScroll(id="form-scroll"):
                yield Label(title, id="dialog-title")

                yield Label("Type", classes="field-label")
                with Horizontal(id="type-row"):
                    yield Select(
                        [("Ollama (local)", "ollama"), ("API (cloud)", "api")],
                        value=self._ptype,
                        id="input-type",
                        allow_blank=False,
                    )

                yield Label("Model", classes="field-label")
                yield Input(
                    value=ex.get("model", ""),
                    placeholder="e.g. qwen3:8b or gpt-4o",
                    id="input-model",
                )

                yield Label(
                    "Name  (optional — auto-generated if blank)", classes="field-label"
                )
                yield Input(
                    value=ex.get("name", ""),
                    placeholder="e.g. my-ollama",
                    id="input-name",
                )

                yield Label("URL", classes="field-label")
                yield Input(
                    value=default_url,
                    placeholder=_OLLAMA_DEFAULT_URL,
                    id="input-url",
                )

                yield Label(
                    "Temperature  (0.0–2.0, blank = global default)",
                    classes="field-label",
                )
                yield Input(
                    value=(
                        str(ex.get("temperature", ""))
                        if ex.get("temperature") is not None
                        else ""
                    ),
                    placeholder="0.7",
                    id="input-temperature",
                )

                yield Label("API Key", classes="field-label")
                with Horizontal(id="key-row"):
                    yield Input(
                        value=ex.get("api_key", ""),
                        placeholder="sk-… (leave blank for Ollama)",
                        password=True,
                        id="input-key",
                    )
                    yield Button("show", id="btn-toggle-key", variant="default")

                yield Static("", id="error-label")

                with Horizontal(id="footer"):
                    yield Button("Cancel", id="btn-cancel", variant="default")
                    yield Button(
                        "Add" if self._mode == "add" else "Save",
                        id="btn-confirm",
                        variant="primary",
                    )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "input-type":
            return
        self._ptype = str(event.value)
        url_input = self.query_one("#input-url", Input)
        if not url_input.value or url_input.value == _OLLAMA_DEFAULT_URL:
            url_input.value = _OLLAMA_DEFAULT_URL if self._ptype == "ollama" else ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.action_cancel()
        elif event.button.id == "btn-confirm":
            self.action_confirm()
        elif event.button.id == "btn-toggle-key":
            self._key_revealed = not self._key_revealed
            inp = self.query_one("#input-key", Input)
            inp.password = not self._key_revealed
            event.button.label = "hide" if self._key_revealed else "show"

    def on_key(self, event) -> None:
        if event.key == "enter":
            self.action_confirm()

    def action_cancel(self) -> None:
        self.dismiss(ProviderModalResult(confirmed=False))

    def action_confirm(self) -> None:
        model = self.query_one("#input-model", Input).value.strip()
        if not model:
            err = self.query_one("#error-label", Static)
            err.update("Model is required.")
            err.add_class("visible")
            return

        ptype = str(self.query_one("#input-type", Select).value)
        url = self.query_one("#input-url", Input).value.strip()
        api_key = self.query_one("#input-key", Input).value.strip()

        if ptype == "api" and not api_key:
            err = self.query_one("#error-label", Static)
            err.update("API key is required for cloud providers.")
            err.add_class("visible")
            return

        temperature: Optional[float] = None
        temp_str = self.query_one("#input-temperature", Input).value.strip()
        if temp_str:
            try:
                temperature = float(temp_str)
                if temperature < 0.0 or temperature > 2.0:
                    err = self.query_one("#error-label", Static)
                    err.update("Temperature must be between 0.0 and 2.0.")
                    err.add_class("visible")
                    return
            except ValueError:
                err = self.query_one("#error-label", Static)
                err.update("Temperature must be a number (e.g. 0.7).")
                err.add_class("visible")
                return

        self.dismiss(
            ProviderModalResult(
                confirmed=True,
                ptype=ptype,
                model=model,
                name=self.query_one("#input-name", Input).value.strip(),
                url=url,
                api_key=api_key,
                temperature=temperature,
            )
        )
