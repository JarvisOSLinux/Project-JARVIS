"""
Dispatch adapter integration tests for JARVIS AI Assistant.

Tests the DispatchAdapter, EventMerger, and the interaction between
dispatch components.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from jarvis.dispatch.adapter import DispatchAdapter
from jarvis.dispatch.event_merger import Event, EventMerger, EventType


@pytest.mark.integration
class TestDispatchAdapterInit:
    """Test DispatchAdapter initialisation and properties."""

    def test_adapter_initialization(self):
        adapter = DispatchAdapter()
        assert adapter is not None
        assert adapter.is_connected is False
        assert adapter.session is None

    def test_adapter_timeout_from_config(self):
        import importlib

        import jarvis.config

        importlib.reload(jarvis.config)
        from jarvis.config import Config

        adapter = DispatchAdapter()
        assert adapter.timeout == Config.DISPATCH_TIMEOUT


@pytest.mark.integration
class TestDispatchAdapterNotConnected:
    """Test DispatchAdapter methods when not connected."""

    @pytest.mark.asyncio
    async def test_send_tasks_returns_error(self):
        adapter = DispatchAdapter()
        result = await adapter.send_tasks([{"server": "X", "tool": "y", "params": {}}])
        assert "error" in result
        assert "Not connected" in result["error"]

    @pytest.mark.asyncio
    async def test_kill_tasks_returns_error(self):
        adapter = DispatchAdapter()
        result = await adapter.kill_tasks([1, 2])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_timer_returns_error(self):
        adapter = DispatchAdapter()
        result = await adapter.set_timer("test", 60)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_signal_window_returns_empty(self):
        adapter = DispatchAdapter()
        result = await adapter.get_signal_window()
        assert result == []


@pytest.mark.integration
class TestEventMergerInit:
    """Test EventMerger creation and basic behaviour."""

    def test_event_merger_creation(self):
        merger = EventMerger()
        assert merger is not None
        assert merger._running is False

    def test_event_creation_user_input(self):
        event = Event.user_input("hello")
        assert event.type == EventType.USER_INPUT
        assert event.data == "hello"

    def test_event_creation_dispatch_signal(self):
        signal = {"pid": 1, "type": "EXIT"}
        event = Event.dispatch_signal(signal)
        assert event.type == EventType.DISPATCH_SIGNAL
        assert event.data == signal

    def test_event_creation_shutdown(self):
        event = Event.shutdown()
        assert event.type == EventType.SHUTDOWN


@pytest.mark.integration
class TestEventMergerQueue:
    """Test EventMerger push/get."""

    @pytest.mark.asyncio
    async def test_push_and_get_event(self):
        merger = EventMerger()
        await merger.push_event(Event.user_input("hi"))

        event = await merger.get_next_event()
        assert event.type == EventType.USER_INPUT
        assert event.data == "hi"

    @pytest.mark.asyncio
    async def test_async_iteration_shutdown(self):
        """Test that async iteration stops on shutdown event."""
        merger = EventMerger()
        merger._running = True

        await merger.push_event(Event.user_input("msg1"))
        await merger.push_event(Event.shutdown())

        events = []
        async for event in merger:
            events.append(event)

        assert len(events) == 1
        assert events[0].data == "msg1"


@pytest.mark.integration
class TestEventMergerListening:
    """Test EventMerger with source callbacks."""

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        merger = EventMerger()

        call_count = 0

        async def user_source():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return "hello"
            await asyncio.sleep(10)  # block after first
            return ""

        async def signal_source():
            await asyncio.sleep(10)
            return None

        merger.start(user_source, signal_source)
        assert merger._running is True

        # Give it time to process
        await asyncio.sleep(0.1)

        await merger.stop()
        assert merger._running is False


@pytest.mark.integration
class TestDispatchAdapterContextManager:
    """Test DispatchAdapter async context manager interface."""

    @pytest.mark.asyncio
    async def test_context_manager_calls_connect_disconnect(self):
        adapter = DispatchAdapter()

        with (
            patch.object(adapter, "connect", new_callable=AsyncMock) as mock_connect,
            patch.object(
                adapter, "disconnect", new_callable=AsyncMock
            ) as mock_disconnect,
        ):
            async with adapter:
                mock_connect.assert_called_once()
            mock_disconnect.assert_called_once()
