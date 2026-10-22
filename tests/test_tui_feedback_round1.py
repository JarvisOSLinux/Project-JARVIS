"""TUI feedback round 1 (#269), from Yakup's testing on 2026-10-21.

- `/switch` changed the session inside JARVIS but left the previous session's
  messages on screen: only the sidebar reloaded the chat.
- The reply to `/switch 110` auto-named the session it switched to "/switch 110".
- `/rename d3 Yakup` failed: `/rename` took only a title, for the current session.
- TUI-only commands such as `/status` were not echoed.
- Suggestions showed a bare `/rename`, with no hint it needs a title.
- The settings window's buttons were clipped at the bottom.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from jarvis.runtime import session_commands
from jarvis.sessions.manager import SessionManager


class FakeContextor:
    """Sessions and conversation entries in memory, shaped like the adapter's replies."""

    is_connected = True
    supports_sessions = True

    def __init__(self):
        self.sessions = {}
        self.entries = {}

    def _add(self, sid, title):
        self.sessions[sid] = {"id": sid, "title": title}
        return self.sessions[sid]

    def create_session(self, title=None):
        sid = f"{len(self.sessions) + 1:08x}-new"
        return {"session": self._add(sid, title or "New chat (9)")}

    def get_session(self, session_id):
        found = self.sessions.get(session_id)
        return {"session": found} if found else {"error": "not found"}

    def list_sessions(self, limit=50, offset=0):
        return {"sessions": list(self.sessions.values())}

    def update_session(self, session_id, title):
        self.sessions[session_id]["title"] = title
        return {"session": self.sessions[session_id]}

    def delete_session(self, session_id, delete_entries=True):
        self.sessions.pop(session_id, None)
        return {}

    def recall(self, theme, limit=100, session_id=None):
        return {"entries": list(self.entries.get(session_id, []))}


def _manager():
    ctx = FakeContextor()
    ctx._add("110d5048-aaaa", "Pacman test")
    ctx._add("d32236e3-bbbb", "New chat (3)")
    ctx.entries["110d5048-aaaa"] = [
        {
            "content": "run pacman -Syu",
            "stored_at": "1",
            "metadata": {"type": "user_prompt"},
        },
        {
            "content": "It is still running.",
            "stored_at": "2",
            "metadata": {"type": "assistant_reply"},
        },
    ]
    return SessionManager(ctx), ctx


@pytest.mark.unit
class TestSwitchAnnouncements:
    def test_every_move_to_another_session_is_announced(self):
        sessions, _ = _manager()
        seen = []
        sessions.on_switch = lambda s: seen.append(s.id[:8] if s else None)
        sessions.switch("110d5048-aaaa")  # by id
        sessions.switch("d32")  # by prefix
        sessions.new_session()
        current = sessions.current_id
        sessions.delete("110d5048-aaaa")  # not the current one: no move
        sessions.delete(current)  # the current one
        assert seen == ["110d5048", "d32236e3", "00000003", None]

    def test_a_first_message_starting_a_session_is_not_a_switch(self):
        sessions, _ = _manager()
        seen = []
        sessions.on_switch = seen.append
        assert sessions.ensure_session() is not None
        sessions.rename("Renamed")
        assert seen == []

    def test_a_failing_listener_does_not_break_the_switch(self):
        sessions, _ = _manager()

        def boom(_session):
            raise RuntimeError("listener bug")

        sessions.on_switch = boom
        assert sessions.switch("110d5048-aaaa").id == "110d5048-aaaa"


def _app(sessions):
    return SimpleNamespace(sessions=sessions, output_manager=Mock())


def _said(app):
    return app.output_manager.handle_response.call_args.args[0]["output"]


@pytest.mark.unit
class TestRename:
    def test_an_id_first_renames_that_session(self):
        sessions, ctx = _manager()
        app = _app(sessions)
        session_commands.handle_slash_command(app, "/rename d3 'Yakup'")
        assert ctx.sessions["d32236e3-bbbb"]["title"] == "Yakup"
        assert _said(app) == "Renamed d32236e3 to 'Yakup'."

    def test_without_an_id_it_renames_the_current_session(self):
        sessions, ctx = _manager()
        sessions.switch("110d5048-aaaa")
        app = _app(sessions)
        # "fix" starts no session id, so the whole text is the title.
        session_commands.handle_slash_command(app, "/rename fix the build")
        assert ctx.sessions["110d5048-aaaa"]["title"] == "fix the build"

    def test_with_no_session_it_says_what_to_do(self):
        sessions, ctx = _manager()
        app = _app(sessions)
        session_commands.handle_slash_command(app, "/rename Yakup")
        assert "/rename <id> <title>" in _said(app) and "/switch <id>" in _said(app)
        assert all(s["title"] != "Yakup" for s in ctx.sessions.values())


