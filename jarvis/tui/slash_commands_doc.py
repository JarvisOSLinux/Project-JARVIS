"""
Session slash-command descriptions for the TUI help modal.

The **session** rows below must stay aligned with
``jarvis/runtime/session_commands.py::handle_slash_command`` (behavior lives there;
this module is the doc source for the help table only).

The **keyboard** section is built from ``JarvisTUI.BINDINGS`` plus a few lines that
are not real Textual bindings (Enter, mouse, help-modal Esc).
"""

from __future__ import annotations

from collections.abc import Sequence

from textual.binding import Binding

# (command column, meaning) — sync with jarvis/runtime/session_commands.py
SESSION_SLASH_HELP: tuple[tuple[str, str], ...] = (
    ("/new [title]", "Start a new session; optional title."),
    (
        "/sessions",
        "List sessions (current row marked with an asterisk in text output).",
    ),
    ("/switch <id>", "Switch session by short id prefix."),
    ("/rename <title>", "Rename the **current** session."),
    ("/delete <id>", "Delete session by **unique** id prefix."),
)

TUI_LOCAL_SLASH_HELP: tuple[tuple[str, str], ...] = (
    ("/help, /?", "Open help (TUI only; not sent to the LLM)."),
    (
        "/export [file]",
        "Save plain transcript (Markdown) under `transcripts/`; optional basename only.",
    ),
    ("/clear", "Clear the chat view (on-screen only; session memory unchanged)."),
    ("/quit, /exit", "Exit the TUI."),
    ("/status", "Show current provider, model, and session."),
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
)

# Keys that are not represented as App BINDINGS but belong in the cheat sheet.
EXTRA_KEYBOARD_HELP: tuple[tuple[str, str], ...] = (
    ("Enter", "Send the message in the input line."),
    ("Click / arrows", "Select a session in the sidebar."),
    ("Esc", "Close the help modal when it is focused."),
)


def _markdown_table(rows: Sequence[tuple[str, str]]) -> str:
    lines = ["| Key / command | Meaning |", "| --- | --- |"]
    for left, right in rows:
        lines.append(f"| **{left}** | {right} |")
    return "\n".join(lines)


def _binding_key_label(b: Binding) -> str:
    if b.key_display:
        return b.key_display
    # "ctrl+n" -> slightly nicer for markdown (still ASCII)
    return b.key.replace("_", " ")


def build_help_markdown(bindings: Sequence[Binding]) -> str:
    """Full markdown for the help modal (keyboard from live bindings + static tables)."""
    key_rows: list[tuple[str, str]] = [
        (_binding_key_label(b), b.description or b.action) for b in bindings
    ]
    key_rows.extend(EXTRA_KEYBOARD_HELP)

    slash_rows = list(TUI_LOCAL_SLASH_HELP) + list(SESSION_SLASH_HELP)

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
