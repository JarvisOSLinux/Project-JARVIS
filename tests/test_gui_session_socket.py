"""Unit tests for the GUI socket's session CRUD handlers (jarvis/runtime/io.py)."""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.runtime import io as runtime_io
from jarvis.sessions.block_log import BlockLog
from jarvis.sessions.model import Session


def _make_app(sessions=None, contextor=None):
    sessions = sessions or Mock()
    sessions.blocks = BlockLog()
    return SimpleNamespace(sessions=sessions, contextor=contextor or Mock())


def _sample_session(id_="abc12345", title="Test chat"):
    return Session(id=id_, title=title, created_at="t0", updated_at="t1", entry_count=2)


@pytest.mark.unit
class TestEntriesToMessages:
    def test_maps_known_roles_in_order(self):
        entries = [
            {"content": "hi", "metadata": {"type": "user_prompt"}, "stored_at": 1.0},
            {
                "content": "hello!",
                "metadata": {"type": "assistant_reply"},
                "stored_at": 2.0,
            },
        ]
        messages = runtime_io._entries_to_messages(entries)
        assert messages == [
            {"role": "user", "content": "hi", "timestamp": 1.0},
            {"role": "assistant", "content": "hello!", "timestamp": 2.0},
        ]

    def test_skips_entries_with_unknown_or_missing_type(self):
        entries = [
            {"content": "noise", "metadata": {"type": "something_else"}},
            {"content": "no metadata at all"},
        ]
        assert runtime_io._entries_to_messages(entries) == []


@pytest.mark.unit
class TestHandleListSessions:
    def test_returns_error_when_sessions_unavailable(self):
        app = _make_app(sessions=Mock(available=False))
        result = runtime_io._handle_list_sessions(app, {})
        assert result["type"] == "session_error"

    def test_returns_serialized_session_list(self):
        sessions_mock = Mock(available=True)
        sessions_mock.list.return_value = [
            _sample_session("aaa"),
            _sample_session("bbb"),
        ]
        app = _make_app(sessions=sessions_mock)

        result = runtime_io._handle_list_sessions(app, {"limit": 10, "offset": 0})

        assert result["type"] == "session_list"
        assert [s["id"] for s in result["sessions"]] == ["aaa", "bbb"]
        sessions_mock.list.assert_called_once_with(limit=10, offset=0)


@pytest.mark.unit
class TestHandleCreateSession:
    def test_creates_and_returns_empty_history(self):
        sessions_mock = Mock(available=True)
        sessions_mock.new_session.return_value = _sample_session()
        app = _make_app(sessions=sessions_mock)

        result = runtime_io._handle_create_session(app, {"title": "New chat"})

        assert result["type"] == "session_switched"
        assert result["messages"] == []
        assert result["session"]["title"] == "Test chat"
        sessions_mock.new_session.assert_called_once_with(title="New chat")

    def test_failure_returns_error(self):
        sessions_mock = Mock(available=True)
        sessions_mock.new_session.return_value = None
        app = _make_app(sessions=sessions_mock)

        result = runtime_io._handle_create_session(app, {})

        assert result["type"] == "session_error"


@pytest.mark.unit
class TestHandleSwitchSession:
    def test_requires_id(self):
        app = _make_app()
        result = runtime_io._handle_switch_session(app, {})
        assert result["type"] == "session_error"

    def test_switches_and_attaches_history(self):
        sessions_mock = Mock(available=True)
        sessions_mock.find.return_value = (_sample_session("abc12345"), "")
        sessions_mock.switch.return_value = _sample_session("abc12345")
        contextor_mock = Mock()
        contextor_mock.recall.return_value = {
            "entries": [
                {
                    "content": "hi",
                    "metadata": {"type": "user_prompt"},
                    "stored_at": 1.0,
                },
            ]
        }
        app = _make_app(sessions=sessions_mock, contextor=contextor_mock)

        result = runtime_io._handle_switch_session(app, {"id": "abc12345"})

        assert result["type"] == "session_switched"
        assert result["session"]["id"] == "abc12345"
        assert result["messages"] == [
            {"role": "user", "content": "hi", "timestamp": 1.0}
        ]
        contextor_mock.recall.assert_called_once_with(
            "conversation_log",
            limit=runtime_io.SESSION_HISTORY_LIMIT,
            session_id="abc12345",
        )

    def test_no_match_returns_error(self):
        sessions_mock = Mock(available=True)
        sessions_mock.find.return_value = (None, "No session id starts with 'nope'.")
        app = _make_app(sessions=sessions_mock)

        result = runtime_io._handle_switch_session(app, {"id": "nope"})

        assert result == {
            "type": "session_error",
            "message": "No session id starts with 'nope'.",
        }
        sessions_mock.switch.assert_not_called()