@pytest.mark.unit
def test_suggestions_show_the_arguments():
    from jarvis.tui.slash_commands_doc import slash_command_catalog

    catalog = dict(slash_command_catalog())
    assert catalog["/rename"].startswith("[<id>] <title>")
    assert catalog["/switch"].startswith("<id>")
    assert not catalog["/status"].startswith("<")


# --- the TUI ---------------------------------------------------------------------------

textual = pytest.importorskip("textual")


def _real_tui(jarvis, drive):
    """Drive the real JarvisTUI with its engine startup stubbed out."""
    from jarvis.tui import app as tui_app
    from jarvis.tui import lifecycle as tui_lifecycle

    async def no_unmount(app):
        return None

    async def run():
        app = tui_app.JarvisTUI()
        app.jarvis = jarvis
        if jarvis is not None and hasattr(jarvis, "sessions"):
            jarvis.sessions.on_switch = app._on_session_switch
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            result = await drive(app, pilot)
            await pilot.pause(0.2)
            return app, result

    with (
        patch.object(tui_lifecycle, "on_mount", lambda app: None),
        patch.object(tui_lifecycle, "on_unmount", no_unmount),
    ):
        return asyncio.run(run())


def _jarvis():
    sessions, ctx = _manager()
    return SimpleNamespace(
        sessions=sessions,
        contextor=ctx,
        events=SimpleNamespace(inject_user_input=Mock()),
        output_manager=Mock(),
    )


@pytest.mark.unit
class TestTui:
    def test_switching_by_command_shows_that_sessions_messages(self):
        jarvis = _jarvis()

        async def drive(app, pilot):
            app._append_log("old line from another session")
            # What `/switch 110` does inside JARVIS.
            jarvis.sessions.switch("110")
            await pilot.pause()
            return list(app._export_lines)

        _, lines = _real_tui(jarvis, drive)
        assert "old line from another session" not in lines
        assert lines[0].startswith("— 110d5048 ('Pacman test')")
        assert "you > run pacman -Syu" in lines
        assert "jarvis > It is still running." in lines

    def test_deleting_the_current_session_says_the_next_message_starts_one(self):
        jarvis = _jarvis()

        async def drive(app, pilot):
            jarvis.sessions.switch("110d5048-aaaa")
            jarvis.sessions.delete("110d5048-aaaa")
            await pilot.pause()
            return list(app._export_lines)

        _, lines = _real_tui(jarvis, drive)
        assert lines == ["— no session; your next message starts a new one —"]

    def test_typed_commands_are_echoed_and_never_name_a_session(self):
        jarvis = _jarvis()

        async def drive(app, pilot):
            box = app.query_one("#input")
            box.focus()
            box.value = "/status"
            await pilot.press("enter")
            await pilot.pause()
            after_status = app._pending_autoname_text
            box.value = "/switch 110"
            await pilot.press("enter")
            await pilot.pause()
            after_switch = app._pending_autoname_text
            box.value = "what's on my calendar"
            await pilot.press("enter")
            await pilot.pause()
            return (
                list(app._export_lines),
                after_status,
                after_switch,
                app._pending_autoname_text,
            )

        _, (lines, after_status, after_switch, after_text) = _real_tui(jarvis, drive)
        assert "you > /status" in lines
        assert after_status is None and after_switch is None
        assert after_text == "what's on my calendar"

    def test_the_settings_buttons_fit_inside_the_window(self):
        from textual.app import App
        from textual.widgets import Button

        from jarvis.tui.config_modal import ConfigModal

        class Host(App):
            async def on_mount(self):
                self.push_screen(ConfigModal(initial_tab="settings"))

        async def run(size):
            app = Host()
            async with app.run_test(size=size) as pilot:
                await pilot.pause(0.3)
                dialog = app.screen.query_one("#config-dialog")
                return [
                    (b.region.height, b.region.bottom <= dialog.region.bottom - 1)
                    for b in app.screen.query(Button)
                    if b.id in ("btn-cancel", "btn-save-settings")
                ]

        for size in ((80, 24), (90, 40), (120, 60)):
            assert asyncio.run(run(size)) == [(3, True), (3, True)], size
