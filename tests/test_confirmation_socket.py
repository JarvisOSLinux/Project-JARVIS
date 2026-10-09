"""Tests for confirmation query/mutation handling over both sockets, and the
broadcast-on-resolve hook in root_handlers.py (Project-JARVIS#144)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.runtime import io as runtime_io
from jarvis.runtime import lifecycle, root_handlers


def _make_app(confirmations=None, gui_clients=None):
    confirmation_mock = Mock()
    confirmation_mock.list_pending.return_value = confirmations or []
    return SimpleNamespace(
        confirmation=confirmation_mock,
        events=Mock(),
        _gui_clients=gui_clients if gui_clients is not None else set(),
    )


@pytest.mark.unit
class TestHandleConfirmationQuery:
    @pytest.mark.asyncio
    async def test_list_confirmations_writes_current_list(self):
        app = _make_app(
            confirmations=[{"id": "a", "tool_names": ["x"], "created_at": 1.0}]
        )
        writer = Mock()
        with patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gui_write:
            handled = await runtime_io._handle_confirmation_query(
                app, {"type": "list_confirmations"}, writer
            )
        assert handled is True
        gui_write.assert_awaited_once_with(
            writer,
            {
                "type": "confirmation_list",
                "confirmations": [
                    {
                        "id": "a",
                        "tool_names": ["x"],
                        "created_at": 1.0,
                        "goal_description": None,
                    }
                ],
            },
        )

    @pytest.mark.asyncio
    async def test_approve_confirmation_injects_approved_response(self):
        app = _make_app()
        writer = Mock()
        with patch.object(runtime_io, "_gui_write", new=AsyncMock()):
            handled = await runtime_io._handle_confirmation_query(
                app, {"type": "approve_confirmation", "id": "abc"}, writer
            )
        assert handled is True
        app.events.inject_confirmation_response.assert_called_once_with(
            {"type": "confirmation_response", "id": "abc", "approved": True}
        )

    @pytest.mark.asyncio
    async def test_deny_confirmation_injects_denied_response(self):
        app = _make_app()
        writer = Mock()
        with patch.object(runtime_io, "_gui_write", new=AsyncMock()):
            handled = await runtime_io._handle_confirmation_query(
                app, {"type": "deny_confirmation", "id": "abc"}, writer
            )
        assert handled is True
        app.events.inject_confirmation_response.assert_called_once_with(
            {"type": "confirmation_response", "id": "abc", "approved": False}
        )

    @pytest.mark.asyncio
    async def test_approve_all_injects_one_response_per_pending(self):
        app = _make_app(
            confirmations=[
                {"id": "a", "tool_names": [], "created_at": 1.0},
                {"id": "b", "tool_names": [], "created_at": 2.0},
            ]
        )
        writer = Mock()
        with patch.object(runtime_io, "_gui_write", new=AsyncMock()):
            handled = await runtime_io._handle_confirmation_query(
                app, {"type": "approve_all_confirmations"}, writer
            )
        assert handled is True
        assert app.events.inject_confirmation_response.call_count == 2
        calls = [
            c.args[0] for c in app.events.inject_confirmation_response.call_args_list
        ]
        assert {"type": "confirmation_response", "id": "a", "approved": True} in calls
        assert {"type": "confirmation_response", "id": "b", "approved": True} in calls

    @pytest.mark.asyncio
    async def test_non_confirmation_message_is_not_handled(self):
        app = _make_app()
        writer = Mock()
        handled = await runtime_io._handle_confirmation_query(
            app, {"type": "message"}, writer
        )
        assert handled is False


@pytest.mark.unit
class TestOnConfirmationResponseBroadcast:
    @pytest.mark.asyncio
    async def test_broadcasts_refreshed_list_when_gui_clients_connected(self):
        app = _make_app(confirmations=[], gui_clients={Mock()})
        app.confirmation.resolve.return_value = Mock(
            request_id="abc", approved_tasks=[], denied_tools=[]
        )
        app.llm = None  # short-circuits right after the broadcast is scheduled

        with patch.object(
            root_handlers, "broadcast_to_gui_clients", new=AsyncMock()
        ) as broadcast:
            await root_handlers.on_confirmation_response(
                app, Mock(), {"id": "abc", "approved": True}
            )
            await asyncio.sleep(0)  # let the scheduled create_task actually run

        assert broadcast.await_count == 2
        broadcast.assert_any_await(
            app, {"type": "confirmation_list", "confirmations": []}
        )
        sent = [c.args[1]["type"] for c in broadcast.await_args_list]
        assert "confirmation_history" in sent

    @pytest.mark.asyncio
    async def test_no_broadcast_when_no_gui_clients(self):
        app = _make_app(confirmations=[], gui_clients=set())
        app.confirmation.resolve.return_value = Mock(
            request_id="abc", approved_tasks=[], denied_tools=[]
        )
        app.llm = None

        with patch.object(
            root_handlers, "broadcast_to_gui_clients", new=AsyncMock()
        ) as broadcast:
            await root_handlers.on_confirmation_response(
                app, Mock(), {"id": "abc", "approved": True}
            )
            await asyncio.sleep(0)

        broadcast.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_broadcast_when_resolve_returns_none(self):
        app = _make_app(confirmations=[], gui_clients={Mock()})
        app.confirmation.resolve.return_value = None

        with patch.object(
            root_handlers, "broadcast_to_gui_clients", new=AsyncMock()
        ) as broadcast:
            await root_handlers.on_confirmation_response(
                app, Mock(), {"id": "unknown", "approved": True}
            )

        broadcast.assert_not_awaited()


@pytest.mark.unit
class TestBroadcastConfirmationNotice:
    """The create-time emitter must carry the same goal_description (#192) as the
    other two confirmation_list emitters.

    This is the broadcast that populates an already-open Permission Requests
    view when a confirmation *arrives*, so if it ships a bare list_pending() the
    goal column is empty on arrival and only fills in after some unrelated
    re-query — one message type, two schemas.
    """

    @pytest.mark.asyncio
    async def test_arrival_broadcast_carries_goal_description(self):
        app = _make_app(
            confirmations=[{"id": "a", "session_id": "goal-1", "tool_names": ["x"]}],
            gui_clients={Mock()},
        )
        app._output_clients = set()
        app.goals = Mock()
        app.goals.get_goal.return_value = SimpleNamespace(description="update the OS")

        notice = {"type": "confirmation_request", "id": "a"}
        with patch.object(
            lifecycle, "broadcast_to_gui_clients", new=AsyncMock()
        ) as broadcast:
            lifecycle._broadcast_confirmation_notice(app, notice)
            await asyncio.sleep(0)

        assert broadcast.await_count == 2
        # First the notice itself, then the refreshed (enriched) pending list.
        assert broadcast.await_args_list[0].args[1] is notice
        payload = broadcast.await_args_list[1].args[1]
        assert payload["type"] == "confirmation_list"
        assert payload["confirmations"][0]["goal_description"] == "update the OS"

    @pytest.mark.asyncio
    async def test_arrival_broadcast_without_goal_manager_still_sets_the_key(self):
        """enrich_pending_with_goals degrades to None rather than requiring a
        GoalManager on app, so this adds no new wiring requirement."""
        app = _make_app(
            confirmations=[{"id": "a", "session_id": "goal-1", "tool_names": ["x"]}],
            gui_clients={Mock()},
        )
        app._output_clients = set()

        with patch.object(
            lifecycle, "broadcast_to_gui_clients", new=AsyncMock()
        ) as broadcast:
            lifecycle._broadcast_confirmation_notice(
                app, {"type": "confirmation_request", "id": "a"}
            )
            await asyncio.sleep(0)

        payload = broadcast.await_args_list[1].args[1]
        assert payload["confirmations"][0]["goal_description"] is None


@pytest.mark.unit
class TestConfirmationHistoryQuery:
    @pytest.mark.asyncio
    async def test_list_confirmation_history_replies_to_the_requester_only(self):
        from jarvis.runtime import io as runtime_io

        app = _make_app(confirmations=[], gui_clients={Mock()})
        app.confirmation.list_history.return_value = [
            {"id": "r1", "outcome": "approved"}
        ]
        writer = Mock()
        with patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw:
            handled = await runtime_io._handle_confirmation_query(
                app, {"type": "list_confirmation_history"}, writer
            )
        assert handled is True
        gw.assert_awaited_once_with(
            writer,
            {
                "type": "confirmation_history",
                "history": [{"id": "r1", "outcome": "approved"}],
            },
        )
