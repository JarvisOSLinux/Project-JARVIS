"""A session keeps its action blocks, so a reopened chat shows them.

Live, the app and the TUI draw what JARVIS does as blocks (#271), but a
session's history held only text: reopening it, a reload of the app's window
or a switch of sessions brought back the messages without a single block, and
comments as plain messages.
"""

import os
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvis.contextor.adapter import ContextorAdapter
from jarvis.runtime import action_events
from jarvis.runtime import io as runtime_io
from jarvis.sessions.block_log import BlockLog
from jarvis.sessions.manager import SessionManager


def _app(blocks=None, current="s1", entries=()):
    contextor = Mock()
    contextor.recall.return_value = {"entries": list(entries)}
    sessions = SimpleNamespace(current_id=current, blocks=blocks or BlockLog())
    return SimpleNamespace(sessions=sessions, contextor=contextor, output_manager=None)


@pytest.mark.unit
class TestBlockLog:
    def test_blocks_are_kept_per_session_in_owner_only_files(self, tmp_path):
        log = BlockLog(str(tmp_path / "blocks"))
        log.append("s1", {"id": "tool:1"})
        log.append("s1", {"id": "tool:2"})
        log.append("s2", {"id": "tool:3"})
        assert log.read("s1", 10) == [{"id": "tool:1"}, {"id": "tool:2"}]
        assert log.read("s1", 1) == [{"id": "tool:2"}]
        mode = stat.S_IMODE(os.stat(tmp_path / "blocks" / "s1.jsonl").st_mode)
        assert mode == 0o600
        log.delete("s1")
        assert log.read("s1", 10) == []
        assert log.read("s2", 10) == [{"id": "tool:3"}]

    def test_a_broken_line_is_skipped(self, tmp_path):
        log = BlockLog(str(tmp_path))
        log.append("s1", {"id": "tool:1"})
        with open(tmp_path / "s1.jsonl", "a") as f:
            f.write('{"id": "tool:2", "out\n')
        log.append("s1", {"id": "tool:3"})
        assert [b["id"] for b in log.read("s1", 10)] == ["tool:1", "tool:3"]

    def test_an_id_that_is_not_one_names_no_file(self, tmp_path):
        log = BlockLog(str(tmp_path / "blocks"))
        log.append("../escape", {"id": "tool:1"})
        log.append("", {"id": "tool:2"})
        assert not (tmp_path / "escape.jsonl").exists()
        assert not (tmp_path / "blocks").exists()

    def test_deleting_a_session_deletes_its_blocks(self):
        contextor = Mock()
        contextor.delete_session.return_value = {"ok": True}
        manager = SessionManager(contextor, blocks=BlockLog())
        manager.blocks.append("s1", {"id": "tool:1"})
        assert manager.delete("s1")
        assert manager.blocks.read("s1", 10) == []


@pytest.mark.unit
class TestKeeping:
    def test_a_finished_block_is_kept_with_the_session_it_started_in(self):
        app = _app()
        action = action_events.start(app, "tool", "run_job (shell)", "pacman -Syu")
        app.sessions.current_id = "s2"
        action_events.end(app, action, "ok", "done")
        assert app.sessions.blocks.read("s2", 10) == []
        [kept] = app.sessions.blocks.read("s1", 10)
        assert kept["id"] == action
        assert (kept["kind"], kept["title"], kept["detail"]) == (
            "tool",
            "run_job (shell)",
            "pacman -Syu",
        )
        assert (kept["status"], kept["output"]) == ("ok", "done")
        assert kept["at"] >= kept["started_at"]

    def test_nothing_is_kept_without_a_session(self):
        app = _app(current=None)
        action_events.end(app, action_events.start(app, "search", "Search: x"))
        assert app.sessions.blocks._memory == {}

    def test_a_long_output_is_kept_by_its_end(self):
        app = _app()
        action = action_events.start(app, "tool", "t")
        action_events.end(app, action, "ok", "x" * 9_000 + "THE END")
        [kept] = app.sessions.blocks.read("s1", 10)
        assert kept["output"].endswith("THE END")
        assert len(kept["output"]) < 8_100

    def test_a_running_block_comes_with_its_output_so_far(self):
        app = _app()
        action = action_events.start(app, "tool", "run_job (shell)", "pacman -Syu")
        action_events.update(app, action, "downloading 3/40")
        app.sessions.current_id = "s2"
        action_events.start(app, "tool", "another session's")
        [running] = action_events.history(app, "s1", 10)
        assert (running["id"], running["status"]) == (action, "running")
        assert running["output"] == "downloading 3/40"


