"""Server configuration modal — collects configurable parameter values before install."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


@dataclass
class ConfigModalResult:
    confirmed: bool
    values: Dict[str, str]
    missing_required: List[str] = field(default_factory=list)


class _FieldRow(Vertical):
    """One configurable property rendered as label + input + description."""

    DEFAULT_CSS = """
    _FieldRow {
        height: auto;
        margin-bottom: 1;
    }
    _FieldRow Label.field-label {
        text-style: bold;
    }
    _FieldRow Static.field-desc {
        color: $text-muted;
        text-style: italic;
    }
    _FieldRow Input.required-empty {
        border: tall $error;
    }
    _FieldRow Input.required-filled {
        border: tall $success;
    }
    _FieldRow Input.saved {
        border: tall $primary-darken-2;
    }
    _FieldRow .saved-badge {
        color: $success;
        margin-left: 1;
    }
    _FieldRow Horizontal.input-row {
        height: auto;
    }
    """

    def __init__(self, prop: Dict[str, Any], saved_value: str | None) -> None:
        super().__init__()
        self._prop = prop
        self._key = prop["key"]
        self._sensitive = prop.get("sensitive", False)
        self._required = prop.get("required", False)
        self._saved_value = saved_value
        self._revealed = False

        default = prop.get("default") or ""
        self._initial = saved_value if saved_value is not None else default

    def compose(self) -> ComposeResult:
        label = self._prop.get("label") or self._key
        desc = self._prop.get("description", "")
        yield Label(label, classes="field-label")
        with Horizontal(classes="input-row"):
            inp = Input(
                value=self._initial,
                password=self._sensitive,
                id=f"input-{self._key}",
            )
            inp.add_class(self._css_class())
            yield inp
            if self._sensitive:
                yield Button("show", id=f"toggle-{self._key}", variant="default")
            if self._saved_value is not None:
                yield Static("✓ saved", classes="saved-badge")
        if desc:
            yield Static(desc, classes="field-desc")

    def _css_class(self) -> str:
        if self._saved_value is not None:
            return "saved"
        if self._required:
            return "required-filled" if self._initial.strip() else "required-empty"
        return ""

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != f"input-{self._key}":
            return
        val = event.value
        inp = event.input
        inp.remove_class("required-empty", "required-filled", "saved")
        if self._required:
            inp.add_class("required-filled" if val.strip() else "required-empty")
        self.app.post_message(_FieldChanged(self._key, val))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != f"toggle-{self._key}":
            return
        self._revealed = not self._revealed
        inp = self.query_one(f"#input-{self._key}", Input)
        inp.password = not self._revealed
        event.button.label = "hide" if self._revealed else "show"

    @property
    def current_value(self) -> str:
        return self.query_one(f"#input-{self._key}", Input).value


class _FieldChanged(asyncio.Event.__class__):
    """Internal message posted when a field value changes."""

    # Using Textual's message system instead
    pass


from textual.message import Message


class _FieldChanged(Message):
    def __init__(self, key: str, value: str) -> None:
        super().__init__()
        self.key = key
        self.value = value


class ServerConfigModal(ModalScreen[ConfigModalResult]):
    """Modal that collects configurable parameter values before MCP server setup.

    Dismissed with a ConfigModalResult (confirmed=True/False).
    The caller awaits this via push_screen_wait or a Future.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+h", "toggle_sensitive", "Show/hide", show=True),
    ]

    CSS = """
    ServerConfigModal {
        align: center middle;
    }

    #config-dialog {
        width: 72;
        max-height: 90vh;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }

    #dialog-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 0;
    }

    #dialog-subtitle {
        color: $text-muted;
        margin-bottom: 1;
    }

    #section-saved, #section-required, #section-optional {
        height: auto;
        margin-bottom: 1;
    }

    .section-header {
        color: $text-muted;
        text-style: bold;
        margin-bottom: 1;
        padding-bottom: 0;
        border-bottom: solid $primary-darken-3;
    }

    .section-empty {
        color: $text-muted;
        text-style: italic;
        margin-left: 2;
    }

    #footer {
        height: 3;
        align: right middle;
        margin-top: 1;
        padding-top: 1;
        border-top: solid $primary-darken-3;
    }

    #status-label {
        width: 1fr;
        color: $text-muted;
    }

    #btn-cancel {
        margin-right: 2;
    }
    """

    def __init__(
        self,
        server_id: str,
        server_name: str,
        server_desc: str,
        props: List[Dict[str, Any]],
        saved: Dict[str, str],
        future: asyncio.Future[ConfigModalResult],
    ) -> None:
        super().__init__()
        self._server_id = server_id
        self._server_name = server_name
        self._server_desc = server_desc
        self._props = props
        self._saved = saved
        self._future = future
        self._values: Dict[str, str] = {
            p["key"]: (saved.get(p["key"]) or p.get("default") or "") for p in props
        }

    def compose(self) -> ComposeResult:
        saved_props = [p for p in self._props if p["key"] in self._saved]
        required_props = [
            p for p in self._props if p.get("required") and p["key"] not in self._saved
        ]
        optional_props = [
            p
            for p in self._props
            if not p.get("required") and p["key"] not in self._saved
        ]

        with Vertical(id="config-dialog"):
            yield Label(f"Configure: {self._server_name}", id="dialog-title")
            if self._server_desc:
                yield Static(self._server_desc, id="dialog-subtitle")

            with ScrollableContainer():
                if saved_props:
                    with Vertical(id="section-saved"):
                        yield Static("── Saved ──", classes="section-header")
                        for p in saved_props:
                            yield _FieldRow(p, self._saved.get(p["key"]))

                with Vertical(id="section-required"):
                    yield Static("── Required ──", classes="section-header")
                    if required_props:
                        for p in required_props:
                            yield _FieldRow(p, None)
                    else:
                        yield Static(
                            "(all required fields are filled)",
                            classes="section-empty",
                        )

                if optional_props:
                    with Vertical(id="section-optional"):
                        yield Static("── Optional ──", classes="section-header")
                        for p in optional_props:
                            yield _FieldRow(p, None)

            with Horizontal(id="footer"):
                yield Static(self._status_text(), id="status-label")
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button(
                    "Install & Save",
                    id="btn-install",
                    variant="primary",
                    disabled=not self._all_required_filled(),
                )

    def _required_keys(self) -> List[str]:
        return [p["key"] for p in self._props if p.get("required")]

    def _all_required_filled(self) -> bool:
        return all(self._values.get(k, "").strip() for k in self._required_keys())

    def _missing_required(self) -> List[str]:
        return [k for k in self._required_keys() if not self._values.get(k, "").strip()]

    def _status_text(self) -> str:
        required = self._required_keys()
        if not required:
            return "No required fields"
        filled = sum(1 for k in required if self._values.get(k, "").strip())
        total = len(required)
        mark = "✓" if filled == total else "⚠"
        return f"{filled}/{total} required {mark}"

    def on__field_changed(self, event: _FieldChanged) -> None:
        self._values[event.key] = event.value
        self.query_one("#status-label", Static).update(self._status_text())
        self.query_one("#btn-install", Button).disabled = (
            not self._all_required_filled()
        )
        # Auto-save every keystroke
        from ..core.params_store import ParamsStore

        ParamsStore(self._server_id).set(event.key, event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.action_cancel()
        elif event.button.id == "btn-install":
            self._confirm()

    def action_cancel(self) -> None:
        result = ConfigModalResult(
            confirmed=False,
            values=dict(self._values),
            missing_required=self._missing_required(),
        )
        if not self._future.done():
            self._future.set_result(result)
        self.dismiss(result)

    def action_toggle_sensitive(self) -> None:
        focused = self.focused
        if focused and isinstance(focused, Input):
            focused.password = not focused.password

    def _confirm(self) -> None:
        result = ConfigModalResult(confirmed=True, values=dict(self._values))
        if not self._future.done():
            self._future.set_result(result)
        self.dismiss(result)

    def on_key(self, event: Any) -> None:
        if event.key == "enter" and self._all_required_filled():
            self._confirm()
