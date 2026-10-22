"""Tests for the TUI message line (Project-JARVIS #130-#133).

#130 Tab completion, #131 suggestion dropdown filtering, #132 Up/Down history,
#133 Ctrl+Backspace / Ctrl+Delete word deletion.

``jarvis/tui/command_input.py`` is a thin Textual shell over the state machines
in ``jarvis/tui/input_helpers.py``; the helpers carry no Textual dependency, so
the behaviour is exercised here directly rather than through a running app.
"""

import pytest

from jarvis.tui.input_helpers import (
    CommandCompleter,
    InputHistory,
    delete_word_left,
    delete_word_right,
    filter_commands,
    is_command_prefix,
    match_commands,
    suggestion_label,
)
from jarvis.tui.slash_commands_doc import (
    SESSION_SLASH_HELP,
    TUI_LOCAL_SLASH_HELP,
    slash_command_catalog,
)

CATALOG = slash_command_catalog()
COMMANDS = [name for name, _ in CATALOG]

# Fixed list for the pure state-machine tests, so they do not move when a new
# slash-command is documented.
SAMPLE = ["/help", "/export", "/exit", "/status", "/settings"]


@pytest.mark.unit
class TestCommandCatalog:
    """The catalog is derived from the help tables, never restated."""

    def test_every_command_comes_from_a_documented_row(self):
        documented = {
            alternative.strip().split(" ", 1)[0]
            for column, _ in TUI_LOCAL_SLASH_HELP + SESSION_SLASH_HELP
            for alternative in column.split(",")
        }
        assert set(COMMANDS) <= documented

    def test_every_documented_command_is_completable(self):
        for column, _ in TUI_LOCAL_SLASH_HELP + SESSION_SLASH_HELP:
            for alternative in column.split(","):
                name = alternative.strip().split(" ", 1)[0]
                assert name in COMMANDS

    def test_argument_variants_collapse_onto_one_entry(self):
        assert COMMANDS.count("/providers") == 1
        assert len(COMMANDS) == len(set(COMMANDS))

    def test_covers_both_handler_families(self):
        # TUI-local (local_input.py) and session (runtime/session_commands.py).
        for name in ("/status", "/settings", "/new", "/switch", "/context"):
            assert name in COMMANDS

    def test_aliases_are_listed_separately(self):
        for name in ("/help", "/?", "/quit", "/exit"):
            assert name in COMMANDS

    def test_descriptions_are_plain_text(self):
        for name, description in CATALOG:
            assert description, f"{name} has no description"
            assert "**" not in description
            assert "`" not in description


@pytest.mark.unit
class TestIsCommandPrefix:
    def test_bare_slash_token(self):
        assert is_command_prefix("/")
        assert is_command_prefix("/pro")

    def test_plain_text_is_not_a_command(self):
        assert not is_command_prefix("")
        assert not is_command_prefix("hello")
        assert not is_command_prefix("what is /pro")

    def test_a_space_settles_the_command(self):
        assert not is_command_prefix("/new title")
        assert not is_command_prefix("/help ")


@pytest.mark.unit
class TestTabCompletion:
    """#130 — Tab completes, repeated Tab cycles, typing resets.

    #269: a completion ends in a space, where the arguments go; Tab cycles
    while the line is exactly what Tab produced, and sending strips it.
    """

    def test_unique_prefix_completes_from_the_real_catalog(self):
        completer = CommandCompleter(COMMANDS)
        assert completer.complete("/pro") == "/providers "

        completer = CommandCompleter(COMMANDS)
        assert completer.complete("/he") == "/help "

    def test_unique_match_is_stable_on_repeated_tab(self):
        completer = CommandCompleter(SAMPLE)
        assert completer.complete("/st") == "/status "
        assert completer.complete("/status ") == "/status "

    def test_multiple_matches_cycle_and_wrap(self):
        completer = CommandCompleter(SAMPLE)
        assert completer.complete("/e") == "/export "
        assert completer.complete("/export ") == "/exit "
        assert completer.complete("/exit ") == "/export "

    def test_cycle_follows_catalog_order(self):
        completer = CommandCompleter(SAMPLE)
        seen = [completer.complete("/s")]
        for _ in range(3):
            seen.append(completer.complete(seen[-1]))
        assert seen == ["/status ", "/settings ", "/status ", "/settings "]

    def test_no_match_is_a_noop(self):
        completer = CommandCompleter(SAMPLE)
        assert completer.complete("/zzz") is None
        assert completer.complete("/zzz") is None

    def test_plain_text_is_never_completed(self):
        completer = CommandCompleter(SAMPLE)
        assert completer.complete("hello") is None
        assert completer.complete("") is None
        assert completer.complete("/new title") is None

    def test_typing_resets_the_cycle(self):
        completer = CommandCompleter(SAMPLE)
        assert completer.complete("/e") == "/export "
        # The user keeps typing instead of pressing Tab again.
        assert completer.complete("/ex") == "/export "
        assert completer.complete("/export ") == "/exit "

    def test_explicit_reset_starts_over(self):
        completer = CommandCompleter(SAMPLE)
        assert completer.complete("/e") == "/export "
        completer.reset()
        # The command is settled (a space follows it): Tab has nothing to do.
        assert completer.complete("/export ") is None
        assert completer.complete("/export") == "/export "

    def test_completion_is_case_insensitive(self):
        completer = CommandCompleter(COMMANDS)
        assert completer.complete("/PRO") == "/providers "

    def test_match_commands_returns_catalog_order(self):
        assert match_commands("/e", SAMPLE) == ["/export", "/exit"]
        assert match_commands("/", SAMPLE) == SAMPLE
        assert match_commands("hello", SAMPLE) == []


