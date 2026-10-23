"""The TUI's controls over jarvis.sock, for jarvis-app and the phone (#267, phase 2).

Socket clients got replies and confirmations but not what JARVIS was doing:
the activity line and the action blocks were in-process only, and there was
no way to ask for the health check or the running work. Now:
- activity and action events are pushed to every GUI client;
- `get_status` answers with the /status sections, `list_work` with what F4
  shows, `move_provider` reorders providers like `/providers move`;
- a reply says whether it is a comment (JARVIS is still working, so the state
  stays busy) or a command's reply.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jarvis.core.output_manager import OutputManager
from jarvis.core.voice_state import VoiceState
from jarvis.runtime import action_events, io
from jarvis.runtime.health import Check, Section


class Writer:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.extend(json.loads(line) for line in data.decode().splitlines())

    async def drain(self):
        pass


def _app():
    writer = Writer()
    app = SimpleNamespace(
        _gui_clients={writer: "jarvis-app"},
        _gui_state="idle",
        output_manager=OutputManager(suppress_stdout=True),
        goals=SimpleNamespace(get_active_goals=lambda: []),
        dispatch=SimpleNamespace(is_connected=False),
        confirmation=SimpleNamespace(list_pending=lambda: []),
        setups=SimpleNamespace(list=lambda: []),
    )
    return app, writer


def _ask(app, writer, msg):
    asyncio.run(io._process_gui_message(app, None, msg, writer))
    return writer.sent[-1]


@pytest.mark.unit
class TestRequests:
    def test_get_status(self):
        app, writer = _app()
        sections = [Section("Engines", [Check("dispatch", "fail", "not connected")])]

        async def collect(a, full=False):
            assert full is True
            return sections

        with patch("jarvis.runtime.health.collect_status", collect):
            reply = _ask(app, writer, {"type": "get_status", "full": True})
        assert reply == {
            "type": "status",
            "full": True,
            "sections": [
                {
                    "title": "Engines",
                    "checks": [
                        {"name": "dispatch", "state": "fail", "detail": "not connected"}
                    ],
                }
            ],
        }

    def test_a_failing_status_check_still_answers(self):
        app, writer = _app()

        async def broken(a, full=False):
            raise RuntimeError("boom")

        with patch("jarvis.runtime.health.collect_status", broken):
            reply = _ask(app, writer, {"type": "get_status"})
        assert reply == {"type": "error", "message": "Status check failed: boom"}

    def test_list_work(self):
        app, writer = _app()
        reply = _ask(app, writer, {"type": "list_work"})
        assert reply["type"] == "work"
        assert set(reply["work"]) == {
            "requests",
            "tasks",
            "thinking",
            "confirmations",
            "setups",
        }

    def test_move_provider(self, tmp_path, monkeypatch):
        from jarvis.core import providers

        monkeypatch.setattr(
            providers.Config, "PROVIDERS_FILE", str(tmp_path / "providers.json")
        )
        for name in ("a", "b", "c"):
            providers.add_provider("ollama", "m", name=name)
        app, writer = _app()
        _ask(app, writer, {"type": "move_provider", "name": "c", "position": 1})
        listed = [m for m in writer.sent if m["type"] == "provider_list"][-1]
        assert [p["name"] for p in listed["providers"]] == ["c", "a", "b"]
        assert (
            _ask(app, writer, {"type": "move_provider", "name": "x", "position": 1})[
                "type"
            ]
            == "provider_error"
        )
        assert (
            "position"
            in _ask(app, writer, {"type": "move_provider", "name": "a"})["message"]
        )


@pytest.mark.unit
class TestPushed:
    def test_activity_and_actions_reach_every_client(self):
        app, writer = _app()
        app.output_manager.add_activity_callback(lambda e: io.on_gui_activity(app, e))
        app.output_manager.add_action_callback(lambda e: io.on_gui_action(app, e))

        async def run():
            app.output_manager.emit_activity("Searching for: calendar…", "dispatch")
            action = action_events.start(app, "search", "Search: calendar")
            action_events.end(app, action, "ok", "found 2")
            await asyncio.sleep(0)

        asyncio.run(run())
        kinds = [(m["type"], m.get("phase")) for m in writer.sent]
        assert kinds == [("activity", None), ("action", "start"), ("action", "end")]
        assert writer.sent[0] == {
            "type": "activity",
            "text": "Searching for: calendar…",
            "kind": "dispatch",
        }
        assert writer.sent[2]["output"] == "found 2"

    def test_nothing_is_built_without_clients(self):
        app, _ = _app()
        app._gui_clients = {}
        with patch.object(io, "broadcast_to_gui_clients") as broadcast:
            io.on_gui_activity(app, {"text": "x"})
        broadcast.assert_not_called()

    def _reply(self, response):
        app, writer = _app()
        app._gui_state = "processing"
        asyncio.run(io._gui_output_and_idle(app, response))
        return writer.sent, app._gui_state

    def test_a_comment_is_marked_and_jarvis_stays_busy(self):
        sent, state = self._reply({"output": "Let me check.", "comment": True})
        assert sent == [
            {
                "type": "response",
                "content": "Let me check.",
                "done": True,
                "comment": True,
            }
        ]
        assert state == "processing"

    def test_a_reply_ends_the_turn(self):
        sent, state = self._reply({"output": "Done."})
        assert sent[0] == {"type": "response", "content": "Done.", "done": True}
        assert state == VoiceState.IDLE.value

    def test_a_commands_reply_is_marked(self):
        sent, _ = self._reply({"output": "Switched.", "command": True})
        assert sent[0]["command"] is True
