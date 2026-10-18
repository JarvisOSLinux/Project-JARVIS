"""The setup form: every setting of one server on one screen (#254).

`jarvis setups fill` draws it under the prompt as a small inline Textual app
(Textual runs it full-screen on Windows, which has no inline driver). The TUI
shows the same widget in a modal. All fields are on screen at once, so Tab,
Shift+Tab and the arrow keys move freely between them. Defaults and current
values are filled in, secrets are masked, and nothing is returned before a
review step.

The form only returns values. Its caller writes them through dmcp, so a
secret goes from the input to dmcp's config and nowhere else (#242).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.markup import escape
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, SelectionList, Static, Switch

from ..core.setup_queue import (
    FormSpec,
    as_setting_value,
    boolean_spellings,
    value_error,
)

MASK = "•" * 8
KEYS_HINT = "Tab or ↑↓ move · Enter next · Ctrl+R shows a secret · Esc cancels"
REVIEW_HINT = "Enter or Ctrl+S saves · Esc goes back"


class _Field(Vertical):
    """One setting: its name, what it is for, and a widget for its type.

    A text box by default; a switch for a boolean, a select for a choice, a
    checklist for a choice of several, and a whole-number box for an integer
    (mcp-registry#115). A secret is always a masked text box.
    """

    DEFAULT_CSS = """
    _Field {
        height: auto;
        margin-bottom: 1;
    }
    _Field .field-desc {
        color: $text-muted;
    }
    _Field SelectionList {
        height: auto;
        max-height: 12;
    }
    """

    def __init__(self, prop: Dict[str, Any], spec: FormSpec) -> None:
        super().__init__()
        self.key: str = prop["key"]
        self.label = str(prop.get("label") or self.key)
        self.secret = bool(prop.get("sensitive"))
        self.needed = self.key in spec.missing
        self.secret_set = self.key in spec.secrets_set
        self._prop = prop
        self.kind = "string" if self.secret else str(prop.get("type") or "string")
        self.choices = [str(c) for c in prop.get("choices") or []]
        self.separator = str(prop.get("separator") or ",")
        self.spelled = boolean_spellings(prop)
        # What the server runs with today: its current value, else its default.
        self.baseline = (
            ""
            if self.secret
            else spec.current.get(self.key, str(prop.get("default") or ""))
        )
        if self.kind == "boolean":
            # An unset switch is off; a value spelled another way ("True") is
            # read as what it means, so an untouched switch writes nothing.
            self.baseline = as_setting_value(
                prop, self.baseline or self.spelled[1]
            ) or (self.baseline)
        self.suggested = None if self.secret else spec.proposed.get(self.key)
        self.initial = self.suggested if self.suggested is not None else self.baseline
        self.input: Optional[Input] = None
        self.widget = self._make_widget()

    def _make_widget(self) -> Any:
        if self.kind == "boolean":
            return Switch(value=self.initial == self.spelled[0])
        if self.kind == "choice" and self._prop.get("multiple"):
            chosen = set(self.initial.split(self.separator)) if self.initial else set()
            return SelectionList(*((c, c, c in chosen) for c in self.choices))
        if self.kind == "choice":
            return Select(
                [(c, c) for c in self.choices],
                value=self.initial if self.initial in self.choices else Select.NULL,
                prompt="choose one" if self.needed else "optional; leave empty",
            )
        self.input = Input(
            value=self.initial,
            password=self.secret,
            placeholder=self._placeholder(),
            type="integer" if self.kind == "integer" else "text",
        )
        return self.input

    def _placeholder(self) -> str:
        if self.secret and self.secret_set:
            return "already set; type a new value to replace it"
        if self.secret:
            return "hidden as you type; Ctrl+R shows it"
        if not self.needed:
            return "optional; leave empty to skip"
        return ""

    def compose(self) -> ComposeResult:
        notes = []
        if self.needed:
            notes.append("[bold red]required[/bold red]")
        elif self._prop.get("required"):
            notes.append("required, already set")
        if self.secret:
            notes.append("secret")
        if self.kind == "choice" and self._prop.get("multiple"):
            notes.append("any of these; Space ticks one")
        if self.kind == "integer":
            low, high = self._prop.get("min"), self._prop.get("max")
            if isinstance(low, int) and isinstance(high, int):
                notes.append(f"{low} to {high}")
        if self.suggested is not None:
            notes.append("[bold yellow]suggested by JARVIS[/bold yellow]")
        tail = f"  [dim]({', '.join(notes)})[/dim]" if notes else ""
        yield Label(f"[bold]{escape(self.label)}[/bold]{tail}")
        if self._prop.get("description"):
            yield Static(
                str(self._prop["description"]), classes="field-desc", markup=False
            )
        yield self.widget

    @property
    def value(self) -> str:
        """The setting as it would be stored: always a string."""
        if self.kind == "boolean":
            return self.spelled[0] if self.widget.value else self.spelled[1]
        if isinstance(self.widget, SelectionList):
            chosen = set(self.widget.selected)
            return self.separator.join(c for c in self.choices if c in chosen)
        if isinstance(self.widget, Select):
            picked = self.widget.value
            return picked if isinstance(picked, str) else ""
        return self.widget.value.strip()

    @property
    def error(self) -> Optional[str]:
        value = self.value
        return value_error(self._prop, value) if value and not self.secret else None

    def shown(self, value: str) -> str:
        """How the review shows a value."""
        if self.secret:
            return MASK
        if self.kind == "boolean":
            return "on" if value == self.spelled[0] else "off"
        return value


class SetupForm(Vertical):
    """The form. Posts `SetupForm.Finished` with the values, or None if cancelled."""

    DEFAULT_CSS = """
    SetupForm {
        height: auto;
        padding: 0 1;
    }
    SetupForm #setup-title {
        text-style: bold;
        color: $primary;
    }
    SetupForm #setup-summary {
        color: $text-muted;
        margin-bottom: 1;
    }
    SetupForm #setup-fields {
        height: auto;
        max-height: 70vh;
    }
    SetupForm #setup-review {
        height: auto;
        margin-bottom: 1;
    }
    SetupForm #setup-footer {
        height: auto;
        align: right middle;
    }
    SetupForm #setup-status {
        width: 1fr;
        color: $text-muted;
    }
    SetupForm #setup-cancel {
        margin-right: 1;
    }
    SetupForm #setup-keys {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("down", "app.focus_next", "Next", show=False),
        Binding("up", "app.focus_previous", "Previous", show=False),
        Binding("ctrl+r", "reveal", "Show or hide a secret", show=False),
        Binding("ctrl+s", "advance", "Review, then save", show=False),
        Binding("escape", "back", "Back or cancel", show=False),
    ]

    class Finished(Message):
        def __init__(self, values: Optional[Dict[str, str]]) -> None:
            super().__init__()
            self.values = values

    def __init__(self, spec: FormSpec) -> None:
        super().__init__()
        self.spec = spec
        self.reviewing = False
        self._fields = [_Field(p, spec) for p in spec.fields]

    def compose(self) -> ComposeResult:
        name = escape(self.spec.server_name)
        title = {
            "configure": f"Change settings for {name}",
            "edit": f"Settings for {name}",
        }.get(self.spec.kind, f"Set up {name}")
        yield Label(title, id="setup-title")
        if self.spec.kind == "configure":
            yield Static(
                "JARVIS asked to change these settings. Check each one before saving; "
                "nothing changes until you save.",
                id="setup-summary",
                markup=False,
            )
        elif self.spec.summary:
            yield Static(self.spec.summary, id="setup-summary", markup=False)
        with VerticalScroll(id="setup-fields"):
            yield from self._fields
        yield Static("", id="setup-review", markup=False)
        with Horizontal(id="setup-footer"):
            yield Static("", id="setup-status")
            yield Button("Cancel", id="setup-cancel")
            yield Button("Review", id="setup-next", variant="primary")
        yield Static(KEYS_HINT, id="setup-keys")

    def on_mount(self) -> None:
        self.query_one("#setup-review").display = False
        self._refresh()
        first = next((f for f in self._fields if f.needed), None) or next(
            iter(self._fields), None
        )
        if first is not None:
            first.widget.focus()
        else:
            self.query_one("#setup-next", Button).focus()

    # -- what will be saved ------------------------------------------------------

    def still_needed(self) -> List[str]:
        return [f.label for f in self._fields if f.needed and not f.value]

    def invalid(self) -> List[str]:
        return [f"{f.label} {f.error}" for f in self._fields if f.error]

    def values(self) -> Dict[str, str]:
        """What to write: each field that differs from what the server runs with
        today, and each required one that was unset.

        An untouched default is left out, as it is at the plain prompt: the
        server applies its own defaults, and writing one would pin it. A value
        JARVIS suggested counts as a change until it is changed back.
        """
        return {
            f.key: f.value
            for f in self._fields
            if f.value and (f.needed or f.value != f.baseline.strip())
        }

    def _review_text(self) -> str:
        values = self.values()
        if not values:
            lines = ["Nothing to save; the settings stay as they are."]
        else:
            lines = [f"Saving for {self.spec.server_name}:"]
            for f in self._fields:
                if f.key in values:
                    shown = f.shown(values[f.key])
                    mark = (
                        "  (suggested by JARVIS)"
                        if values[f.key] == f.suggested
                        else ""
                    )
                    lines.append(f" · {f.label}: {shown}{mark}")
        unchanged = [f.label for f in self._fields if f.key not in values]
        if unchanged and values:
            lines.append(f"Unchanged: {', '.join(unchanged)}")
        return "\n".join(lines)

    # -- moving around -----------------------------------------------------------

    def _refresh(self) -> None:
        status = self.query_one("#setup-status", Static)
        go = self.query_one("#setup-next", Button)
        if self.reviewing:
            status.update("")
            go.disabled = False
            return
        needed, invalid = self.still_needed(), self.invalid()
        if needed:
            status.update(f"Still needed: {', '.join(needed)}")
        elif invalid:
            status.update(f"Fix: {'; '.join(invalid)}")
        else:
            status.update("")
        go.disabled = bool(needed or invalid)

    def _show_review(self, on: bool) -> None:
        self.reviewing = on
        self.query_one("#setup-fields").display = not on
        review = self.query_one("#setup-review", Static)
        review.display = on
        if on:
            review.update(self._review_text())
        self.query_one("#setup-cancel", Button).label = "Back" if on else "Cancel"
        self.query_one("#setup-next", Button).label = "Save" if on else "Review"
        self.query_one("#setup-keys", Static).update(REVIEW_HINT if on else KEYS_HINT)
        self._refresh()
        if on:
            self.query_one("#setup-next", Button).focus()
        elif self._fields:
            self._fields[0].widget.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        self._refresh()

    def on_select_changed(self, event: Select.Changed) -> None:
        self._refresh()

    def on_selection_list_selected_changed(
        self, event: SelectionList.SelectedChanged
    ) -> None:
        self._refresh()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        widgets = [f.widget for f in self._fields]
        if event.input is widgets[-1]:
            self.action_advance()
        else:
            widgets[widgets.index(event.input) + 1].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "setup-next":
            self.action_advance()
        elif event.button.id == "setup-cancel":
            self.action_back()

    def action_advance(self) -> None:
        if self.reviewing:
            self.post_message(self.Finished(self.values()))
            return
        problems = [f for f in self._fields if (f.needed and not f.value) or f.error]
        if problems:
            self.app.bell()
            problems[0].widget.focus()
            return
        self._show_review(True)

    def action_back(self) -> None:
        if self.reviewing:
            self._show_review(False)
        else:
            self.post_message(self.Finished(None))

    def action_reveal(self) -> None:
        focused = self.app.focused
        for f in self._fields:
            if f.secret and f.input is not None and f.input is focused:
                f.input.password = not f.input.password


