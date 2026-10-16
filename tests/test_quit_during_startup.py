"""Quitting while JARVIS starts must not hang the process.

Ctrl+Q in the first seconds of `jarvis tui` closed the TUI but left the
terminal stuck: the engine task was cancelled mid-startup, the dispatch
stdio transport it had opened was never closed, and the MCP SDK's stdout
reader (shielded until the transport closes the pipe) kept asyncio.run
waiting forever. Two holes, one test each: connect() now closes what it
opened when cancelled, and run() reaches shutdown() even if startup is
cancelled.
"""

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis import main as jarvis_main
from jarvis.dispatch import transport


class _Ctx:
    """An async context manager that records how it was left."""

    def __init__(self, value=None, *, exit_error=None):
        self.value = value
        self.exit_error = exit_error
        self.exited = 0

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc):
        self.exited += 1
        if self.exit_error:
            raise self.exit_error
        return False


def _adapter():
    return SimpleNamespace(session=None, _client=None, _connected=False)


@contextlib.contextmanager
def _patch_mcp(client, session):
    with (
        patch("mcp.client.stdio.stdio_client", lambda params: client),
        patch("mcp.ClientSession", lambda *a, **kw: session),
    ):
        yield


@pytest.mark.unit
def test_cancel_mid_handshake_closes_the_transport():
    client = _Ctx(("read", "write"))
    session = _Ctx()
    started = asyncio.Event()

    async def never_initializes():
        started.set()
        await asyncio.Event().wait()

    session.initialize = never_initializes
    adapter = _adapter()

    async def run():
        task = asyncio.create_task(transport.connect(adapter, Mock()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with _patch_mcp(client, session):
        asyncio.run(run())

    assert client.exited == 1 and session.exited == 1
    assert adapter._client is None and adapter.session is None
    assert not adapter._connected


@pytest.mark.unit
def test_disconnect_closes_the_transport_even_if_the_session_fails():
    client = _Ctx()
    adapter = _adapter()
    adapter._client = client
    adapter.session = _Ctx(exit_error=RuntimeError("boom"))
    adapter._connected = True

    asyncio.run(transport.disconnect(adapter, Mock()))
    asyncio.run(transport.disconnect(adapter, Mock()))

    assert client.exited == 1
    assert adapter._client is None and not adapter._connected


@pytest.mark.unit
def test_cancel_during_startup_still_shuts_down():
    started = asyncio.Event()

    async def slow_connect():
        started.set()
        await asyncio.Event().wait()

    app = SimpleNamespace(
        _running=False,
        dispatch=SimpleNamespace(connect=slow_connect),
        _embeddings=None,
        _gui_clients=[],
    )
    shutdown = AsyncMock()

    async def run():
        task = asyncio.create_task(jarvis_main.Jarvis.run(app))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with (
        patch.object(jarvis_main, "install_signal_handlers"),
        patch.object(jarvis_main, "shutdown", shutdown),
    ):
        asyncio.run(run())

    shutdown.assert_awaited_once()
