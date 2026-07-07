"""Tests for the connected-clients query + graceful shutdown protocol
(Project-JARVIS#146): client labeling, list_clients, shutdown_request,
the DAEMON_SHUTDOWN broadcast + final-state snapshot, and voice-thread
join-on-shutdown.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.runtime import io as runtime_io
from jarvis.runtime import lifecycle as runtime_lifecycle

_UNSET = object()


def _make_app(gui_clients=None, goals=None, sessions=_UNSET):
    return SimpleNamespace(
        _gui_clients=gui_clients if gui_clients is not None else {},
        _gui_state="idle",
        goals=goals or Mock(get_active_goals=Mock(return_value=[])),
        sessions=Mock(current_id="sess-123") if sessions is _UNSET else sessions,
        _running=True,
        events=Mock(),
        voice_manager=None,
        stop=Mock(),
    )


@pytest.mark.unit
class TestClientLabeling:
    @pytest.mark.asyncio
    async def test_hello_sets_the_client_label(self):
        writer = Mock()
        app = _make_app(gui_clients={writer: runtime_io.DEFAULT_CLIENT_LABEL})

        await runtime_io._process_gui_message(
            app, Mock(), {"type": "hello", "label": "jarvis-app"}, writer
        )

        assert app._gui_clients[writer] == "jarvis-app"

    @pytest.mark.asyncio
    async def test_hello_with_blank_label_is_ignored(self):
        writer = Mock()
        app = _make_app(gui_clients={writer: runtime_io.DEFAULT_CLIENT_LABEL})

        await runtime_io._process_gui_message(
            app, Mock(), {"type": "hello", "label": "   "}, writer
        )

        assert app._gui_clients[writer] == runtime_io.DEFAULT_CLIENT_LABEL

    @pytest.mark.asyncio
    async def test_list_clients_returns_current_labels(self):
        w1, w2 = Mock(), Mock()
        app = _make_app(gui_clients={w1: "jarvis-app", w2: "TUI"})
        writer = Mock()

        with patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw:
            await runtime_io._process_gui_message(
                app, Mock(), {"type": "list_clients"}, writer
            )

        gw.assert_awaited_once_with(
            writer, {"type": "client_list", "clients": ["jarvis-app", "TUI"]}
        )

    @pytest.mark.asyncio
    async def test_unlabeled_client_appears_with_default_label(self):
        writer = Mock()
        app = _make_app(gui_clients={writer: runtime_io.DEFAULT_CLIENT_LABEL})
        reply_writer = Mock()

        with patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw:
            await runtime_io._process_gui_message(
                app, Mock(), {"type": "list_clients"}, reply_writer
            )

        gw.assert_awaited_once_with(
            reply_writer, {"type": "client_list", "clients": ["unlabeled"]}
        )


@pytest.mark.unit
class TestShutdownRequest:
    @pytest.mark.asyncio
    async def test_shutdown_request_calls_app_stop(self):
        app = _make_app()
        writer = Mock()

        await runtime_io._process_gui_message(
            app, Mock(), {"type": "shutdown_request"}, writer
        )

        app.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_request_callable_by_any_client_no_gate(self):
        """No confirmation-tier gating at the daemon level -- the issue is
        explicit that this is each client's own responsibility."""
        app = _make_app()
        writer = Mock()

        # No confirmation_manager interaction of any kind should occur.
        await runtime_io._process_gui_message(
            app, Mock(), {"type": "shutdown_request"}, writer
        )
        assert app.stop.called


@pytest.mark.unit
class TestBuildShutdownSnapshot:
    def test_snapshot_includes_state_goals_session_and_timestamp(self):
        goal_ctx = {"id": "g1", "description": "do a thing", "status": "active"}
        app = _make_app(
            goals=Mock(
                get_active_goals=Mock(return_value=[Mock(to_context=lambda: goal_ctx)])
            ),
            sessions=Mock(current_id="sess-abc"),
        )
        app._gui_state = "processing"

        before = time.time()
        snapshot = runtime_lifecycle.build_shutdown_snapshot(app)
        after = time.time()

        assert snapshot["state"] == "processing"
        assert snapshot["goals"] == [goal_ctx]
        assert snapshot["session_id"] == "sess-abc"
        assert before <= snapshot["timestamp"] <= after

    def test_snapshot_session_id_is_none_when_sessions_unavailable(self):
        app = _make_app(sessions=None)
        snapshot = runtime_lifecycle.build_shutdown_snapshot(app)
        assert snapshot["session_id"] is None


@pytest.mark.unit
class TestBroadcastShutdownNotice:
    @pytest.mark.asyncio
    async def test_broadcasts_daemon_shutdown_with_snapshot_fields(self):
        writer = Mock()
        app = _make_app(gui_clients={writer: "jarvis-app"})

        with patch.object(
            runtime_lifecycle, "broadcast_to_gui_clients", new=AsyncMock()
        ) as bc:
            await runtime_lifecycle.broadcast_shutdown_notice(app, Mock())

        bc.assert_awaited_once()
        sent_app, message = bc.await_args.args
        assert sent_app is app
        assert message["type"] == "DAEMON_SHUTDOWN"
        assert "state" in message and "goals" in message
        assert "session_id" in message and "timestamp" in message

    @pytest.mark.asyncio
    async def test_no_broadcast_when_no_clients_connected(self):
        app = _make_app(gui_clients={})

        with patch.object(
            runtime_lifecycle, "broadcast_to_gui_clients", new=AsyncMock()
        ) as bc:
            await runtime_lifecycle.broadcast_shutdown_notice(app, Mock())

        bc.assert_not_awaited()


@pytest.mark.unit
class TestJoinVoiceThreadIfRunning:
    def test_joins_a_running_thread(self):
        started = threading.Event()
        finished = threading.Event()

        def work():
            started.set()
            time.sleep(0.05)
            finished.set()

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        started.wait(timeout=1.0)

        runtime_lifecycle.join_voice_thread_if_running(thread, timeout=1.0)

        assert finished.is_set()
        assert not thread.is_alive()

    def test_none_thread_is_a_no_op(self):
        runtime_lifecycle.join_voice_thread_if_running(None)  # must not raise

    def test_already_finished_thread_is_a_no_op(self):
        thread = threading.Thread(target=lambda: None)
        thread.start()
        thread.join()
        runtime_lifecycle.join_voice_thread_if_running(thread)  # must not raise