class SetupFormScreen(ModalScreen[Optional[Dict[str, str]]]):
    """The form in the TUI. Dismissed with the values, or None if cancelled."""

    DEFAULT_CSS = """
    SetupFormScreen {
        align: center middle;
    }
    SetupFormScreen > #setup-dialog {
        width: 80;
        max-width: 95%;
        height: auto;
        max-height: 90%;
        border: round $primary;
        background: $surface;
        padding: 1 1;
    }
    """

    def __init__(self, spec: FormSpec) -> None:
        super().__init__()
        self._spec = spec

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-dialog"):
            yield SetupForm(self._spec)

    def on_setup_form_finished(self, message: SetupForm.Finished) -> None:
        self.dismiss(message.values)


class SetupFormApp(App[Optional[Dict[str, str]]]):
    """The form on its own, for `jarvis setups fill`."""

    ENABLE_COMMAND_PALETTE = False

    def __init__(self, spec: FormSpec) -> None:
        super().__init__()
        self._spec = spec

    def compose(self) -> ComposeResult:
        yield SetupForm(self._spec)

    def on_setup_form_finished(self, message: SetupForm.Finished) -> None:
        self.exit(message.values)


def run_inline(spec: FormSpec) -> Optional[Dict[str, str]]:
    """Show the form under the prompt; the values to save, or None if cancelled."""
    return SetupFormApp(spec).run(inline=True)
