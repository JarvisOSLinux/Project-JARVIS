"""A TUI attached over the GUI socket is asked for confirmations ahead of a
desktop notification (#236). It replaces the in-process TUI callback.
"""

from unittest.mock import Mock, patch

import pytest

from jarvis.core.confirmation_manager import ConfirmationManager

DETAIL = {"tool_name": "shell_exec", "params": {"command": "rm -rf /tmp/x"}}


async def _request(mgr, **kw):
    await mgr.request_confirmation(
        request_id="r1",
        tasks=[],
        tools_needing_confirmation=[DETAIL],
        approved_tasks=[],
        denied_tools=[],
        **kw,
    )


@pytest.mark.unit
class TestInteractiveClientRouting:
    @pytest.mark.asyncio
    async def test_an_interactive_client_is_asked_over_the_socket_not_via_desktop(self):
        mgr = ConfirmationManager()
        sent = Mock()
        mgr.set_output_callback(
            sent, has_clients=lambda: True, has_interactive_clients=lambda: True
        )
        with (
            patch.object(mgr, "_has_desktop_notifications", return_value=True),
            patch.object(mgr, "_notify_desktop") as desktop,
        ):
            await _request(mgr)
        message = sent.call_args.args[0]
        assert message["type"] == "confirmation_request"
        assert message["id"] == "r1"
        assert message["details"][0]["command"]
        desktop.assert_not_called()

    @pytest.mark.asyncio
    async def test_without_an_interactive_client_the_desktop_notification_wins(self):
        mgr = ConfirmationManager()
        sent = Mock()
        mgr.set_output_callback(
            sent, has_clients=lambda: True, has_interactive_clients=lambda: False
        )
        with (
            patch.object(mgr, "_has_desktop_notifications", return_value=True),
            patch.object(mgr, "_notify_desktop") as desktop,
        ):
            await _request(mgr)
            await __import__("asyncio").sleep(0)
        desktop.assert_called_once()
        sent.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_hook_registered_means_the_old_channel_order(self):
        mgr = ConfirmationManager()
        sent = Mock()
        mgr.set_output_callback(sent, has_clients=lambda: True)
        with (patch.object(mgr, "_has_desktop_notifications", return_value=False),):
            await _request(mgr)
        assert sent.call_args.args[0]["type"] == "confirmation_request"

    @pytest.mark.asyncio
    async def test_the_request_stays_pending_for_review(self):
        mgr = ConfirmationManager()
        mgr.set_output_callback(
            Mock(), has_clients=lambda: True, has_interactive_clients=lambda: True
        )
        await _request(mgr)
        assert [p["id"] for p in mgr.list_pending()] == ["r1"]
