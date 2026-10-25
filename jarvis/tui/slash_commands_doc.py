"""
Session slash-command descriptions for the TUI help modal.

The **session** rows below must stay aligned with
``jarvis/runtime/session_commands.py::handle_slash_command`` (behavior lives there;
this module is the doc source for the help table only).

The **keyboard** section is built from ``JarvisTUI.BINDINGS`` plus a few lines that
are not real Textual bindings (Enter, mouse, help-modal Esc).

These tables are also the single source of truth for the input line's Tab
completion and suggestion dropdown, via ``slash_command_catalog()`` — a command
that is not documented here does not get completed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keeps the command catalog importable without Textual
    from textual.binding import Binding

# (command column, meaning) — sync with jarvis/runtime/session_commands.py
SESSION_SLASH_HELP: tuple[tuple[str, str], ...] = (
    ("/new [title]", "Start a new session; optional title."),
    (
        "/sessions",
        "List sessions (current row marked with an asterisk in text output).",
    ),
    ("/switch <id>", "Switch session by short id prefix."),
    (
        "/rename [<id>] <title>",
        "Rename the session you're in, or the one whose id starts with <id>.",
    ),
    ("/delete <id>", "Delete session by **unique** id prefix."),
    (
        "/context",
        "Show what filled the last prompt, part by part, and how much room is left.",
    ),
    (
        "/stop [all|<n>]",
        # No code span: the help drew "`all` also" as "allalso".
        "Stop this session's work; its running tasks keep running. /stop all "
        "ends them too, /stop <n> ends task n (F4 shows the numbers). Esc on an "
        "empty input is /stop.",
    ),
)

TUI_LOCAL_SLASH_HELP: tuple[tuple[str, str], ...] = (
    (
        "/help [words], /? [words]",
        "Open help, showing only the keys and commands that mention the words "
        "(TUI only; not sent to the LLM). Typing in the help searches too.",
    ),
    (
        "/export [file]",
        "Save plain transcript (Markdown) under `transcripts/`; optional basename only.",
    ),
    ("/clear", "Clear the chat view (on-screen only; session memory unchanged)."),
    (
        "/commands [collapse|expand]",
        "Browse the chat's blocks (↑/↓ move, Enter/Space fold, Esc back), "
        "or fold or unfold them all.",
    ),
    ("/quit, /exit", "Exit the TUI."),
    (
        "/status [full]",
        "Check JARVIS's health: model providers, engines, voice, safety, work, "
        "session. `full` also checks for server updates (goes online).",
    ),
    ("/providers", "List configured providers with live pool status."),
    ("/providers add", "Open guided modal to add a provider."),
    (
        "/providers add --type ... --model ...",
        "Add a provider directly (power-user flags).",
    ),
    ("/providers remove <name>", "Remove a provider by name."),
    ("/providers move <name> <pos>", "Reorder provider priority (1 = highest)."),
    ("/providers edit <name>", "Open pre-filled modal to edit a provider."),
    (
        "/providers edit <name> --field <val>",
        "Update a provider field directly (power-user flags).",
    ),
    ("/model [name]", "Show or switch the current LLM model."),
    ("/settings", "Open settings editor (runtime config)."),
    ("/setups", "List servers waiting for setup; fill one in or cancel it."),
    (
        "/tasks",
        "What JARVIS is doing now: requests, running tasks with their latest "
        "output, timers, and what waits on you.",
    ),
)

# Keys that are not represented as App BINDINGS but belong in the cheat sheet.
EXTRA_KEYBOARD_HELP: tuple[tuple[str, str], ...] = (
    ("Enter", "Send the message in the input line."),
    ("Tab", "Complete the `/command` being typed; press again to cycle matches."),
    ("Up / Down", "Walk previously sent messages (or the suggestion list)."),
    ("Ctrl+W", "Delete the word left of the cursor (Ctrl+Backspace where supported)."),
    ("Ctrl+Delete", "Delete the word right of the cursor."),
    ("Click / arrows", "Select a session in the sidebar."),
    (
        "In browse mode",
        "Up/Down move between blocks, Enter or Space folds or unfolds one, "
        "Esc goes back to typing. Clicking a block's title folds it too.",
    ),
    (
        "Esc",
        "Close the suggestion list; in the help, clear the search, then close it; "
        "on an empty input while JARVIS works, stop it (/stop).",
    ),
)


def _plain(text: str) -> str:
    """Drop the Markdown emphasis the tables carry, for plain-text surfaces."""
    return text.replace("**", "").replace("`", "")


def slash_command_catalog() -> tuple[tuple[str, str], ...]:
    """Bare ``/command`` names plus a one-line description, in doc order.

    Derived from the help tables above rather than restated, so completion and
    the suggestion dropdown cannot drift from what ``/help`` documents. Rows
    that only differ in their arguments (``/providers``, ``/providers add``, …)
    collapse onto the first, since only the command token is completable.
    """
    catalog: dict[str, str] = {}
    for column, description in TUI_LOCAL_SLASH_HELP + SESSION_SLASH_HELP:
        for alternative in column.split(","):
            name, _, usage = alternative.strip().partition(" ")
            if name.startswith("/") and name not in catalog:
                # The arguments lead the description: the list showed a bare
                # `/rename` with no hint that it needs a title.
                text = _plain(description)
                catalog[name] = f"{usage}  {text}" if usage else text
    return tuple(catalog.items())


def _cell(text: str) -> str:
    """Table-safe Markdown: `|` ended the cell ("/stop [all|<n>]" showed as
    "/stop [all" and "]"), and `<id>`, `<n>`, `<title>` were read as HTML
    tags and dropped. Code spans are left as written."""
    parts = text.split("`")
    for i in range(0, len(parts), 2):
        parts[i] = (
            parts[i].replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")
        )
    return "`".join(parts)


def _markdown_table(rows: Sequence[tuple[str, str]]) -> str:
    lines = ["| Key / command | Meaning |", "| --- | --- |"]
    for left, right in rows:
        lines.append(f"| **{_cell(left)}** | {_cell(right)} |")
    return "\n".join(lines)


def _binding_key_label(b: Binding) -> str:
    if b.key_display:
        return b.key_display
    # "ctrl+n" -> slightly nicer for markdown (still ASCII)
    return b.key.replace("_", " ")


def _matches(query: str, row: tuple[str, str]) -> bool:
    """True when every word of `query` is in the row, ignoring case."""
    text = f"{row[0]} {_plain(row[1])}".lower()
    return all(word in text for word in query.lower().split())


def build_help_markdown(bindings: Sequence[Binding], query: str = "") -> str:
    """Full markdown for the help modal (keyboard from live bindings + static tables).

    With a `query`, only the rows that mention all of its words (Yakup: "would
    be nice if we could search the commands in /help").
    """
    key_rows: list[tuple[str, str]] = [
        (_binding_key_label(b), b.description or b.action) for b in bindings
    ]
    key_rows.extend(EXTRA_KEYBOARD_HELP)

    slash_rows = list(TUI_LOCAL_SLASH_HELP) + list(SESSION_SLASH_HELP)

    if query.strip():
        key_rows = [r for r in key_rows if _matches(query, r)]
        slash_rows = [r for r in slash_rows if _matches(query, r)]
        sections = []
        if key_rows:
            sections.append("## Keyboard\n\n" + _markdown_table(key_rows))
        if slash_rows:
            sections.append("## Slash commands\n\n" + _markdown_table(slash_rows))
        if not sections:
            sections.append(f"Nothing mentions **{_plain(query.strip())}**.")
        return "\n\n".join(sections)

    return "\n\n".join(
        [
            "# JARVIS — help",
            "## Keyboard\n\n" + _markdown_table(key_rows),
            "Click a session in the sidebar or use **/switch** to change the active session.",
            "## Slash commands (input line)\n\n"
            "Session commands are handled locally (no LLM). Unknown `/…` is still sent to the model.\n\n"
            + _markdown_table(slash_rows),
        ]
    )
