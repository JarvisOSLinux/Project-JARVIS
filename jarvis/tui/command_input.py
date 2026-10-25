"""Message-line widgets: slash-command completion, suggestions, history.

``CommandInput`` is the ``Input`` the chat pane uses; ``CommandDropdown`` is
the overlay it drives.  Focus never leaves the input — arrow keys and Enter are
forwarded to the dropdown from here — so typing keeps filtering the list while
it is open.  All of the decision logic lives in ``input_helpers``.
"""

from __future__ import annotations

from typing import Any, Optional

from textual import events
from textual.binding import Binding
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from .input_helpers import (
    CommandCompleter,
    InputHistory,
    delete_word_left,
    delete_word_right,
    filter_commands,
    is_command_prefix,
    suggestion_label,
)
from .slash_commands_doc import slash_command_catalog


class CommandDropdown(OptionList):
    """Slash-command suggestions floating above the message line."""

    # Focus stays in the input; this list is driven by forwarded key presses.
    can_focus = False

    DEFAULT_CSS = """
    CommandDropdown {
        display: none;
        layer: dropdown;
        dock: bottom;
        margin: 0 0 4 0;
        width: 68;
        max-width: 100%;
        height: auto;
        max-height: 10;
        border: round $accent;
        background: $surface;
        padding: 0 1;
        /* One line per command, so a long description cannot push the list
           into a wall of wrapped text. */
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }

    CommandDropdown.-visible {
        display: block;
    }
    """


class CommandInput(Input):
    """The chat message line, with shell-grade editing ergonomics."""

    BINDINGS = [
        Binding("tab", "complete", "Complete command", show=False),
        Binding("up", "history_prev", "Previous message", show=False),
        Binding("down", "history_next", "Next message", show=False),
        Binding("escape", "close_dropdown", "Close suggestions", show=False),
        # Terminal reality check (#133): ctrl+backspace / ctrl+delete only
        # arrive as distinct keys under the Kitty keyboard protocol (kitty,
        # Ghostty, WezTerm, recent xterm).  Legacy terminals send 0x08 for
        # Ctrl+Backspace, which Textual decodes as plain ``backspace`` — it
        # cannot be told apart from Backspace, so rebinding it would eat a
        # whole word on every ordinary backspace.  ctrl+w is the readline
        # fallback every terminal delivers, and Textual already decodes
        # Alt+Backspace (ESC DEL) to ctrl+w.  Textual's own defaults map
        # ctrl+backspace to *right*-word deletion, which is why they are
        # overridden here rather than extended.
        Binding("ctrl+w", "delete_word_left", "Delete word left", show=False),
        Binding("ctrl+backspace", "delete_word_left", "Delete word left", show=False),
        Binding("alt+backspace", "delete_word_left", "Delete word left", show=False),
        Binding("ctrl+delete", "delete_word_right", "Delete word right", show=False),
        Binding("alt+d", "delete_word_right", "Delete word right", show=False),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._catalog = slash_command_catalog()
        self._completer = CommandCompleter([name for name, _ in self._catalog])
        self._history = InputHistory()
        self._dropdown: Optional[CommandDropdown] = None
        # The exact string this widget last wrote, so a programmatic update is
        # not mistaken for the user typing.
        self._written: Optional[str] = None

    # ------------------------------------------------------------------
    # Dropdown plumbing
    # ------------------------------------------------------------------

    def _get_dropdown(self) -> Optional[CommandDropdown]:
        if self._dropdown is None:
            try:
                self._dropdown = self.screen.query_one(CommandDropdown)
            except Exception:
                return None
        return self._dropdown

    @property
    def dropdown_open(self) -> bool:
        dropdown = self._get_dropdown()
        return dropdown is not None and dropdown.has_class("-visible")

    def _sync_dropdown(self, value: str) -> None:
        dropdown = self._get_dropdown()
        if dropdown is None:
            return
        rows = filter_commands(value, self._catalog)
        if not rows:
            dropdown.remove_class("-visible")
            dropdown.clear_options()
            return
        dropdown.clear_options()
        dropdown.add_options(
            [
                Option(suggestion_label(name, description), id=name)
                for name, description in rows
            ]
        )
        dropdown.highlighted = 0
        dropdown.add_class("-visible")

    def _close_dropdown(self) -> None:
        dropdown = self._get_dropdown()
        if dropdown is None:
            return
        dropdown.remove_class("-visible")
        dropdown.clear_options()

    def _highlighted_command(self) -> Optional[str]:
        dropdown = self._get_dropdown()
        if dropdown is None or dropdown.option_count == 0:
            return None
        index = dropdown.highlighted
        if index is None:
            return None
        return dropdown.get_option_at_index(index).id

    def accept_command(self, command: Optional[str]) -> None:
        """Put *command* on the line and dismiss the suggestions."""
        if not command:
            return
        self._write(command + " ", sync_dropdown=False)
        self._close_dropdown()

    # ------------------------------------------------------------------
    # Value updates
    # ------------------------------------------------------------------

    def _write(self, text: str, *, sync_dropdown: bool) -> None:
        self._written = text
        self.value = text
        self.cursor_position = len(text)
        if sync_dropdown:
            self._sync_dropdown(text)

    def watch_value(self, value: str) -> None:
        written, self._written = self._written, None
        if value == written:
            return
        self._completer.reset()
        self._history.reset()
        self._sync_dropdown(value)

    def on_blur(self, event: events.Blur) -> None:
        self._close_dropdown()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Let Tab fall through to focus movement when there is no command to
        # complete, and Esc fall through when it has nothing to close and a
        # line is half typed (an empty line's Esc is the stop, #270).
        if action == "complete":
            return is_command_prefix(self.value) or self._completer.cycling(self.value)
        if action == "close_dropdown":
            return self.dropdown_open or not self.value.strip()
        return True

    def action_complete(self) -> None:
        completion = self._completer.complete(self.value)
        if completion is None:
            return
        self._write(completion, sync_dropdown=True)

    def action_close_dropdown(self) -> None:
        """Esc: close the suggestions; on an empty line, stop JARVIS (#270)."""
        if self.dropdown_open:
            self._close_dropdown()
            return
        stop = getattr(self.app, "action_stop", None)
        if stop is not None:
            stop()

    def action_history_prev(self) -> None:
        dropdown = self._get_dropdown()
        if self.dropdown_open and dropdown is not None:
            dropdown.action_cursor_up()
            return
        entry = self._history.previous(self.value)
        if entry is None:
            return
        self._write(entry, sync_dropdown=False)

    def action_history_next(self) -> None:
        dropdown = self._get_dropdown()
        if self.dropdown_open and dropdown is not None:
            dropdown.action_cursor_down()
            return
        entry = self._history.next()
        if entry is None:
            return
        self._write(entry, sync_dropdown=False)

    def action_delete_word_left(self) -> None:
        value, cursor = delete_word_left(self.value, self.cursor_position)
        if value == self.value:
            return
        self.value = value
        self.cursor_position = cursor

    def action_delete_word_right(self) -> None:
        value, cursor = delete_word_right(self.value, self.cursor_position)
        if value == self.value:
            return
        self.value = value
        self.cursor_position = cursor

    async def action_submit(self) -> None:
        if self.dropdown_open:
            command = self._highlighted_command()
            # Only "select" when the highlight would actually change the line;
            # otherwise a fully typed (or just completed) command would need a
            # second Enter to send.
            if command is not None and command != self.value.rstrip():
                self.accept_command(command)
                return
            self._close_dropdown()
        self._history.record(self.value)
        await super().action_submit()
