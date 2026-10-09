"""The confirmations panel: review pending and resolved requests from the
keyboard, attached or offline (#235). Headless against a fake daemon.
"""

import asyncio
import json
import sys
import time

import pytest

pytest.importorskip("textual")

from jarvis.tui.app import JarvisTUI
from jarvis.tui.confirmations_panel import ConfirmationsPanel
from tests.test_tui_thin_client import FakeDaemon, isolated, until  # noqa: F401

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="unix-socket fixture")


def pending(request_id="r1", lines=("shell: ls", "shell: rm x"), chat="s1-full-id"):
    return {
        "id": request_id,
        "tool_lines": list(lines),
        "created_at": time.time() - 90,
        "chat_session_id": chat,
        "session_id": "goal-1",
    }


def resolved(request_id="h1", outcome="approved", chat="s1-full-id"):
    return {
        "id": request_id,
        "tool_lines": ["shell: ls"],
        "resolved_at": time.time() - 30,
        "outcome": outcome,
        "approved": 1,
        "total": 1,
        "chat_session_id": chat,
    }


class PanelDaemon(FakeDaemon):
    def __init__(self, path, pend=(), hist=()):
        super().__init__(path)
        self.pend = list(pend)
        self.hist = list(hist)

    def replies(self, msg):
        t = msg["type"]
        if t == "list_confirmations":
            return [{"type": "confirmation_list", "confirmations": self.pend}]
        if t == "list_confirmation_history":
            return [{"type": "confirmation_history", "history": self.hist}]
        return super().replies(msg)


def panel_text(app):
    return str(app.query_one(ConfirmationsPanel).render())


async def attach(isolated, pend=(), hist=()):
    daemon = PanelDaemon(isolated / "jarvis.sock", pend, hist)
    await daemon.start()
    return daemon


@pytest.mark.asyncio
async def test_panel_is_hidden_until_there_is_something_to_review(isolated):
    daemon = await attach(isolated)
    app = JarvisTUI()
    async with app.run_test():
        await until(
            lambda: app.connected and "list_confirmation_history" in daemon.types()
        )
        assert app.query_one(ConfirmationsPanel).display is False
        await daemon.push({"type": "confirmation_list", "confirmations": [pending()]})
        await until(lambda: app.query_one(ConfirmationsPanel).display)
    await daemon.stop()


@pytest.mark.asyncio
async def test_shows_pending_with_tasks_and_history_with_outcomes(isolated):
    daemon = await attach(isolated, [pending()], [resolved(outcome="denied")])
    app = JarvisTUI()
    async with app.run_test():
        await until(lambda: "shell: rm x" in panel_text(app))
        text = panel_text(app)
        assert "PENDING (1)" in text and "[x] shell: ls" in text
        assert "HISTORY (1)" in text and "denied" in text
    await daemon.stop()


@pytest.mark.asyncio
async def test_tab_reaches_the_panel_and_escape_returns_to_the_input(isolated):
    daemon = await attach(isolated, [pending()])
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: app.query_one(ConfirmationsPanel).display)
        for _ in range(6):
            await pilot.press("tab")
            if isinstance(app.focused, ConfirmationsPanel):
                break
        assert isinstance(app.focused, ConfirmationsPanel)
        await pilot.press("escape")
        assert app.focused is app.query_one("#input")
    await daemon.stop()


async def focus_panel(app, pilot):
    await until(lambda: app.query_one(ConfirmationsPanel).display)
    app.query_one(ConfirmationsPanel).focus()
    await pilot.pause()


@pytest.mark.asyncio
async def test_y_on_a_request_approves_it_all(isolated):
    daemon = await attach(isolated, [pending()])
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await focus_panel(app, pilot)
        await pilot.press("y")
        await until(
            lambda: {"type": "approve_confirmation", "id": "r1"} in daemon.received
        )
    await daemon.stop()


@pytest.mark.asyncio
async def test_n_denies_the_whole_request_whatever_is_checked(isolated):
    daemon = await attach(isolated, [pending()])
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await focus_panel(app, pilot)
        await pilot.press("down", "space", "n")
        await until(
            lambda: {"type": "deny_confirmation", "id": "r1"} in daemon.received
        )
    await daemon.stop()


@pytest.mark.asyncio
async def test_unchecking_a_task_makes_apply_a_partial_approval(isolated):
    daemon = await attach(isolated, [pending()])
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await focus_panel(app, pilot)
        await pilot.press("down", "down", "space")
        await until(lambda: "[ ] shell: rm x" in panel_text(app))
        await pilot.press("enter")
        await until(
            lambda: {
                "type": "partial_approve_confirmation",
                "id": "r1",
                "approved_indices": [0],
            }
            in daemon.received
        )
    await daemon.stop()


