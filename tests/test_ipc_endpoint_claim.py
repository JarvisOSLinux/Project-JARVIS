"""IPC endpoints are claimed, not stolen.

``create_ipc_server`` used to unlink whatever was at the path and bind over it.
A second JARVIS — most easily ``jarvis tui`` next to a running daemon — then
silently took all three endpoints: the first process kept a listening fd nobody
could reach, every client landed on the second, and both mirrored ``_pending``
to the same confirmation store.
"""

import asyncio
import socket

import pytest

from jarvis.platform.base import IPCEndpointBusy
from jarvis.platform.linux import LinuxPlatform


async def _noop(reader, writer):  # pragma: no cover - never invoked
    writer.close()


@pytest.mark.unit
@pytest.mark.asyncio
class TestClaimEndpoint:
    async def test_live_endpoint_is_refused(self, tmp_path):
        p = LinuxPlatform()
        path = str(tmp_path / "input.sock")
        server = await p.create_ipc_server(path, _noop)
        try:
            with pytest.raises(IPCEndpointBusy) as excinfo:
                await p.create_ipc_server(path, _noop)
            assert path in str(excinfo.value)
            assert excinfo.value.path == path
        finally:
            server.close()
            await server.wait_closed()

    async def test_the_original_server_keeps_working_after_a_refusal(self, tmp_path):
        """The point of refusing: the first daemon is still the one serving."""
        p = LinuxPlatform()
        path = str(tmp_path / "input.sock")
        seen = asyncio.Event()

        async def handler(reader, writer):
            seen.set()
            writer.close()

        server = await p.create_ipc_server(path, handler)
        try:
            with pytest.raises(IPCEndpointBusy):
                await p.create_ipc_server(path, _noop)

            reader, writer = await asyncio.open_unix_connection(path)
            await asyncio.wait_for(seen.wait(), timeout=2)
            writer.close()
        finally:
            server.close()
            await server.wait_closed()

    async def test_stale_socket_file_is_reclaimed(self, tmp_path):
        """A crashed daemon leaves a socket file that refuses connections."""
        p = LinuxPlatform()
        path = str(tmp_path / "input.sock")

        stale = socket.socket(socket.AF_UNIX)
        stale.bind(path)
        stale.close()  # file remains, nothing listening

        server = await p.create_ipc_server(path, _noop)
        try:
            assert server.sockets
        finally:
            server.close()
            await server.wait_closed()

    async def test_absent_path_binds_normally(self, tmp_path):
        p = LinuxPlatform()
        server = await p.create_ipc_server(str(tmp_path / "fresh.sock"), _noop)
        server.close()
        await server.wait_closed()

    async def test_endpoint_live_reports_false_for_nothing_there(self, tmp_path):
        p = LinuxPlatform()
        assert p.ipc_endpoint_live(str(tmp_path / "missing.sock")) is False


@pytest.mark.unit
@pytest.mark.asyncio
class TestListenerDegradesInsteadOfCrashing:
    async def test_busy_endpoint_disables_the_socket_and_spares_the_owner(
        self, tmp_path, monkeypatch
    ):
        """A busy endpoint must not kill the daemon — and must not clean up
        the *other* daemon's socket file on the way out."""
        from jarvis.runtime import io as runtime_io

        path = str(tmp_path / "input.sock")
        p = LinuxPlatform()
        owner = await p.create_ipc_server(path, _noop)

        # io.py's own Config reference — other test modules reload
        # jarvis.config, so patching the current jarvis.config.Config can miss.
        monkeypatch.setattr(
            runtime_io.Config, "JARVIS_INPUT_SOCKET", path, raising=False
        )

        class _Logger:
            def __init__(self):
                self.errors = []

            def error(self, msg, *args):
                self.errors.append(msg % args if args else msg)

        logger = _Logger()
        try:
            # Returns rather than raising, and returns promptly.
            await asyncio.wait_for(
                runtime_io.run_socket_listener(object(), logger), timeout=5
            )
            assert any("already served" in e for e in logger.errors)
            # The owner's endpoint file is untouched.
            assert p.ipc_endpoint_live(path) is True
        finally:
            owner.close()
            await owner.wait_closed()
