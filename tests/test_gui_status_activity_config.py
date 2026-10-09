"""GUI socket additions that let the TUI be a client of the daemon (#236):
status, history, activity forwarding, and the server-config form relay.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.config import Config
from jarvis.runtime import io as runtime_io
from jarvis.runtime.root_actions import _collect_config_securely


def _session(sid="abcd1234-0000", title="Bug fix"):
    return SimpleNamespace(
        id=sid,
        title=title,
        short_id=lambda: sid[:4],
        to_dict=lambda: {"id": sid, "title": title},
    )


def _app(**kw):
    app = SimpleNamespace(
        _gui_clients={},
        _gui_capabilities={},
        _config_requests={},
        sessions=SimpleNamespace(available=True, current=_session()),
        llm=SimpleNamespace(
            provider=SimpleNamespace(
                active_provider_name="local", model="qwen3:4b", last_prompt_tokens=120
            )
        ),
    )
    app.__dict__.update(kw)
    return app


@pytest.mark.unit
class TestStatus:
    def test_reports_session_model_and_context_use(self):
        with patch.object(Config, "LLM_CONTEXT_WINDOW", 4096):
            status = runtime_io.build_status(_app())
        assert status == {
            "type": "status",
            "session": {"id": "abcd1234-0000", "short_id": "abcd", "title": "Bug fix"},
            "llm_configured": True,
            "provider": "local",
            "model": "qwen3:4b",
            "prompt_tokens": 120,
            "context_window": 4096,
        }

    def test_no_llm_and_no_session(self):
        app = _app(llm=None)
        app.sessions.current = None
        status = runtime_io.build_status(app)
        assert status["session"] is None
        assert status["llm_configured"] is False
        assert status["provider"] is None and status["model"] is None
        assert status["prompt_tokens"] == 0

    @pytest.mark.asyncio
    async def test_get_status_replies_to_the_requester_only(self):
        app, writer = _app(), Mock()
        with (
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
            patch.object(runtime_io, "broadcast_to_gui_clients", new=AsyncMock()) as bc,
        ):
            await runtime_io._process_gui_message(
                app, Mock(), {"type": "get_status"}, writer
            )
        assert gw.await_args.args[0] is writer
        assert gw.await_args.args[1]["type"] == "status"
        bc.assert_not_awaited()


@pytest.mark.unit
class TestHistory:
    def _entries(self):
        return [
            {"content": "hi", "stored_at": 1, "metadata": {"type": "user_prompt"}},
            {
                "content": "hello",
                "stored_at": 2,
                "metadata": {"type": "assistant_reply"},
            },
            {"content": "noise", "stored_at": 3, "metadata": {"type": "other"}},
        ]

    @pytest.mark.asyncio
    async def test_current_session_history_to_requester_without_switching(self):
        app, writer = _app(), Mock()
        app.contextor = Mock()
        app.contextor.recall.return_value = {"entries": self._entries()}
        app.sessions.switch = Mock()
        with (
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
            patch.object(runtime_io, "broadcast_to_gui_clients", new=AsyncMock()) as bc,
        ):
            await runtime_io._process_gui_message(
                app, Mock(), {"type": "get_history"}, writer
            )
        reply = gw.await_args.args[1]
        assert reply["type"] == "history"
        assert reply["session"] == {"id": "abcd1234-0000", "title": "Bug fix"}
        assert [m["role"] for m in reply["messages"]] == ["user", "assistant"]
        app.contextor.recall.assert_called_once_with(
            "conversation_log",
            limit=runtime_io.SESSION_HISTORY_LIMIT,
            session_id="abcd1234-0000",
        )
        app.sessions.switch.assert_not_called()
        bc.assert_not_awaited()

    def test_no_session_is_empty_not_an_error(self):
        app = _app()
        app.sessions.current = None
        assert runtime_io._handle_get_history(app, {}) == {
            "type": "history",
            "session": None,
            "messages": [],
        }

    def test_memory_disabled_is_a_session_error(self):
        app = _app()
        app.sessions.available = False
        assert runtime_io._handle_get_history(app, {})["type"] == "session_error"


@pytest.mark.unit
class TestActivity:
    @pytest.mark.asyncio
    async def test_activity_is_broadcast_with_its_fields(self):
        app = _app()
        with patch.object(
            runtime_io, "broadcast_to_gui_clients", new=AsyncMock()
        ) as bc:
            runtime_io.on_gui_activity(
                app, {"text": "Installing x…", "kind": "dispatch", "meta": {"n": 1}}
            )
            await asyncio.sleep(0)
        bc.assert_awaited_once_with(
            app,
            {
                "type": "activity",
                "text": "Installing x…",
                "kind": "dispatch",
                "meta": {"n": 1},
            },
        )

    def test_no_running_loop_is_ignored(self):
        runtime_io.on_gui_activity(_app(), {"text": "x", "kind": "activity"})


@pytest.mark.unit
class TestHelloCapabilities:
    @pytest.mark.asyncio
    async def test_capabilities_recorded_and_junk_dropped(self):
        app, writer = _app(), Mock()
        app._gui_clients[writer] = "unlabeled"
        await runtime_io._process_gui_message(
            app,
            Mock(),
            {
                "type": "hello",
                "label": "jarvis-tui",
                "capabilities": ["config_form", 3],
            },
            writer,
        )
        assert app._gui_clients[writer] == "jarvis-tui"
        assert app._gui_capabilities[writer] == {"config_form"}

    @pytest.mark.asyncio
    async def test_a_hello_without_capabilities_declares_none(self):
        app, writer = _app(), Mock()
        await runtime_io._process_gui_message(
            app, Mock(), {"type": "hello", "label": "jarvisos-app"}, writer
        )
        assert writer not in app._gui_capabilities


def _client(app, label="jarvis-tui", caps=("config_form",)):
    writer = Mock()
    app._gui_clients[writer] = label
    if caps:
        app._gui_capabilities[writer] = set(caps)
    return writer


async def _ask(app, **kw):
    return await runtime_io.request_config_form(
        app, "com.x", "X", "desc", [{"key": "TOKEN", "sensitive": True}], {}
    )


@pytest.mark.unit
class TestConfigRelay:
    @pytest.mark.asyncio
    async def test_no_capable_client_means_no_relay(self):
        app = _app()
        _client(app, "jarvisos-app", caps=())
        assert await _ask(app) is None
        assert app._config_requests == {}

    @pytest.mark.asyncio
    async def test_only_capable_clients_are_asked_and_the_answer_returns(self):
        app = _app()
        tui = _client(app)
        other = _client(app, "jarvisos-app", caps=())
        logger = Mock()
        with patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw:
            task = asyncio.create_task(_ask(app))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            asked = [c.args for c in gw.await_args_list]
            assert [a[0] for a in asked] == [tui]
            request = asked[0][1]
            assert request["type"] == "config_request"
            assert request["server_id"] == "com.x"
            runtime_io.resolve_config_request(
                app,
                logger,
                {
                    "request_id": request["request_id"],
                    "confirmed": True,
                    "values": {"TOKEN": "s3cret-value"},
                },
                tui,
            )
            result = await task
        assert result == {
            "confirmed": True,
            "values": {"TOKEN": "s3cret-value"},
            "missing_required": [],
        }
        assert other not in app._config_requests
        assert app._config_requests == {}
        for call in logger.mock_calls:
            assert "s3cret-value" not in str(call)

    @pytest.mark.asyncio
    async def test_unasked_unknown_and_late_responses_are_ignored(self):
        app = _app()
        tui = _client(app)
        stranger = _client(app, "x", caps=())
        with patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw:
            task = asyncio.create_task(_ask(app))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            rid = gw.await_args.args[1]["request_id"]
            logger = Mock()
            runtime_io.resolve_config_request(
                app,
                logger,
                {"request_id": rid, "confirmed": True, "values": {"A": "1"}},
                stranger,
            )
            runtime_io.resolve_config_request(
                app, logger, {"request_id": "nope", "confirmed": True}, tui
            )
            assert not task.done()
            runtime_io.resolve_config_request(
                app,
                logger,
                {"request_id": rid, "confirmed": False, "missing_required": ["TOKEN"]},
                tui,
            )
            result = await task
            runtime_io.resolve_config_request(
                app,
                logger,
                {"request_id": rid, "confirmed": True, "values": {"A": "1"}},
                tui,
            )
        assert result == {
            "confirmed": False,
            "values": {},
            "missing_required": ["TOKEN"],
        }

    @pytest.mark.asyncio
    async def test_a_malformed_values_field_cannot_break_the_flow(self):
        app = _app()
        tui = _client(app)
        with patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw:
            task = asyncio.create_task(_ask(app))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            rid = gw.await_args.args[1]["request_id"]
            runtime_io.resolve_config_request(
                app,
                Mock(),
                {
                    "request_id": rid,
                    "confirmed": True,
                    "values": "oops",
                    "missing_required": 5,
                },
                tui,
            )
            assert await task == {
                "confirmed": True,
                "values": {},
                "missing_required": [],
            }

    @pytest.mark.asyncio
    async def test_the_last_asked_client_disconnecting_cancels_the_request(self):
        app = _app()
        tui = _client(app)
        with patch.object(runtime_io, "_gui_write", new=AsyncMock()):
            task = asyncio.create_task(_ask(app))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            runtime_io.drop_config_waiter(app, tui)
            assert await asyncio.wait_for(task, 1) == {
                "confirmed": False,
                "values": {},
                "missing_required": [],
            }

    @pytest.mark.asyncio
    async def test_another_client_can_still_answer_after_one_leaves(self):
        app = _app()
        a, b = _client(app, "a"), _client(app, "b")
        with patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw:
            task = asyncio.create_task(_ask(app))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            rid = gw.await_args.args[1]["request_id"]
            runtime_io.drop_config_waiter(app, a)
            assert not task.done()
            runtime_io.resolve_config_request(
                app,
                Mock(),
                {"request_id": rid, "confirmed": True, "values": {"T": "v"}},
                b,
            )
            assert (await task)["values"] == {"T": "v"}

    @pytest.mark.asyncio
    async def test_a_client_that_cannot_be_written_to_is_dropped(self):
        app = _app()
        _client(app)
        with patch.object(
            runtime_io, "_gui_write", new=AsyncMock(side_effect=BrokenPipeError)
        ):
            result = await asyncio.wait_for(_ask(app), 1)
        assert result["confirmed"] is False

    @pytest.mark.asyncio
    async def test_listener_shutdown_cancels_pending_requests(self):
        app = _app()
        _client(app)
        with patch.object(runtime_io, "_gui_write", new=AsyncMock()):
            task = asyncio.create_task(_ask(app))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            runtime_io.cancel_all_config_requests(app)
            assert (await asyncio.wait_for(task, 1))["confirmed"] is False


@pytest.mark.unit
class TestCollectConfigUsesTheRelay:
    @pytest.mark.asyncio
    async def test_relayed_values_are_collected(self):
        app = SimpleNamespace(config_modal_callback=None)
        relay = AsyncMock(
            return_value={
                "confirmed": True,
                "values": {"K": "v"},
                "missing_required": [],
            }
        )
        with patch.object(runtime_io, "request_config_form", new=relay):
            got = await _collect_config_securely(
                app, Mock(), "com.x", {"name": "X"}, [{"key": "K"}], {}
            )
        assert got == ("collected", {"K": "v"}, [])

    @pytest.mark.asyncio
    async def test_a_cancelled_relay_reports_what_was_missing(self):
        app = SimpleNamespace(config_modal_callback=None)
        relay = AsyncMock(
            return_value={"confirmed": False, "values": {}, "missing_required": ["K"]}
        )
        with patch.object(runtime_io, "request_config_form", new=relay):
            got = await _collect_config_securely(
                app, Mock(), "com.x", {}, [{"key": "K"}], {}
            )
        assert got == ("cancelled", {}, ["K"])

    @pytest.mark.asyncio
    async def test_the_in_process_modal_still_wins(self):
        result = SimpleNamespace(confirmed=True, values={"K": "m"}, missing_required=[])

        async def modal(server_id, name, desc, props, saved, future):
            future.set_result(result)

        relay = AsyncMock()
        app = SimpleNamespace(config_modal_callback=modal)
        with patch.object(runtime_io, "request_config_form", new=relay):
            got = await _collect_config_securely(
                app, Mock(), "com.x", {}, [{"key": "K"}], {}
            )
        assert got == ("collected", {"K": "m"}, [])
        relay.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_client_and_no_display_is_unavailable(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        app = SimpleNamespace(config_modal_callback=None)
        with patch.object(
            runtime_io, "request_config_form", new=AsyncMock(return_value=None)
        ):
            got = await _collect_config_securely(
                app, Mock(), "com.x", {}, [{"key": "K"}], {}
            )
        assert got == ("unavailable", {}, [])