@pytest.mark.asyncio
async def test_unchecking_every_task_then_applying_denies(isolated):
    daemon = await attach(isolated, [pending()])
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await focus_panel(app, pilot)
        await pilot.press("space")
        await until(
            lambda: "[ ] shell: ls" in panel_text(app)
            and "[ ] shell: rm x" in panel_text(app)
        )
        await pilot.press("y")
        await until(
            lambda: {"type": "deny_confirmation", "id": "r1"} in daemon.received
        )
    await daemon.stop()


@pytest.mark.asyncio
async def test_wasd_moves_like_the_arrows(isolated):
    daemon = await attach(isolated, [pending()])
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await focus_panel(app, pilot)
        await pilot.press("s", "s", "space")
        await until(lambda: "[ ] shell: rm x" in panel_text(app))
        await pilot.press("w", "w")
        assert app.query_one(ConfirmationsPanel).cursor == 0
    await daemon.stop()


@pytest.mark.asyncio
async def test_the_session_subtab_filters_to_the_active_chat_session(isolated):
    daemon = await attach(
        isolated,
        [pending("mine", chat="s1-full-id"), pending("theirs", chat="s2-full-id")],
        [resolved("hm", chat="s1-full-id"), resolved("ht", chat="s2-full-id")],
    )
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await focus_panel(app, pilot)
        await until(lambda: "theirs" in panel_text(app) and "mine" in panel_text(app))
        await pilot.press("right")
        await until(lambda: "theirs" not in panel_text(app))
        text = panel_text(app)
        assert "mine" in text and "PENDING (1)" in text and "HISTORY (1)" in text
        await pilot.press("left")
        await until(lambda: "theirs" in panel_text(app))
    await daemon.stop()


@pytest.mark.asyncio
async def test_history_rows_cannot_be_acted_on(isolated):
    daemon = await attach(isolated, [], [resolved()])
    app = JarvisTUI()
    async with app.run_test() as pilot:
        await focus_panel(app, pilot)
        await pilot.press("y", "n", "space")
        await asyncio.sleep(0.2)
        assert not any(m["type"].endswith("_confirmation") for m in daemon.received)
    await daemon.stop()


@pytest.mark.asyncio
async def test_a_request_resolved_elsewhere_leaves_the_panel_and_enters_history(
    isolated,
):
    daemon = await attach(isolated, [pending()])
    app = JarvisTUI()
    async with app.run_test():
        await until(lambda: "shell: rm x" in panel_text(app))
        await daemon.push({"type": "confirmation_list", "confirmations": []})
        await daemon.push({"type": "confirmation_history", "history": [resolved("r1")]})
        await until(
            lambda: "PENDING (0)" in panel_text(app)
            and "HISTORY (1)" in panel_text(app)
        )
    await daemon.stop()


@pytest.mark.asyncio
async def test_offline_the_panel_reads_disk_and_a_decision_goes_to_the_queue(
    isolated, monkeypatch
):
    from jarvis.core.confirmation_manager import (
        ConfirmationManager,
        decision_queue_path,
        store_path,
    )

    mgr = ConfirmationManager(store_path=str(store_path()))
    details = [
        {"tool_name": "shell", "params": {"command": "ls"}, "task": {"id": 1}},
        {"tool_name": "shell", "params": {"command": "rm x"}, "task": {"id": 2}},
    ]
    await mgr.request_confirmation(
        request_id="off1",
        tasks=[d["task"] for d in details],
        tools_needing_confirmation=details,
        approved_tasks=[],
        denied_tools=[],
        chat_session_id=None,
    )
    mgr.resolve({"id": "off1", "approved": True})
    await mgr.request_confirmation(
        request_id="off2",
        tasks=[d["task"] for d in details],
        tools_needing_confirmation=details,
        approved_tasks=[],
        denied_tools=[],
    )

    app = JarvisTUI()
    async with app.run_test() as pilot:
        await until(lambda: not app.connected and "off2" in panel_text(app))
        assert "HISTORY (1)" in panel_text(app)
        await focus_panel(app, pilot)
        await pilot.press("n")
        await until(lambda: decision_queue_path().exists())
        queued = json.loads(decision_queue_path().read_text())
        assert queued == [{"type": "deny_confirmation", "id": "off2"}]
        await until(lambda: "[queued: deny]" in panel_text(app))
