"""Pure input-line logic for the TUI: completion, history, word deletion.

Kept free of Textual imports so the state machines can be unit-tested
directly; ``command_input.py`` is the thin widget layer that drives them.
"""

from __future__ import annotations

from typing import Sequence

CommandCatalog = Sequence[tuple[str, str]]


def is_command_prefix(text: str) -> bool:
    """True while *text* is still a bare, unfinished ``/command`` token.

    A space means the command is settled and the user is typing arguments, so
    neither completion nor the suggestion dropdown should keep interfering.
    """
    return text.startswith("/") and not any(ch.isspace() for ch in text)


def match_commands(prefix: str, commands: Sequence[str]) -> list[str]:
    """Commands starting with *prefix*, case-insensitively, in catalog order."""
    if not is_command_prefix(prefix):
        return []
    low = prefix.lower()
    return [command for command in commands if command.lower().startswith(low)]


def filter_commands(text: str, catalog: CommandCatalog) -> list[tuple[str, str]]:
    """Catalog rows the suggestion dropdown should show for *text*."""
    if not is_command_prefix(text):
        return []
    low = text.lower()
    return [row for row in catalog if row[0].lower().startswith(low)]


def suggestion_label(name: str, description: str, name_width: int = 12) -> str:
    """One dropdown row: the command in an aligned column, then its description.

    Overflow is ellipsized by the dropdown itself (``text-overflow`` in CSS, so
    it stays responsive to the terminal width); this only lines the rows up.
    """
    return f"{name.ljust(name_width)}  {description}"


class CommandCompleter:
    """Tab-completion over a fixed command list, with cycling.

    The first Tab on a fresh prefix returns the first match; further Tabs on
    the value this completer itself produced walk the remaining matches and
    wrap.  Anything the user types breaks that chain because the incoming text
    no longer equals what was last emitted.
    """

    def __init__(self, commands: Sequence[str]) -> None:
        self._commands = list(commands)
        self._matches: list[str] = []
        self._index = 0
        self._emitted: str | None = None

    def reset(self) -> None:
        """Forget the current cycle (call when the user edits the line)."""
        self._matches = []
        self._index = 0
        self._emitted = None

    def cycling(self, text: str) -> bool:
        """True while *text* is exactly the completion this completer last produced."""
        return bool(self._matches) and text == self._emitted

    def complete(self, text: str) -> str | None:
        """Next completion for *text*, followed by a space; None when there is none.

        The space is where the arguments go. Tab again walks the other matches
        while the line is still exactly what Tab produced; sending strips it.
        """
        if self.cycling(text):
            self._index = (self._index + 1) % len(self._matches)
        elif is_command_prefix(text):
            matches = match_commands(text, self._commands)
            if not matches:
                self.reset()
                return None
            self._matches = matches
            self._index = 0
        else:
            self.reset()
            return None

        self._emitted = self._matches[self._index] + " "
        return self._emitted


class InputHistory:
    """Session-scoped shell-style history for the message line.

    Nothing is persisted: the list dies with the TUI process.
    """

    def __init__(self, limit: int = 500) -> None:
        self._entries: list[str] = []
        self._limit = limit
        self._cursor: int | None = None
        self._draft: str | None = None

    @property
    def entries(self) -> tuple[str, ...]:
        return tuple(self._entries)

    @property
    def navigating(self) -> bool:
        return self._cursor is not None

    def record(self, text: str) -> None:
        """Remember a submitted line and leave navigation mode."""
        self.reset()
        entry = text.strip()
        if not entry:
            return
        if self._entries and self._entries[-1] == entry:
            return
        self._entries.append(entry)
        if len(self._entries) > self._limit:
            del self._entries[: len(self._entries) - self._limit]

    def reset(self) -> None:
        """Leave navigation mode and drop any stashed draft."""
        self._cursor = None
        self._draft = None

    def previous(self, current: str) -> str | None:
        """Step back through history; None when there is nothing to show.

        *current* is stashed on the first step so Down can restore the line the
        user was actually writing.
        """
        if not self._entries:
            return None
        if self._cursor is None:
            self._draft = current
            self._cursor = len(self._entries) - 1
        elif self._cursor > 0:
            self._cursor -= 1
        return self._entries[self._cursor]

    def next(self) -> str | None:
        """Step forward; past the newest entry this restores the stashed draft.

        Returns None only when history navigation was never started, so an
        empty string ("restore an empty draft") stays distinguishable.
        """
        if self._cursor is None:
            return None
        if self._cursor < len(self._entries) - 1:
            self._cursor += 1
            return self._entries[self._cursor]
        draft = self._draft or ""
        self.reset()
        return draft


def _char_class(char: str) -> str:
    if char.isspace():
        return "space"
    if char.isalnum() or char == "_":
        return "word"
    return "punct"


def delete_word_left(text: str, cursor: int) -> tuple[str, int]:
    """Delete the word left of *cursor*; returns the new (text, cursor)."""
    cursor = max(0, min(cursor, len(text)))
    start = cursor
    while start > 0 and text[start - 1].isspace():
        start -= 1
    if start > 0:
        kind = _char_class(text[start - 1])
        while start > 0 and _char_class(text[start - 1]) == kind:
            start -= 1
    return text[:start] + text[cursor:], start


def delete_word_right(text: str, cursor: int) -> tuple[str, int]:
    """Delete the word right of *cursor*; returns the new (text, cursor)."""
    cursor = max(0, min(cursor, len(text)))
    end = cursor
    while end < len(text) and text[end].isspace():
        end += 1
    if end < len(text):
        kind = _char_class(text[end])
        while end < len(text) and _char_class(text[end]) == kind:
            end += 1
    return text[:cursor] + text[end:], cursor
