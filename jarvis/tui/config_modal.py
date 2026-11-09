"""Unified config modal — F2 / /settings / /providers open this tabbed panel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    Rule,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from ..core import settings
from ..core.providers import (
    add_provider,
    edit_provider,
    list_providers,
    move_provider,
    remove_provider,
)
from .provider_modal import ProviderModal

# ---------------------------------------------------------------------------
#  Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ConfigModalResult:
    settings_changes: Dict[str, str]
    providers_changed: bool = False


# ---------------------------------------------------------------------------
#  Unified tabbed config modal
# ---------------------------------------------------------------------------


class ConfigModal(ModalScreen[ConfigModalResult]):
    """Tabbed config panel: Settings + Providers."""

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=False),
        Binding("ctrl+s", "save_settings", "Save settings", show=True),
    ]

    CSS = """
    ConfigModal {
        align: center middle;
    }

    #config-dialog {
        width: 68;
        height: 90vh;
        border: round $primary;
        background: $surface;
        padding: 0;
    }

    #config-title {
        text-style: bold;
        color: $primary;
        padding: 1 2 0 2;
    }

    /* The footer is docked and the tabs take what is left, so the buttons
       are never pushed past the dialog's edge by a long settings list. */
    #config-tabs {
        height: 1fr;
    }

    #config-tabs ContentSwitcher, #config-tabs TabPane {
        height: 1fr;
    }

    #settings-pane {
        height: 1fr;
        padding: 0 2;
    }

    #providers-pane {
        height: 1fr;
        padding: 0 2;
    }

    .settings-category-row {
        height: 1;
        margin-top: 1;
    }

    .settings-category {
        text-style: bold;
        color: $accent;
        width: auto;
    }

    .settings-category-row Rule {
        width: 1fr;
        margin: 0 0 0 1;
        color: $accent;
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
        padding: 0 2;
        display: none;
    }

    #settings-error.visible {
        display: block;
    }

    #config-footer {
        dock: bottom;
        height: auto;
        align: right middle;
        padding: 0 2;
        border-top: solid $primary 30%;
    }

    #config-footer Button {
        margin-left: 1;
    }

    #provider-table {
        height: auto;
        max-height: 40vh;
        margin-top: 1;
    }

    #provider-buttons {
        height: 3;
        align: left middle;
        margin-top: 1;
    }

    #provider-buttons Button {
        margin-right: 1;
    }

    #provider-hint {
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
    }
    """

    def __init__(self, initial_tab: str = "settings") -> None:
        super().__init__()
        self._initial_tab = initial_tab
        self._settings_changes: Dict[str, str] = {}
        self._providers_changed = False
        self._discard_armed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="config-dialog"):
            yield Label("Configuration", id="config-title")

            with TabbedContent("Settings", "Providers", id="config-tabs"):
                with TabPane("Settings", id="tab-settings"):
                    with VerticalScroll(id="settings-pane"):
                        current_section = ""
                        for s in settings.SETTINGS:
                            if s.section != current_section:
                                current_section = s.section
                                with Horizontal(classes="settings-category-row"):
                                    yield Label(
                                        f"— {current_section}",
                                        classes="settings-category",
                                    )
                                    yield Rule()

                            yield Label(s.label, classes="settings-field-label")
                            if s.hint:
                                yield Label(s.hint, classes="settings-hint")

                            options = (
                                [("on", "true"), ("off", "false")]
                                if s.kind == "bool"
                                else [(lbl, val) for val, lbl in s.options]
                            )
                            if options:
                                with Horizontal(classes="settings-select-row"):
                                    yield Select(
                                        options,
                                        value=settings.text(s),
                                        id=f"setting-{s.key}",
                                        allow_blank=False,
                                    )
                            else:
                                yield Input(
                                    value=settings.text(s),
                                    id=f"setting-{s.key}",
                                )

                with TabPane("Providers", id="tab-providers"):
                    with VerticalScroll(id="providers-pane"):
                        yield DataTable(id="provider-table", show_cursor=True)
                        with Horizontal(id="provider-buttons"):
                            yield Button("Add", id="btn-prov-add", variant="primary")
                            yield Button("Edit", id="btn-prov-edit", variant="default")
                            yield Button(
                                "Remove", id="btn-prov-remove", variant="error"
                            )
                        yield Label(
                            "Select a row then Edit/Remove, or Add a new provider.",
                            id="provider-hint",
                        )

            yield Static("", id="settings-error")
            with Horizontal(id="config-footer"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save settings", id="btn-save-settings", variant="primary")

    def on_mount(self) -> None:
        self._refresh_provider_table()
        if self._initial_tab == "providers":
            tabs = self.query_one(TabbedContent)
            tabs.active = "tab-providers"

    def _refresh_provider_table(self) -> None:
        table = self.query_one("#provider-table", DataTable)
        table.clear(columns=True)
        table.add_columns("#", "Name", "Type", "Model", "Temp")
        for i, p in enumerate(list_providers()):
            name = p.get("name", f"provider-{i}")
            ptype = p.get("type", "?")
            model = p.get("model", "?")
            temp = str(p.get("temperature", "")) if p.get("temperature") else "—"
            table.add_row(str(i + 1), name, ptype, model, temp, key=name)

    # -- Button handlers ----------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-save-settings":
            self._save_settings()
        elif bid == "btn-cancel":
            self.action_cancel()
        elif bid == "btn-prov-add":
            self._add_provider()
        elif bid == "btn-prov-edit":
            self._edit_provider()
        elif bid == "btn-prov-remove":
            self._remove_provider()

    def on_key(self, event: Any) -> None:
        if event.key == "enter":
            tabs = self.query_one(TabbedContent)
            if tabs.active == "tab-settings":
                self._save_settings()

    # -- Settings save ------------------------------------------------------

    def _save_settings(self) -> None:
        for s in settings.SETTINGS:
            _, error = settings.parse(s, self._field(s))
            if error:
                err = self.query_one("#settings-error", Static)
                err.update(f"Fix: {error}")
                err.add_class("visible")
                return

        changes = self._edited()
        if not changes:
            err = self.query_one("#settings-error", Static)
            err.update("No changes to save.")
            err.add_class("visible")
            return

        self._settings_changes = changes
        self.dismiss(
            ConfigModalResult(settings_changes=changes, providers_changed=False)
        )

    # -- Provider actions ---------------------------------------------------

    def _get_selected_provider_name(self) -> Optional[str]:
        table = self.query_one("#provider-table", DataTable)
        if table.cursor_row is not None:
            try:
                row_key = table.get_row_at(table.cursor_row)
                return str(row_key[1])  # Name column
            except Exception:
                pass
        return None

    def _add_provider(self) -> None:
        def _on_add(result: Any) -> None:
            if not result or not result.confirmed:
                return
            try:
                add_provider(
                    result.ptype,
                    result.model,
                    name=result.name or None,
                    url=result.url or None,
                    api_key=result.api_key or None,
                    temperature=result.temperature,
                )
                self._providers_changed = True
                self._refresh_provider_table()
            except ValueError:
                pass

        self.app.push_screen(ProviderModal(mode="add"), _on_add)

    def _edit_provider(self) -> None:
        name = self._get_selected_provider_name()
        if not name:
            return

        existing = next((p for p in list_providers() if p.get("name") == name), None)
        if existing is None:
            return

        def _on_edit(result: Any) -> None:
            if not result or not result.confirmed:
                return
            fields: dict = {}
            if result.model:
                fields["model"] = result.model
            if result.url:
                fields["url"] = result.url
            if result.api_key:
                fields["key"] = result.api_key
            if result.ptype:
                fields["type"] = result.ptype
            if result.temperature is not None:
                fields["temperature"] = result.temperature
            if not fields:
                return
            try:
                edit_provider(name, **fields)
                self._providers_changed = True
                self._refresh_provider_table()
            except ValueError:
                pass

        self.app.push_screen(ProviderModal(mode="edit", existing=existing), _on_edit)

    def _remove_provider(self) -> None:
        name = self._get_selected_provider_name()
        if not name:
            return
        try:
            remove_provider(name)
            self._providers_changed = True
            self._refresh_provider_table()
        except ValueError:
            pass

    # -- Dismiss ------------------------------------------------------------

    def _field(self, s: settings.Setting) -> str:
        widget_id = f"#setting-{s.key}"
        if s.kind in ("select", "bool"):
            return str(self.query_one(widget_id, Select).value)
        return self.query_one(widget_id, Input).value.strip()

    def _edited(self) -> Dict[str, str]:
        """Settings whose field differs from the value JARVIS runs with."""
        edited: Dict[str, str] = {}
        for s in settings.SETTINGS:
            new_val = self._field(s)
            if new_val != settings.text(s):
                edited[s.key] = new_val
        return edited

    def action_cancel(self) -> None:
        """Close; with unsaved edits, the first Esc says so and the second discards."""
        edited = self._edited()
        if edited and not self._discard_armed:
            self._discard_armed = True
            labels = [s.label for s in settings.SETTINGS if s.key in edited]
            err = self.query_one("#settings-error", Static)
            err.update(
                f"Not saved: {', '.join(labels)}. Ctrl+S saves; Esc again discards."
            )
            err.add_class("visible")
            return
        self.dismiss(
            ConfigModalResult(
                settings_changes={},
                providers_changed=self._providers_changed,
            )
        )

    def action_save_settings(self) -> None:
        self._save_settings()