@pytest.mark.unit
class TestHandleRenameSession:
    def test_requires_id_and_title(self):
        app = _make_app()
        assert (
            runtime_io._handle_rename_session(app, {"id": "x"})["type"]
            == "session_error"
        )
        assert (
            runtime_io._handle_rename_session(app, {"title": "y"})["type"]
            == "session_error"
        )

    def test_rename_success_returns_refreshed_list(self):
        sessions_mock = Mock(available=True)
        sessions_mock.find.return_value = (_sample_session("abc"), "")
        sessions_mock.rename.return_value = True
        sessions_mock.list.return_value = [_sample_session("abc", title="Renamed")]
        app = _make_app(sessions=sessions_mock)

        result = runtime_io._handle_rename_session(
            app, {"id": "abc", "title": "Renamed"}
        )

        assert result["type"] == "session_list"
        sessions_mock.rename.assert_called_once_with("Renamed", session_id="abc")

    def test_rename_failure_returns_error(self):
        sessions_mock = Mock(available=True)
        sessions_mock.find.return_value = (_sample_session("abc"), "")
        sessions_mock.rename.return_value = False
        app = _make_app(sessions=sessions_mock)

        result = runtime_io._handle_rename_session(app, {"id": "abc", "title": "x"})

        assert result["type"] == "session_error"


@pytest.mark.unit
class TestHandleDeleteSession:
    def test_requires_id(self):
        app = _make_app()
        assert runtime_io._handle_delete_session(app, {})["type"] == "session_error"

    def test_delete_success_returns_refreshed_list(self):
        sessions_mock = Mock(available=True)
        sessions_mock.find.return_value = (_sample_session("abc"), "")
        sessions_mock.delete.return_value = True
        sessions_mock.list.return_value = []
        app = _make_app(sessions=sessions_mock)

        result = runtime_io._handle_delete_session(app, {"id": "abc"})

        assert result["type"] == "session_list"
        sessions_mock.delete.assert_called_once_with("abc")

    def test_delete_failure_returns_error(self):
        sessions_mock = Mock(available=True)
        sessions_mock.find.return_value = (_sample_session("abc"), "")
        sessions_mock.delete.return_value = False
        app = _make_app(sessions=sessions_mock)

        result = runtime_io._handle_delete_session(app, {"id": "abc"})

        assert result["type"] == "session_error"


@pytest.mark.unit
class TestReplyOrBroadcast:
    @pytest.mark.asyncio
    async def test_error_goes_to_requester_only(self):
        app = _make_app()
        writer = Mock()
        with (
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gui_write,
            patch.object(
                runtime_io, "broadcast_to_gui_clients", new=AsyncMock()
            ) as broadcast,
        ):
            await runtime_io._reply_or_broadcast(
                app, writer, {"type": "session_error", "message": "boom"}
            )
        gui_write.assert_awaited_once_with(
            writer, {"type": "session_error", "message": "boom"}
        )
        broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_broadcasts_to_all_clients(self):
        app = _make_app()
        writer = Mock()
        response = {"type": "session_list", "sessions": []}
        with (
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gui_write,
            patch.object(
                runtime_io, "broadcast_to_gui_clients", new=AsyncMock()
            ) as broadcast,
        ):
            await runtime_io._reply_or_broadcast(app, writer, response)
        broadcast.assert_awaited_once_with(app, response)
        gui_write.assert_not_awaited()


