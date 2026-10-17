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
from textual.widgets import Button, Input, Label, Static

from ..core.setup_queue import FormSpec

MASK = "•" * 8
KEYS_HINT = "Tab or ↑↓ move · Enter next · Ctrl+R shows a secret · Esc cancels"
REVIEW_HINT = "Enter or Ctrl+S saves · Esc goes back"


class _Field(Vertical):
    """One setting: its name, what it is for, and an input."""

    DEFAULT_CSS = """
    _Field {
        height: auto;
        margin-bottom: 1;
    }
    _Field .field-desc {
        color: $text-muted;
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
        self.initial = (
            ""
            if self.secret
            else spec.current.get(self.key, str(prop.get("default") or ""))
        )
        self.input = Input(
            value=self.initial, password=self.secret, placeholder=self._placeholder()
        )

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
        tail = f"  [dim]({', '.join(notes)})[/dim]" if notes else ""
        yield Label(f"[bold]{escape(self.label)}[/bold]{tail}")
        if self._prop.get("description"):
            yield Static(
                str(self._prop["description"]), classes="field-desc", markup=False
            )
        yield self.input

    @property
    def value(self) -> str:
        return self.input.value.strip()


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
        yield Label(f"Set up {escape(self.spec.server_name)}", id="setup-title")
        if self.spec.summary:
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
            first.input.focus()
        else:
            self.query_one("#setup-next", Button).focus()

    # -- what will be saved ------------------------------------------------------

    def still_needed(self) -> List[str]:
        return [f.label for f in self._fields if f.needed and not f.value]

    def values(self) -> Dict[str, str]:
        """What to write: each changed field, and each required one that was unset.

        An untouched default is left out, as it is at the plain prompt: the
        server applies its own defaults, and writing one would pin it.
        """
        return {
            f.key: f.value
            for f in self._fields
            if f.value and (f.needed or f.value != f.initial.strip())
        }

    def _review_text(self) -> str:
        values = self.values()
        if not values:
            lines = ["Nothing to save; the settings stay as they are."]
        else:
            lines = [f"Saving for {self.spec.server_name}:"]
            for f in self._fields:
                if f.key in values:
                    shown = MASK if f.secret else values[f.key]
                    lines.append(f" · {f.label}: {shown}")
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
        needed = self.still_needed()
        status.update(f"Still needed: {', '.join(needed)}" if needed else "")
        go.disabled = bool(needed)

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
            self._fields[0].input.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        inputs = [f.input for f in self._fields]
        if event.input is inputs[-1]:
            self.action_advance()
        else:
            inputs[inputs.index(event.input) + 1].focus()

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
        needed = [f for f in self._fields if f.needed and not f.value]
        if needed:
            self.app.bell()
            needed[0].input.focus()
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
            if f.secret and f.input is focused:
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