@pytest.mark.unit
class TestSuggestionFilter:
    """#131 — what the dropdown shows for the text typed so far."""

    def test_slash_lists_every_command(self):
        assert filter_commands("/", CATALOG) == list(CATALOG)

    def test_filters_as_the_user_types(self):
        assert [name for name, _ in filter_commands("/pro", CATALOG)] == ["/providers"]
        assert [name for name, _ in filter_commands("/se", CATALOG)] == [
            "/settings",
            "/setups",
            "/sessions",
        ]

    def test_rows_carry_descriptions(self):
        ((name, description),) = filter_commands("/pro", CATALOG)
        assert name == "/providers"
        assert "provider" in description.lower()

    def test_unknown_prefix_shows_nothing(self):
        assert filter_commands("/zzz", CATALOG) == []

    def test_plain_text_shows_nothing(self):
        assert filter_commands("hello", CATALOG) == []
        assert filter_commands("", CATALOG) == []

    def test_arguments_dismiss_the_list(self):
        assert filter_commands("/providers add", CATALOG) == []

    def test_filter_is_case_insensitive(self):
        assert filter_commands("/PRO", CATALOG) == filter_commands("/pro", CATALOG)

    def test_rows_are_one_line_with_an_aligned_command_column(self):
        labels = [suggestion_label(name, description) for name, description in CATALOG]
        assert all("\n" not in label for label in labels)
        # Every catalog command fits the name column, so all the descriptions
        # start at the same offset and read as a table.
        assert {label[14:] for label in labels} == {
            description for _, description in CATALOG
        }

    def test_a_long_command_name_keeps_its_description(self):
        assert (
            suggestion_label("/a-very-long-command", "Do a thing.")
            == "/a-very-long-command  Do a thing."
        )


@pytest.mark.unit
class TestInputHistory:
    """#132 — Up/Down through submitted messages, with the draft stashed."""

    def _history(self, *entries):
        history = InputHistory()
        for entry in entries:
            history.record(entry)
        return history

    def test_up_walks_back_and_down_walks_forward(self):
        history = self._history("first", "second", "third")
        assert history.previous("") == "third"
        assert history.previous("") == "second"
        assert history.next() == "third"

    def test_down_past_the_newest_restores_the_draft(self):
        history = self._history("first", "second")
        assert history.previous("half-typed") == "second"
        assert history.previous("ignored") == "first"
        assert history.next() == "second"
        assert history.next() == "half-typed"
        assert not history.navigating

    def test_draft_restore_can_be_empty_but_is_not_none(self):
        history = self._history("first")
        assert history.previous("") == "first"
        assert history.next() == ""

    def test_empty_history_is_a_noop(self):
        history = InputHistory()
        assert history.previous("draft") is None
        assert history.next() is None

    def test_down_without_navigating_is_a_noop(self):
        history = self._history("first")
        assert history.next() is None

    def test_up_stops_at_the_oldest_entry(self):
        history = self._history("first", "second")
        assert history.previous("") == "second"
        assert history.previous("") == "first"
        assert history.previous("") == "first"

    def test_blank_submissions_are_not_recorded(self):
        history = self._history("first", "", "   ")
        assert history.entries == ("first",)

    def test_consecutive_duplicates_are_not_recorded(self):
        history = self._history("same", "same", "other", "same")
        assert history.entries == ("same", "other", "same")

    def test_entries_are_stripped(self):
        history = self._history("  padded  ")
        assert history.entries == ("padded",)

    def test_recording_leaves_navigation_mode(self):
        history = self._history("first", "second")
        assert history.previous("draft") == "second"
        history.record("third")
        assert not history.navigating
        assert history.next() is None
        assert history.previous("") == "third"

    def test_reset_discards_the_stashed_draft(self):
        history = self._history("first")
        assert history.previous("draft") == "first"
        history.reset()
        assert history.next() is None

    def test_oldest_entries_drop_past_the_limit(self):
        history = InputHistory(limit=2)
        for entry in ("a", "b", "c"):
            history.record(entry)
        assert history.entries == ("b", "c")