class _Writer:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.extend(json.loads(line) for line in data.decode().splitlines())

    async def drain(self):
        pass


def _typed_app():
    """A daemon with one GUI client, whose sessions all succeed."""
    session = _sample_session("abc12345")
    sessions = Mock(available=True, current_id="cur00000")
    sessions.new_session.return_value = session
    sessions.find.return_value = (session, "")
    sessions.switch.return_value = session
    sessions.rename.return_value = True
    sessions.delete.return_value = True
    sessions.list.return_value = [session]
    contextor = Mock()
    contextor.recall.return_value = {
        "entries": [{"content": "hi", "metadata": {"type": "user_prompt"}}]
    }
    writer = _Writer()
    app = _make_app(sessions=sessions, contextor=contextor)
    app._gui_clients = {writer: "jarvis-app"}
    app.output_manager = Mock()
    return app, writer


def _type(app, line):
    from jarvis.runtime import session_commands

    async def run():
        assert session_commands.handle_slash_command(app, line)
        await asyncio.sleep(0)

    asyncio.run(run())


@pytest.mark.unit
class TestTypedSessionCommandsRedrawEveryScreen:
    """A typed /new or /switch left jarvis-app and the phones on the old chat:
    the text path answered with a reply alone, while the socket requests
    broadcast the session."""

    @pytest.mark.parametrize(
        "line, kind, messages",
        [
            ("/new", "session_switched", []),
            ("/switch abc", "session_switched", [{"role": "user", "content": "hi"}]),
            ("/rename abc12345 Yakup", "session_list", None),
            ("/delete abc", "session_list", None),
        ],
    )
    def test_success_broadcasts_what_the_socket_request_does(
        self, line, kind, messages
    ):
        app, writer = _typed_app()
        _type(app, line)
        [sent] = writer.sent
        assert sent["type"] == kind
        if kind == "session_switched":
            assert sent["session"]["id"] == "abc12345"
            assert [
                {k: m[k] for k in ("role", "content")} for m in sent["messages"]
            ] == messages
        else:
            assert [s["id"] for s in sent["sessions"]] == ["abc12345"]
        app.output_manager.handle_response.assert_called_once()

    @pytest.mark.parametrize("line", ["/switch", "/rename", "/delete", "/sessions"])
    def test_nothing_is_broadcast_when_nothing_changed(self, line):
        app, writer = _typed_app()
        _type(app, line)
        assert writer.sent == []

    def test_a_failed_switch_broadcasts_nothing(self):
        app, writer = _typed_app()
        app.sessions.find.return_value = (None, "No session matches 'zz'")
        _type(app, "/switch zz")
        assert writer.sent == []

    def test_deleting_the_current_session_leaves_it_on_every_screen(self):
        """A5: jarvis-app kept showing a deleted current chat; the TUI hears
        it through `on_switch`, the sockets now through `session_switched`."""
        app, writer = _typed_app()
        app.sessions.current_id = "abc12345"
        _type(app, "/delete abc")
        assert [m["type"] for m in writer.sent] == ["session_switched", "session_list"]
        assert writer.sent[0] == {
            "type": "session_switched",
            "session": None,
            "messages": [],
        }

    def test_the_socket_delete_of_the_current_session_leaves_it_too(self):
        app, writer = _typed_app()
        app.sessions.current_id = "abc12345"

        async def run():
            await runtime_io._process_gui_message(
                app,
                logging.getLogger("t"),
                {"type": "delete_session", "id": "abc"},
                writer,
            )
            await asyncio.sleep(0)

        asyncio.run(run())
        assert sorted(m["type"] for m in writer.sent) == [
            "session_list",
            "session_switched",
        ]
        [left] = [m for m in writer.sent if m["type"] == "session_switched"]
        assert left["session"] is None and left["messages"] == []

    def test_the_socket_request_is_still_broadcast_once(self):
        app, writer = _typed_app()
        asyncio.run(
            runtime_io._process_gui_message(
                app, logging.getLogger("t"), {"type": "create_session"}, writer
            )
        )
        assert [m["type"] for m in writer.sent] == ["session_switched"]
