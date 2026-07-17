"""Tests for confirmation query/mutation handling over both sockets, and the
broadcast-on-resolve hook in root_handlers.py (Project-JARVIS#144)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.runtime import io as runtime_io
from jarvis.runtime import root_handlers


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

        broadcast.assert_awaited_once_with(
            app, {"type": "confirmation_list", "confirmations": []}
        )

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
