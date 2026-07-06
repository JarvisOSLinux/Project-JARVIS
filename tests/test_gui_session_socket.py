"""Unit tests for the GUI socket's session CRUD handlers (jarvis/runtime/io.py)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.runtime import io as runtime_io
from jarvis.sessions.model import Session


def _make_app(sessions=None, contextor=None):
    return SimpleNamespace(sessions=sessions or Mock(), contextor=contextor or Mock())


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
        sessions_mock.switch.return_value = None
        app = _make_app(sessions=sessions_mock)

        result = runtime_io._handle_switch_session(app, {"id": "nope"})

        assert result["type"] == "session_error"


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
        sessions_mock.delete.return_value = True
        sessions_mock.list.return_value = []
        app = _make_app(sessions=sessions_mock)

        result = runtime_io._handle_delete_session(app, {"id": "abc"})

        assert result["type"] == "session_list"
        sessions_mock.delete.assert_called_once_with("abc")

    def test_delete_failure_returns_error(self):
        sessions_mock = Mock(available=True)
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
