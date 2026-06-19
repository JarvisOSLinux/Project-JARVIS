"""TUI settings editor modal — /settings opens an editable form for runtime config."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static


@dataclass
class _SettingDef:
    key: str
    label: str
    category: str
    kind: str  # "int", "float", "bool", "select"
    options: Optional[List[Tuple[str, str]]] = None
    hint: str = ""


_SETTINGS: List[_SettingDef] = [
    _SettingDef(
        "CONFIRMATION_MODE",
        "Confirmation mode",
        "Security",
        "select",
        [("smart", "smart"), ("ask_all", "ask_all"), ("allow_all", "allow_all")],
    ),
    _SettingDef(
        "CONFIRMATION_TIMEOUT",
        "Confirmation timeout (s)",
        "Security",
        "int",
        hint="Auto-deny after this many seconds",
    ),
    _SettingDef(
        "MCP_BUFFER_SIZE",
        "MCP buffer size",
        "Memory",
        "int",
        hint="Recently-used server docs kept in context",
    ),
    _SettingDef(
        "RAG_TOP_K",
        "RAG top-k",
        "Memory",
        "int",
        hint="Memories per RAG query",
    ),
    _SettingDef(
        "RAG_MIN_SCORE",
        "RAG min score",
        "Memory",
        "float",
        hint="Minimum cosine similarity (0.0–1.0)",
    ),
    _SettingDef(
        "MAX_GOALS_IN_CONTEXT",
        "Max goals in context",
        "LLM",
        "int",
        hint="Goal cap sent to LLM per turn",
    ),
    _SettingDef(
        "RESET_HISTORY_AFTER_RESPONSE",
        "Reset history after response",
        "LLM",
        "bool",
    ),
    _SettingDef(
        "OUTPUT_MODE",
        "Output mode",
        "Output",
        "select",
        [("text", "text"), ("voice", "voice")],
    ),
    _SettingDef(
        "LOG_LEVEL",
        "Log level",
        "Output",
        "select",
        [
            ("DEBUG", "DEBUG"),
            ("INFO", "INFO"),
            ("WARNING", "WARNING"),
            ("ERROR", "ERROR"),
        ],
    ),
]


@dataclass
class SettingsEditorResult:
    confirmed: bool
    changes: Dict[str, str]


class SettingsEditorModal(ModalScreen[SettingsEditorResult]):
    """Editable settings form. Dismissed with a SettingsEditorResult."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+s", "confirm", "Save", show=True),
    ]

    CSS = """
    SettingsEditorModal {
        align: center middle;
    }

    #settings-editor-dialog {
        width: 64;
        height: auto;
        max-height: 90vh;
        border: round $primary;
        background: $surface;
        padding: 0;
    }

    #settings-editor-scroll {
        padding: 1 2 1 2;
    }

    #settings-editor-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }

    .settings-category {
        text-style: bold;
        color: $accent;
        margin-top: 1;
    }

    .settings-field-label {
        margin-top: 1;
    }

    .settings-hint {
        color: $text-muted;
        text-style: italic;
    }

    .settings-select-row {
        height: 3;
        margin-bottom: 0;
    }

    .settings-select-row Select {
        width: 1fr;
    }

    #settings-error {
        color: $error;
        margin-top: 1;
        display: none;
    }

    #settings-error.visible {
        display: block;
    }

    #settings-footer {
        height: 3;
        align: right middle;
        margin-top: 1;
        padding: 0 2;
    }

    #settings-btn-cancel {
        margin-right: 2;
    }
    """

    def compose(self) -> ComposeResult:
        from ..config import Config

        with Vertical(id="settings-editor-dialog"):
            with VerticalScroll(id="settings-editor-scroll"):
                yield Label("Settings", id="settings-editor-title")

                current_category = ""
                for s in _SETTINGS:
                    if s.category != current_category:
                        current_category = s.category
                        yield Label(
                            f"— {current_category} —", classes="settings-category"
                        )

                    yield Label(s.label, classes="settings-field-label")
                    if s.hint:
                        yield Label(s.hint, classes="settings-hint")

                    raw = getattr(Config, s.key, "")
                    value = str(raw).lower() if s.kind == "bool" else str(raw)

                    if s.kind == "select" and s.options:
                        with Horizontal(classes="settings-select-row"):
                            yield Select(
                                [(lbl, val) for lbl, val in s.options],
                                value=value,
                                id=f"setting-{s.key}",
                                allow_blank=False,
                            )
                    elif s.kind == "bool":
                        with Horizontal(classes="settings-select-row"):
                            yield Select(
                                [("true", "true"), ("false", "false")],
                                value="true" if raw else "false",
                                id=f"setting-{s.key}",
                                allow_blank=False,
                            )
                    else:
                        yield Input(
                            value=value,
                            id=f"setting-{s.key}",
                        )

                yield Static("", id="settings-error")

                with Horizontal(id="settings-footer"):
                    yield Button("Cancel", id="settings-btn-cancel", variant="default")
                    yield Button("Save", id="settings-btn-confirm", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-btn-cancel":
            self.action_cancel()
        elif event.button.id == "settings-btn-confirm":
            self.action_confirm()

    def on_key(self, event: Any) -> None:
        if event.key == "enter":
            self.action_confirm()

    def action_cancel(self) -> None:
        self.dismiss(SettingsEditorResult(confirmed=False, changes={}))

    def action_confirm(self) -> None:
        from ..config import Config

        changes: Dict[str, str] = {}
        for s in _SETTINGS:
            widget_id = f"setting-{s.key}"

            if s.kind in ("select", "bool"):
                widget = self.query_one(f"#{widget_id}", Select)
                new_val = str(widget.value)
            else:
                widget = self.query_one(f"#{widget_id}", Input)
                new_val = widget.value.strip()

            if s.kind == "int":
                try:
                    int(new_val)
                except ValueError:
                    err = self.query_one("#settings-error", Static)
                    err.update(f"{s.label} must be an integer.")
                    err.add_class("visible")
                    return
            elif s.kind == "float":
                try:
                    float(new_val)
                except ValueError:
                    err = self.query_one("#settings-error", Static)
                    err.update(f"{s.label} must be a number.")
                    err.add_class("visible")
                    return

            old_raw = getattr(Config, s.key, "")
            if s.kind == "bool":
                old_val = "true" if old_raw else "false"
            else:
                old_val = str(old_raw)

            if new_val != old_val:
                changes[s.key] = new_val

        self.dismiss(SettingsEditorResult(confirmed=True, changes=changes))
