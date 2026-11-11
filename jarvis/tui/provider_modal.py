"""Provider add/edit modal — triggered by /providers add (no flags) and /providers edit <name>."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

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


_URL_HINT = {
    "llamacpp": "blank = the local runner's default",
    "ollama-cloud": "blank = https://ollama.com",
    "api": "e.g. https://integrate.api.nvidia.com or http://localhost:11434 (Ollama)",
}


def _is_loopback(url: str) -> bool:
    return urlparse(url).hostname in ("localhost", "127.0.0.1", "::1")


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
        # A new provider starts with no type chosen, so a cloud model and key
        # cannot be saved under a local default by accident (#316).
        self._ptype = self._existing.get("type", "")
        self._key_revealed = False

    def compose(self) -> ComposeResult:
        ex = self._existing
        title = (
            "Add Provider"
            if self._mode == "add"
            else f"Edit Provider: {ex.get('name', '')}"
        )
        default_url = ex.get("url", "")

        with Vertical(id="provider-dialog"):
            with VerticalScroll(id="form-scroll"):
                yield Label(title, id="dialog-title")

                yield Label("Type", classes="field-label")
                with Horizontal(id="type-row"):
                    yield Select(
                        [
                            ("Local (llama.cpp)", "llamacpp"),
                            ("Ollama Cloud", "ollama-cloud"),
                            ("API (cloud, or Ollama/LM Studio server)", "api"),
                        ],
                        value=self._ptype or Select.NULL,
                        prompt="Choose a type",
                        id="input-type",
                        allow_blank=not self._ptype,
                    )

                yield Label("Model", classes="field-label")
                yield Input(
                    value=ex.get("model", ""),
                    placeholder="e.g. Qwen/Qwen3-8B-GGUF:Q4_K_M or gpt-4o",
                    id="input-model",
                )

                yield Label(
                    "Name  (optional — auto-generated if blank)", classes="field-label"
                )
                yield Input(
                    value=ex.get("name", ""),
                    placeholder="e.g. my-local",
                    id="input-name",
                )

                yield Label(
                    "URL  (blank = JARVIS runs the model itself)",
                    classes="field-label",
                )
                yield Input(
                    value=default_url,
                    placeholder=_URL_HINT.get(self._ptype, "choose a type first"),
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
                        placeholder="sk-… (leave blank for local models)",
                        password=True,
                        id="input-key",
                    )
                    yield Button("show", id="btn-toggle-key", variant="default")

                yield Static("", id="error-label")

                with Horizontal(id="footer"):
                    yield Button("Cancel", id="btn-cancel", variant="default")
                    yield Button(
                        self._confirm_label(),
                        id="btn-confirm",
                        variant="primary",
                    )

    def _confirm_label(self) -> str:
        return "Add" if self._mode == "add" else "Save"

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "input-type":
            return
        self._ptype = "" if event.value is Select.NULL else str(event.value)
        self.query_one("#input-url", Input).placeholder = _URL_HINT.get(
            self._ptype, "choose a type first"
        )

    def _show_error(self, text: str) -> None:
        err = self.query_one("#error-label", Static)
        err.update(text)
        err.add_class("visible")

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

        chosen = self.query_one("#input-type", Select).value
        if chosen is Select.NULL:
            self._show_error("Choose a type.")
            return
        ptype = str(chosen)
        url = self.query_one("#input-url", Input).value.strip()
        api_key = self.query_one("#input-key", Input).value.strip()

        if ptype == "api" and not url:
            self._show_error(f"An API provider needs its URL ({_URL_HINT['api']}).")
            return
        if ptype == "api" and not api_key and not _is_loopback(url):
            self._show_error("API key is required for cloud providers.")
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