@pytest.mark.unit
class TestWordDeletion:
    """#133 — word-wise delete left/right around the cursor."""

    def test_issue_example_delete_word_left(self):
        # 'hello world|' + Ctrl+Backspace -> 'hello |'
        assert delete_word_left("hello world", 11) == ("hello ", 6)

    def test_issue_example_delete_word_right(self):
        # '|hello world' + Ctrl+Delete -> '| world'
        assert delete_word_right("hello world", 0) == (" world", 0)

    def test_left_skips_trailing_whitespace_first(self):
        assert delete_word_left("hello world   ", 14) == ("hello ", 6)

    def test_right_eats_leading_whitespace_with_the_word(self):
        assert delete_word_right("hello   world", 5) == ("hello", 5)

    def test_left_treats_punctuation_as_its_own_word(self):
        text, cursor = delete_word_left("foo.bar", 7)
        assert (text, cursor) == ("foo.", 4)
        assert delete_word_left(text, cursor) == ("foo", 3)

    def test_right_treats_punctuation_as_its_own_word(self):
        text, cursor = delete_word_right("foo.bar", 0)
        assert (text, cursor) == (".bar", 0)
        assert delete_word_right(text, cursor) == ("bar", 0)

    def test_underscores_and_digits_stay_inside_one_word(self):
        assert delete_word_left("keep tmp_file2", 14) == ("keep ", 5)

    def test_left_preserves_the_tail_after_the_cursor(self):
        assert delete_word_left("alpha beta gamma", 10) == ("alpha  gamma", 6)

    def test_right_preserves_the_head_before_the_cursor(self):
        assert delete_word_right("alpha beta gamma", 6) == ("alpha  gamma", 6)

    def test_start_of_line_is_a_noop_for_left(self):
        assert delete_word_left("hello", 0) == ("hello", 0)
        assert delete_word_left("", 0) == ("", 0)

    def test_end_of_line_is_a_noop_for_right(self):
        assert delete_word_right("hello", 5) == ("hello", 5)
        assert delete_word_right("", 0) == ("", 0)

    def test_leading_whitespace_only_is_cleared(self):
        assert delete_word_left("   ", 3) == ("", 0)
        assert delete_word_right("   ", 0) == ("", 0)

    def test_out_of_range_cursor_is_clamped(self):
        assert delete_word_left("hello", 99) == ("", 0)
        assert delete_word_right("hello", -5) == ("", 0)


@pytest.mark.unit
class TestCommandInputBindings:
    """#133 — the keys actually wired up, given what terminals deliver."""

    def _bindings(self):
        pytest.importorskip("textual")
        from jarvis.tui.command_input import CommandInput

        actions = {}
        for binding in CommandInput.BINDINGS:
            for key in binding.key.split(","):
                actions[key.strip()] = binding.action
        return actions

    def test_word_deletion_keys(self):
        actions = self._bindings()
        # ctrl+w is the fallback every terminal delivers; ctrl+backspace and
        # ctrl+delete only arrive distinctly under the Kitty keyboard protocol.
        for key in ("ctrl+w", "ctrl+backspace", "alt+backspace"):
            assert actions[key] == "delete_word_left"
        for key in ("ctrl+delete", "alt+d"):
            assert actions[key] == "delete_word_right"

    def test_plain_backspace_is_left_alone(self):
        # Legacy terminals send 0x08 for Ctrl+Backspace, which Textual reports
        # as plain `backspace` — rebinding it would eat a word per keystroke.
        assert "backspace" not in self._bindings()

    def test_completion_and_history_keys(self):
        actions = self._bindings()
        assert actions["tab"] == "complete"
        assert actions["up"] == "history_prev"
        assert actions["down"] == "history_next"
        assert actions["escape"] == "close_dropdown"