@pytest.mark.unit
class TestSessionHistory:
    def test_blocks_stand_where_they_started(self):
        app = _app(
            entries=[
                {
                    "content": "update",
                    "metadata": {"type": "user_prompt"},
                    "stored_at": 1,
                },
                {
                    "content": "Let me run it.",
                    "metadata": {"type": "assistant_reply", "comment": True},
                    "stored_at": 2,
                },
                {
                    "content": "Done.",
                    "metadata": {"type": "assistant_reply"},
                    "stored_at": 9,
                },
            ]
        )
        app.sessions.blocks.append(
            "s1",
            {"id": "tool:1", "kind": "tool", "status": "ok", "started_at": 3, "at": 8},
        )
        history = runtime_io.session_history(app, "s1")
        assert [m["role"] for m in history] == [
            "user",
            "assistant",
            "action",
            "assistant",
        ]
        assert history[1]["comment"] is True
        assert "comment" not in history[3]
        assert history[2]["timestamp"] == 3
        assert history[2]["id"] == "tool:1"

    def test_blocks_older_than_the_messages_shown_are_left_out(self):
        entries = [
            {
                "content": f"m{i}",
                "metadata": {"type": "user_prompt"},
                "stored_at": 10 + i,
            }
            for i in range(3)
        ]
        app = _app(entries=entries)
        app.sessions.blocks.append("s1", {"id": "old", "started_at": 5, "at": 6})
        app.sessions.blocks.append("s1", {"id": "new", "started_at": 11, "at": 12})
        ids = [m.get("id") for m in runtime_io.session_history(app, "s1", limit=3)]
        assert ids == [None, None, "new", None]
        # With the whole session shown, every block is in it.
        ids = [m.get("id") for m in runtime_io.session_history(app, "s1", limit=4)]
        assert ids == ["old", None, None, "new", None]

    def test_switching_sends_the_blocks(self):
        app = _app()
        session = SimpleNamespace(id="s1", to_dict=lambda: {"id": "s1"})
        app.sessions.available = True
        app.sessions.find = lambda _id: (session, "")
        app.sessions.switch = lambda _id: session
        app.sessions.blocks.append("s1", {"id": "tool:1", "started_at": 1, "at": 2})
        reply = runtime_io._handle_switch_session(app, {"id": "s1"})
        assert reply["messages"][0]["role"] == "action"


@pytest.mark.unit
def test_a_comment_is_stored_marked():
    adapter = ContextorAdapter()
    adapter._connected = True
    adapter._embed = lambda text: [0.1]
    sent = []
    adapter._send = lambda cmd: sent.append(cmd) or {"ok": True}
    adapter.auto_store_assistant_reply("Let me look.", session_id="s1", comment=True)
    adapter.auto_store_assistant_reply("Found it.", session_id="s1")
    assert sent[0]["metadata"] == {"type": "assistant_reply", "comment": True}
    assert sent[1]["metadata"] == {"type": "assistant_reply"}


def _tui(drive):
    import asyncio
    from unittest.mock import patch

    from jarvis.core.output_manager import OutputManager
    from jarvis.tui import app as tui_app
    from jarvis.tui import lifecycle as tui_lifecycle

    async def no_unmount(app):
        return None

    async def run():
        app = tui_app.JarvisTUI()
        manager = OutputManager(suppress_stdout=True)
        jarvis = _app(
            entries=[
                {
                    "content": "update",
                    "metadata": {"type": "user_prompt"},
                    "stored_at": 1,
                },
                {
                    "content": "Done.",
                    "metadata": {"type": "assistant_reply"},
                    "stored_at": 9,
                },
            ]
        )
        jarvis.contextor.is_connected = True
        jarvis.output_manager = manager
        jarvis.sessions.current = None
        jarvis.sessions.rename = Mock()
        app.jarvis = jarvis
        manager.add_output_callback(app._on_jarvis_output)
        manager.add_action_callback(app._on_jarvis_action)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            result = await drive(app, pilot)
            await pilot.pause()
            return result

    with (
        patch.object(tui_lifecycle, "on_mount", lambda app: None),
        patch.object(tui_lifecycle, "on_unmount", no_unmount),
    ):
        return asyncio.run(run())


@pytest.mark.unit
def test_the_tui_draws_a_sessions_blocks_and_a_running_one_still_ends():
    pytest.importorskip("textual")
    from jarvis.tui.chat_view import ChatView
    from jarvis.tui.session_sidebar import render_session_history

    async def drive(app, pilot):
        jarvis = app.jarvis
        jarvis.sessions.blocks.append(
            "s1",
            {
                "id": "tool:1",
                "kind": "tool",
                "title": "run_job (shell)",
                "detail": "pacman -Syu",
                "status": "ok",
                "output": "upgraded 40 packages",
                "started_at": 2,
                "at": 8,
            },
        )
        running = action_events.start(jarvis, "tool", "read_output (shell)")
        await pilot.pause()
        chat = app.query_one("#chat-log", ChatView)
        # As show_session does, coming back to the session.
        chat.clear()
        app._export_lines.clear()
        render_session_history(app, "s1")
        await pilot.pause()
        kept, live = chat.blocks()
        drawn = (str(kept._title.render()), str(kept._output.render()), live.status)
        action_events.end(jarvis, running, "ok", "more")
        await pilot.pause()
        return drawn, live.status, app._export_lines

    (title, output, before), after, lines = _tui(drive)
    assert title == "▸ ✓ run_job (shell) 6.0s pacman -Syu"
    assert output == "upgraded 40 packages"
    assert (before, after) == ("running", "ok")
    assert lines[:3] == [
        "you > update",
        "· run_job (shell): pacman -Syu",
        "  ok: upgraded 40 packages",
    ]
