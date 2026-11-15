"""Attaching images and PDFs in the TUI: `/attach`, the picker, a dropped file.

A terminal pastes a dropped file as its path, so a paste that is nothing but
attachable files attaches them. Attached files wait above the input and go
with the next message, not with a command.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from jarvis.config import Config
from jarvis.runtime import attachments
from jarvis.tui.input_helpers import parse_dropped_paths
from jarvis.tui.local_input import handle_local_input

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(Config, "JARVIS_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def files(tmp_path):
    """A PNG, a PDF, a text file, and a PNG in a folder whose name has a space."""
    (tmp_path / "my pics").mkdir()
    made = {
        "png": tmp_path / "a.png",
        "pdf": tmp_path / "report.PDF",
        "txt": tmp_path / "notes.txt",
        "spaced": tmp_path / "my pics" / "b c.png",
    }
    for path in made.values():
        path.write_bytes(b"%PDF-1.4\n" if path.suffix == ".PDF" else PNG)
    made["txt"].write_text("hello")
    return made


@pytest.mark.unit
class TestParseDroppedPaths:
    def test_a_plain_path(self, files):
        assert parse_dropped_paths(str(files["png"])) == [files["png"]]

    def test_several_paths_with_the_trailing_space_terminals_add(self, files):
        text = f"{files['png']} {files['pdf']} "
        assert parse_dropped_paths(text) == [files["png"], files["pdf"]]

    def test_quoted_and_backslash_escaped_spaces(self, files):
        spaced = str(files["spaced"])
        assert parse_dropped_paths(f"'{spaced}'") == [files["spaced"]]
        assert parse_dropped_paths(f'"{spaced}"') == [files["spaced"]]
        assert parse_dropped_paths(spaced.replace(" ", "\\ ")) == [files["spaced"]]

    def test_file_uris_are_percent_decoded(self, files):
        uri = "file://" + str(files["spaced"]).replace(" ", "%20")
        assert parse_dropped_paths(uri) == [files["spaced"]]
        lines = f"file://{files['png']}\nfile://{files['pdf']}\n"
        assert parse_dropped_paths(lines) == [files["png"], files["pdf"]]

    def test_anything_else_is_text(self, files, tmp_path):
        for text in (
            "",
            "   ",
            "look at this",
            f"look at {files['png']}",
            str(files["txt"]),
            str(tmp_path / "missing.png"),
            str(tmp_path / "my pics"),
            f"'{files['png']}",  # unclosed quote
            f"{files['png']} {files['txt']}",
            # expanduser raised RuntimeError on these, which closed the TUI.
            "~5 minutes ago",
            "~nouserxyz/a.png",
        ):
            assert parse_dropped_paths(text) is None, text


class FakeApp:
    """What the /attach handler touches on the TUI app."""

    def __init__(self):
        self._pending_attachments = []
        self.log = []
        self.screens = []

    def _append_log(self, markup):
        self.log.append(markup)

    def push_screen(self, screen, callback=None):
        self.screens.append((screen, callback))

    def query_one(self, *_args):
        raise LookupError("no widgets in this fake")


@pytest.mark.unit
class TestAttachCommand:
    def test_a_path_is_attached(self, files):
        app = FakeApp()
        assert handle_local_input(app, f"/attach {files['png']}", ())
        (att,) = app._pending_attachments
        assert (att.name, att.kind) == ("a.png", "image")
        assert att.path.read_bytes() == PNG
        assert app.log == []

    def test_quotes_and_a_tilde_are_understood(self, files, monkeypatch):
        monkeypatch.setenv("HOME", str(files["spaced"].parent.parent))
        app = FakeApp()
        handle_local_input(app, "/attach '~/my pics/b c.png'", ())
        assert [a.name for a in app._pending_attachments] == ["b c.png"]

    def test_a_file_of_another_type_says_why(self, files):
        app = FakeApp()
        handle_local_input(app, f"/attach {files['txt']}", ())
        assert app._pending_attachments == []
        assert app.log[0].startswith("[red]notes.txt is not a PNG, JPEG")

    def test_a_file_too_big_says_why(self, files, monkeypatch):
        monkeypatch.setattr(attachments, "MAX_BYTES", 10)
        app = FakeApp()
        handle_local_input(app, f"/attach {files['png']}", ())
        assert app._pending_attachments == []
        assert "an attachment can be at most" in app.log[0]

    def test_a_missing_file_says_why(self, tmp_path):
        app = FakeApp()
        handle_local_input(app, f"/attach {tmp_path / 'gone.png'}", ())
        assert app.log == [f"[red]{tmp_path / 'gone.png'} is not a file.[/red]"]

    def test_clear_deletes_what_was_attached(self, files):
        app = FakeApp()
        handle_local_input(app, f"/attach {files['png']}", ())
        stored = app._pending_attachments[0].path
        handle_local_input(app, "/attach clear", ())
        assert app._pending_attachments == []
        assert not stored.exists()
        assert app.log == ["[dim]Dropped 1 attachment(s).[/dim]"]
        handle_local_input(app, "/ATTACH CLEAR", ())
        assert app.log[-1] == "[dim]Nothing is attached.[/dim]"

    def test_a_message_holds_at_most_the_daemons_limit(self, files, monkeypatch):
        monkeypatch.setattr(attachments, "MAX_PER_MESSAGE", 2)
        app = FakeApp()
        for _ in range(3):
            handle_local_input(app, f"/attach {files['png']}", ())
        assert len(app._pending_attachments) == 2
        assert app.log == ["[red]A message can carry at most 2 attachments.[/red]"]

    def test_no_path_opens_the_picker_and_its_choice_is_attached(self, files):
        from jarvis.tui.attach_picker import AttachPicker

        app = FakeApp()
        handle_local_input(app, "/attach", ())
        ((screen, picked),) = app.screens
        assert isinstance(screen, AttachPicker)
        picked(None)
        assert app._pending_attachments == []
        picked(files["png"])
        assert [a.name for a in app._pending_attachments] == ["a.png"]


# --- in the running TUI ------------------------------------------------------------

textual = pytest.importorskip("textual")


def _tui(drive):
    from jarvis.tui import app as tui_app
    from jarvis.tui import lifecycle as tui_lifecycle

    async def no_unmount(app):
        return None

    async def run():
        app = tui_app.JarvisTUI()
        app.jarvis = SimpleNamespace(events=SimpleNamespace(inject_user_input=Mock()))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return app, await drive(app, pilot)

    with (
        patch.object(tui_lifecycle, "on_mount", lambda app: None),
        patch.object(tui_lifecycle, "on_unmount", no_unmount),
        patch("jarvis.runtime.io.on_user_message") as told,
    ):
        app, result = asyncio.run(run())
        return app, result, told


async def _send(app, pilot, text):
    box = app.query_one("#input")
    box.focus()
    box.value = text
    await pilot.press("enter")
    await pilot.pause()


def _attached_line(app):
    from textual.widgets import Static

    line = app.query_one("#attached", Static)
    return "" if line.has_class("-idle") else str(line.render())


@pytest.mark.unit
class TestTuiAttach:
    def test_a_message_carries_what_is_attached(self, files):
        async def drive(app, pilot):
            await _send(app, pilot, f"/attach {files['png']}")
            shown = _attached_line(app)
            pending = list(app._pending_attachments)
            await _send(app, pilot, "what is this?")
            return shown, pending, _attached_line(app)

        app, (shown, pending, after), told = _tui(drive)
        assert shown == (
            "Attached: a.png — sent with your next message; /attach clear drops them"
        )
        inject = app.jarvis.events.inject_user_input
        inject.assert_called_once_with("what is this?", attachments=pending)
        told.assert_called_once_with(app.jarvis, "what is this?", attachments=pending)
        assert "you > what is this?\nAttached: a.png" in app._export_lines
        assert app._pending_attachments == [] and after == ""

    def test_enter_on_an_empty_line_sends_the_files_alone(self, files):
        async def drive(app, pilot):
            await _send(app, pilot, f"/attach {files['pdf']}")
            pending = list(app._pending_attachments)
            await _send(app, pilot, "")
            return pending

        app, pending, _ = _tui(drive)
        app.jarvis.events.inject_user_input.assert_called_once_with(
            "", attachments=pending
        )
        assert "you > Attached: report.pdf" in app._export_lines

    def test_a_command_leaves_them_for_the_next_message(self, files):
        async def drive(app, pilot):
            await _send(app, pilot, f"/attach {files['png']}")
            await _send(app, pilot, "/sessions")
            return list(app._pending_attachments)

        app, pending, _ = _tui(drive)
        app.jarvis.events.inject_user_input.assert_called_once_with(
            "/sessions", attachments=[]
        )
        assert [a.name for a in pending] == ["a.png"]

    def test_an_unknown_slash_line_is_a_message_and_takes_them(self, files):
        """As in jarvis-app: the daemon gives an unknown /x to the model."""

        async def drive(app, pilot):
            await _send(app, pilot, f"/attach {files['png']}")
            pending = list(app._pending_attachments)
            await _send(app, pilot, "/foo bar")
            return pending

        app, pending, _ = _tui(drive)
        app.jarvis.events.inject_user_input.assert_called_once_with(
            "/foo bar", attachments=pending
        )
        assert app._pending_attachments == []

    def test_a_paste_starting_with_a_tilde_is_pasted(self):
        from textual import events

        async def drive(app, pilot):
            box = app.query_one("#input")
            box.focus()
            box.post_message(events.Paste("~5 minutes ago"))
            await pilot.pause()
            return box.value, app.is_running

        _, (pasted, running), _ = _tui(drive)
        assert (pasted, running) == ("~5 minutes ago", True)

    def test_enter_on_an_empty_line_with_nothing_attached_sends_nothing(self):
        async def drive(app, pilot):
            await _send(app, pilot, "")

        app, _, _ = _tui(drive)
        app.jarvis.events.inject_user_input.assert_not_called()

    def test_a_dropped_file_is_attached_not_typed(self, files):
        from textual import events

        async def drive(app, pilot):
            box = app.query_one("#input")
            box.focus()
            box.post_message(events.Paste(f"'{files['spaced']}' "))
            await pilot.pause()
            dropped = (box.value, [a.name for a in app._pending_attachments])
            box.post_message(events.Paste("just text"))
            await pilot.pause()
            return dropped, box.value

        _, (dropped, pasted), _ = _tui(drive)
        assert dropped == ("", ["b c.png"])
        assert pasted == "just text"


@pytest.mark.unit
def test_the_picker_offers_folders_and_attachable_files_only(tmp_path):
    from textual.app import App

    from jarvis.tui.attach_picker import AttachableTree, AttachPicker

    tmp_path = tmp_path / "home"
    tmp_path.mkdir()
    for name in ("a.png", "B.PDF", "c.txt", ".hidden.png"):
        (tmp_path / name).write_bytes(PNG)
    (tmp_path / "folder").mkdir()
    (tmp_path / ".config").mkdir()
    chosen = []

    class Host(App):
        def on_mount(self):
            self.push_screen(AttachPicker(tmp_path), chosen.append)

    async def run():
        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            tree = app.screen.query_one(AttachableTree)
            labels = [str(node.label) for node in tree.root.children]
            await pilot.press("down", "down", "enter")  # past ~ and the folder
            await pilot.pause(0.2)
            return labels

    assert asyncio.run(run()) == ["folder", "a.png", "B.PDF"]
    assert chosen == [tmp_path / "a.png"]


@pytest.mark.unit
def test_esc_closes_the_picker_with_nothing(tmp_path):
    from textual.app import App

    from jarvis.tui.attach_picker import AttachPicker

    chosen = []

    class Host(App):
        def on_mount(self):
            self.push_screen(AttachPicker(tmp_path), chosen.append)

    async def run():
        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            await pilot.press("escape")
            await pilot.pause()
            return len(app.screen_stack)

    assert asyncio.run(run()) == 1
    assert chosen == [None]


@pytest.mark.unit
def test_quitting_deletes_what_was_attached_and_never_sent(files):
    from jarvis.tui import lifecycle as tui_lifecycle

    pending = attachments.store_file(files["png"])
    app = SimpleNamespace(
        _pending_attachments=[pending], jarvis=None, _jarvis_task=None
    )
    asyncio.run(tui_lifecycle.on_unmount(app))
    assert not pending.path.exists()
