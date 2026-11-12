"""One way to name a session by its id (2026-10-24, Yakup).

/switch took the first id that matched, /delete and /rename wanted the start
of exactly one id, the GUI socket passed ids through, and all of them said
something different when nothing matched: "/switch 5d" answered "No session
matches '5d'" where the session meant was 5bd5f76e. `SessionManager.find`
is now the one rule, and its answer says what to type instead.

Also: a session the first message starts has no title yet, and the TUI only
auto-named titles reading "New chat", so the name went to a later message
("switch 5d").
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvis.runtime import io, session_commands
from jarvis.sessions.manager import SessionManager
from tests.test_tui_feedback_round1 import FakeContextor


def _sessions():
    ctx = FakeContextor()
    for sid, title in [
        ("5bd5f76e-1111", "Update"),
        ("6d9c6d35-2222", "Programming"),
        ("6d9a0000-3333", "Other"),
        ("c1f1aaf8-4444", "New chat"),
    ]:
        ctx._add(sid, title)
    return SessionManager(ctx), ctx


@pytest.mark.unit
class TestFind:
    def test_a_unique_start_or_the_full_id(self):
        sessions, _ = _sessions()
        assert sessions.find("5b")[0].id == "5bd5f76e-1111"
        assert sessions.find("c1f1aaf8-4444")[0].id == "c1f1aaf8-4444"

    def test_an_ambiguous_start_lists_the_candidates(self):
        sessions, _ = _sessions()
        session, why = sessions.find("6d9")
        assert session is None
        assert (
            why
            == "'6d9' starts 2 session ids (6d9c6d35, 6d9a0000); type more of the id."
        )

    def test_a_near_miss_suggests_what_was_meant(self):
        sessions, _ = _sessions()
        session, why = sessions.find("5d")
        assert session is None
        assert why == (
            "No session id starts with '5d'. "
            "Did you mean 5bd5f76e or 6d9c6d35 or 6d9a0000?"
        )

    def test_nothing_close(self):
        sessions, _ = _sessions()
        assert sessions.find("zz") == (None, "No session id starts with 'zz'.")
        assert sessions.find(" ")[0] is None


def _app():
    sessions, ctx = _sessions()
    return SimpleNamespace(sessions=sessions, output_manager=Mock()), ctx


def _said(app):
    return app.output_manager.handle_response.call_args.args[0]["output"]


@pytest.mark.unit
class TestCommands:
    def test_switch_delete_and_rename_take_the_same_ids(self):
        app, ctx = _app()
        session_commands.handle_slash_command(app, "/switch 5b")
        assert _said(app) == "Switched to 5bd5f76e ('Update')"
        session_commands.handle_slash_command(app, "/rename c1f Plans")
        assert ctx.sessions["c1f1aaf8-4444"]["title"] == "Plans"
        session_commands.handle_slash_command(app, "/delete 6d9c")
        assert "6d9c6d35-2222" not in ctx.sessions

    def test_every_command_explains_a_miss_the_same_way(self):
        app, _ = _app()
        for cmd in ("/switch 6d9", "/delete 6d9"):
            session_commands.handle_slash_command(app, cmd)
            assert _said(app).startswith("'6d9' starts 2 session ids")

    def test_the_socket_uses_the_same_rule(self):
        app, ctx = _app()
        app.contextor = SimpleNamespace(recall=lambda *a, **k: {"entries": []})
        reply = io._handle_switch_session(app, {"id": "5d"})
        assert reply["type"] == "session_error"
        assert reply["message"].startswith(
            "No session id starts with '5d'. Did you mean"
        )
        assert (
            io._handle_switch_session(app, {"id": "5b"})["type"] == "session_switched"
        )
        io._handle_rename_session(app, {"id": "c1f", "title": "Plans"})
        assert ctx.sessions["c1f1aaf8-4444"]["title"] == "Plans"
        assert io._handle_delete_session(app, {"id": "6d9"})["type"] == "session_error"


@pytest.mark.unit
class TestNaming:
    """The daemon names a new chat after its first message, for every screen
    (2026-11-12, Toufic: the phone page and jarvis-app kept "New chat")."""

    def test_the_lead_in_said_to_jarvis_is_dropped(self):
        from jarvis.sessions.naming import title_from

        assert (
            title_from("Hey Jarvis, can you run the system update for me?")
            == "Run the system update for me"
        )
        assert title_from("what's on my calendar") == "What's on my calendar"
        assert title_from("Jarvis please check disk space") == "Check disk space"
        assert title_from("I want you to   find\nmy notes") == "Find my notes"

    def test_long_and_empty_titles(self):
        from jarvis.sessions.naming import title_from

        long = title_from(
            "summarize supercalifragilisticexpialidocious documentation thoroughly"
        )
        assert len(long) <= 40 and long.endswith("…")
        assert title_from("Hey Jarvis!") == ""
        assert title_from("/status") == ""
        assert title_from("   ") == ""

    def test_a_new_session_is_named_once(self):
        sessions, ctx = _sessions()
        sessions.ensure_session()
        sid = sessions.current_id
        assert sessions.name_after("Hey Jarvis, can you run the system update for me?")
        assert ctx.sessions[sid]["title"] == "Run the system update for me"
        assert not sessions.name_after("and then reboot")
        assert ctx.sessions[sid]["title"] == "Run the system update for me"

    def test_a_greeting_or_a_command_leaves_the_name_for_later(self):
        sessions, ctx = _sessions()
        sessions.ensure_session()
        sid = sessions.current_id
        assert not sessions.name_after("hey jarvis")
        assert not sessions.name_after("/switch 110")
        assert sessions.name_after("what's on my calendar")
        assert ctx.sessions[sid]["title"] == "What's on my calendar"

    def test_a_named_session_keeps_its_name(self):
        sessions, ctx = _sessions()
        sessions.switch("5bd5f76e-1111")
        assert not sessions.name_after("something else entirely")
        assert ctx.sessions["5bd5f76e-1111"]["title"] == "Update"

    def test_every_screen_gets_the_new_name(self):
        from jarvis.runtime import root_handlers

        sessions, _ = _sessions()
        sessions.ensure_session()
        sent = []

        async def broadcast(app, message, writers=None):
            sent.append(message)

        app = SimpleNamespace(sessions=sessions)
        original = root_handlers.broadcast_to_gui_clients
        root_handlers.broadcast_to_gui_clients = broadcast
        try:
            asyncio.run(root_handlers.name_session(app, "find my notes"))
            asyncio.run(root_handlers.name_session(app, "and the other ones"))
        finally:
            root_handlers.broadcast_to_gui_clients = original
        assert len(sent) == 1 and sent[0]["type"] == "session_list"
        assert "Find my notes" in [s["title"] for s in sent[0]["sessions"]]
