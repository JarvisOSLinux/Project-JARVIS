"""Tests for the persistent pending-confirmations list (Project-JARVIS#144)."""

import asyncio
from unittest.mock import Mock, patch

import pytest

from jarvis.core.confirmation_manager import ConfirmationManager


def _tools_needing_confirmation(names):
    return [{"tool_name": n, "params": {}} for n in names]


@pytest.mark.unit
class TestListPending:
    @pytest.mark.asyncio
    async def test_empty_when_nothing_pending(self):
        mgr = ConfirmationManager()
        assert mgr.list_pending() == []

    @pytest.mark.asyncio
    async def test_lists_tool_names_and_created_at(self):
        mgr = ConfirmationManager()
        mgr.set_event_injector(Mock())
        await mgr.request_confirmation(
            request_id="abc123",
            tasks=[{"server": "s", "tool": "t", "params": {}}],
            tools_needing_confirmation=_tools_needing_confirmation(["s.t"]),
            approved_tasks=[],
            denied_tools=[],
            timeout=0,
        )
        pending = mgr.list_pending()
        assert len(pending) == 1
        assert pending[0]["id"] == "abc123"
        assert pending[0]["tool_names"] == ["s.t"]
        assert isinstance(pending[0]["created_at"], float)

    @pytest.mark.asyncio
    async def test_resolved_confirmation_is_removed(self):
        mgr = ConfirmationManager()
        mgr.set_event_injector(Mock())
        await mgr.request_confirmation(
            request_id="abc123",
            tasks=[{"server": "s", "tool": "t"}],
            tools_needing_confirmation=_tools_needing_confirmation(["s.t"]),
            approved_tasks=[],
            denied_tools=[],
            timeout=0,
        )
        mgr.resolve({"id": "abc123", "approved": True})
        assert mgr.list_pending() == []


@pytest.mark.unit
class TestTimeoutDisabledByDefault:
    @pytest.mark.asyncio
    async def test_zero_timeout_never_auto_denies(self):
        mgr = ConfirmationManager()
        injector = Mock()
        mgr.set_event_injector(injector)
        await mgr.request_confirmation(
            request_id="abc",
            tasks=[{"server": "s", "tool": "t"}],
            tools_needing_confirmation=_tools_needing_confirmation(["s.t"]),
            approved_tasks=[],
            denied_tools=[],
            timeout=0,
        )
        await asyncio.sleep(0.05)
        injector.assert_not_called()
        assert mgr.has_pending("abc")

    @pytest.mark.asyncio
    async def test_positive_timeout_still_auto_denies(self):
        mgr = ConfirmationManager()
        injector = Mock()
        mgr.set_event_injector(injector)
        await mgr.request_confirmation(
            request_id="abc",
            tasks=[{"server": "s", "tool": "t"}],
            tools_needing_confirmation=_tools_needing_confirmation(["s.t"]),
            approved_tasks=[],
            denied_tools=[],
            timeout=0.05,
        )
        await asyncio.sleep(0.2)
        # _auto_deny_after's whole contract is firing this injection; actual
        # removal from _pending only happens later, when resolve() runs off
        # the real event loop's CONFIRMATION_RESPONSE handling -- not
        # exercised by this isolated unit test.
        injector.assert_called_once_with(
            {"type": "confirmation_response", "id": "abc", "approved": False}
        )


@pytest.mark.unit
class TestNoChannelAvailable:
    @pytest.mark.asyncio
    async def test_no_channel_leaves_it_pending_not_denied(self):
        mgr = ConfirmationManager()
        injector = Mock()
        mgr.set_event_injector(injector)
        with (
            patch.object(ConfirmationManager, "_has_tty", return_value=False),
            patch.object(
                ConfirmationManager, "_has_desktop_notifications", return_value=False
            ),
        ):
            await mgr.request_confirmation(
                request_id="abc",
                tasks=[{"server": "s", "tool": "t"}],
                tools_needing_confirmation=_tools_needing_confirmation(["s.t"]),
                approved_tasks=[],
                denied_tools=[],
                timeout=0,
            )
        injector.assert_not_called()
        assert mgr.has_pending("abc")


@pytest.mark.unit
class TestSocketClientsHearOfEveryRequest:
    """jarvis-app and phones list what waits; a TUI or desktop toast asking
    too must not keep a new request from them."""

    async def _request(self, mgr, desktop, tty=False):
        with (
            patch.object(ConfirmationManager, "_has_tty", return_value=tty),
            patch.object(
                ConfirmationManager, "_has_desktop_notifications", return_value=desktop
            ),
            patch.object(ConfirmationManager, "_notify_desktop") as toast,
            patch.object(ConfirmationManager, "_notify_cli") as cli,
        ):
            await mgr.request_confirmation(
                request_id="abc",
                tasks=[{"server": "s", "tool": "t"}],
                tools_needing_confirmation=_tools_needing_confirmation(["s.t"]),
                approved_tasks=[],
                denied_tools=[],
                timeout=0,
            )
            await asyncio.sleep(0)
        return toast, cli

    def _manager(self, clients=True):
        mgr = ConfirmationManager()
        mgr.set_event_injector(Mock())
        sent = []
        mgr.set_output_callback(sent.append, has_clients=lambda: clients)
        return mgr, sent

    @pytest.mark.asyncio
    async def test_beside_a_desktop_toast(self):
        mgr, sent = self._manager()
        toast, _ = await self._request(mgr, desktop=True)
        assert [m["id"] for m in sent] == ["abc"]
        assert sent[0]["type"] == "confirmation_request"
        toast.assert_called_once()

    @pytest.mark.asyncio
    async def test_beside_the_tui(self):
        mgr, sent = self._manager()
        tui = Mock()
        mgr.set_tui_callback(tui)
        with patch.object(ConfirmationManager, "_notify_tui") as modal:
            await self._request(mgr, desktop=False)
        assert [m["id"] for m in sent] == ["abc"]
        modal.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_socket_client_still_spares_the_terminal_prompt(self):
        mgr, sent = self._manager()
        _, cli = await self._request(mgr, desktop=False, tty=True)
        assert len(sent) == 1
        cli.assert_not_called()

    @pytest.mark.asyncio
    async def test_nothing_sent_without_clients(self):
        mgr, sent = self._manager(clients=False)
        _, cli = await self._request(mgr, desktop=False, tty=True)
        assert sent == []
        cli.assert_called_once()
