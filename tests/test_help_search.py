"""Searching the help (2026-10-25, Yakup: "would be nice if we could search
the commands in the /? and /help commands").

The help window has a search line: typing keeps the keys and commands that
mention every word typed, and `/help <words>` opens it searching.
"""

import asyncio
from unittest.mock import patch

import pytest

from jarvis.tui.slash_commands_doc import build_help_markdown

textual = pytest.importorskip("textual")

from jarvis.tui.app import JarvisTUI  # noqa: E402

BINDINGS = JarvisTUI.BINDINGS


@pytest.mark.unit
class TestFilter:
    def test_only_what_mentions_the_words(self):
        text = build_help_markdown(BINDINGS, "stop")
        assert "**/stop [all|<n>]**" in text
        assert "**Esc**" in text  # "…while JARVIS works, stop it"
        assert "/export" not in text and "# JARVIS — help" not in text

    def test_every_word_must_match(self):
        text = build_help_markdown(BINDINGS, "session delete")
        assert "**/delete <id>**" in text
        assert "/new" not in text

    def test_case_and_markdown_do_not_matter(self):
        # "`all`" is code in the table; the search reads it as plain text.
        assert build_help_markdown(BINDINGS, "ALSO ENDS") == build_help_markdown(
            BINDINGS, "also ends"
        )
        assert "/stop" in build_help_markdown(BINDINGS, "also ends")

    def test_nothing_found(self):
        assert build_help_markdown(BINDINGS, "zzz") == "Nothing mentions **zzz**."

    def test_no_query_is_the_whole_help(self):
        assert build_help_markdown(BINDINGS, "  ") == build_help_markdown(BINDINGS)


def _tui(drive):
    from jarvis.tui import app as tui_app
    from jarvis.tui import lifecycle as tui_lifecycle

    async def no_unmount(app):
        return None

    async def run():
        app = tui_app.JarvisTUI()
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            return await drive(app, pilot)

    with (
        patch.object(tui_lifecycle, "on_mount", lambda app: None),
        patch.object(tui_lifecycle, "on_unmount", no_unmount),
    ):
        return asyncio.run(run())


def _shown(app):
    from textual.widgets import Markdown

    return app.screen.query_one("#help-md", Markdown).source


@pytest.mark.unit
def test_typing_in_the_help_searches_and_esc_clears_then_closes():
    from jarvis.tui.help_screen import HelpScreen

    async def drive(app, pilot):
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press(*"export")
        await pilot.pause()
        found = _shown(app)
        await pilot.press("escape")
        await pilot.pause()
        cleared = _shown(app)
        still_open = isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        return found, cleared, still_open, isinstance(app.screen, HelpScreen)

    found, cleared, still_open, open_after = _tui(drive)
    assert "/export" in found and "/switch" not in found
    assert cleared.startswith("# JARVIS — help")
    assert still_open and not open_after


@pytest.mark.unit
@pytest.mark.parametrize("command", ["/help stop", "/? stop"])
def test_help_with_words_opens_searching(command):
    from textual.widgets import Input

    async def drive(app, pilot):
        box = app.query_one("#input")
        box.focus()
        box.value = command
        await pilot.press("enter")
        await pilot.pause()
        return app.screen.query_one("#help-search", Input).value, _shown(app)

    value, shown = _tui(drive)
    assert value == "stop"
    assert "/stop" in shown and "/export" not in shown
