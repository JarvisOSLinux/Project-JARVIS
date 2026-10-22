"""JARVIS says what it is about to do (#273).

Asked to do something, JARVIS went silent until it had an answer. The model
may now add a one-line "comment" to any action; it goes out like a reply
(shown, saved, spoken in voice mode) before the action runs. A setting in the
Voice section silences the spoken part.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.config import Config
from jarvis.core.command_parser import TaskParser
from jarvis.core.output_manager import OutputManager
from jarvis.runtime import root_actions


def _app():
    return SimpleNamespace(
        task_parser=TaskParser(),
        output_manager=Mock(spec=OutputManager),
        goals=SimpleNamespace(dismiss_completed=Mock(return_value=[])),
        llm=Mock(),
        mcp_dispatch_docs={},
    )


def _act(app, response):
    order = []
    app.output_manager.handle_response.side_effect = lambda r: order.append(("said", r))

    async def search(*_a, **_k):
        order.append(("searched", None))

    with (
        patch.object(root_actions, "_handle_search_tools", search),
        patch.object(root_actions, "emit_activity"),
        patch.object(
            root_actions,
            "persist_assistant_turn",
            lambda a, text: order.append(("saved", text)),
        ),
        patch.object(root_actions, "apply_goal_updates"),
    ):
        asyncio.run(root_actions.act_on_root_response(app, Mock(), response, 0, 5))
    return order


@pytest.mark.unit
class TestComment:
    def test_it_is_said_and_saved_before_the_action_runs(self):
        order = _act(
            _app(),
            {
                "action": "search_tools",
                "capability": "calendar",
                "comment": "Let me find a calendar tool for you.",
            },
        )
        assert order == [
            (
                "said",
                {"output": "Let me find a calendar tool for you.", "comment": True},
            ),
            ("saved", "Let me find a calendar tool for you."),
            ("searched", None),
        ]

    def test_a_respond_speaks_for_itself(self):
        order = _act(
            _app(),
            {"action": "respond", "output": "Hello!", "comment": "Let me say hello."},
        )
        assert order == [("said", {"output": "Hello!"}), ("saved", "Hello!")]

    @pytest.mark.parametrize("comment", [None, "", "   ", 42, ["a list"]])
    def test_no_usable_comment_adds_nothing(self, comment):
        response = {"action": "search_tools", "capability": "calendar"}
        if comment is not None:
            response["comment"] = comment
        assert _act(_app(), response) == [("searched", None)]

    def test_a_long_comment_is_kept_to_one_short_line(self):
        order = _act(
            _app(),
            {
                "action": "search_tools",
                "capability": "x",
                "comment": "Checking\n\nyour   calendar " + "x" * 400,
            },
        )
        said = order[0][1]["output"]
        assert said.startswith("Checking your calendar ")
        assert len(said) == root_actions.COMMENT_MAX_CHARS and said.endswith("…")


@pytest.mark.unit
class TestSpeaking:
    def _speak(self, monkeypatch, response, speak_comments):
        monkeypatch.setattr(Config, "OUTPUT_MODE", "voice")
        monkeypatch.setattr(Config, "VOICE_SPEAK_COMMENTS", speak_comments)
        tts = Mock()
        manager = OutputManager(tts=tts, suppress_stdout=True)
        shown = []
        manager.add_output_callback(shown.append)
        manager.handle_response(response)
        return tts.say.call_args_list, shown

    def test_comments_are_spoken_by_default(self, monkeypatch):
        said, shown = self._speak(
            monkeypatch, {"output": "One moment.", "comment": True}, True
        )
        assert [c.args[0] for c in said] == ["One moment."]
        assert shown == [{"output": "One moment.", "comment": True}]

    def test_silenced_comments_are_still_shown(self, monkeypatch):
        said, shown = self._speak(
            monkeypatch, {"output": "One moment.", "comment": True}, False
        )
        assert said == []
        assert shown == [{"output": "One moment.", "comment": True}]

    def test_silencing_comments_never_silences_replies(self, monkeypatch):
        said, _ = self._speak(monkeypatch, {"output": "It's sunny."}, False)
        assert [c.args[0] for c in said] == ["It's sunny."]


@pytest.mark.unit
def test_the_root_prompts_ask_for_comments():
    from jarvis.core.system_info import SystemInfo

    info = SystemInfo.get_system_info()
    fmt = dict(
        system=info["system"],
        release=info["release"],
        version=info["version"],
        machine=info["machine"],
        shell=info["shell"],
        data_consent_note=Config.DATA_CONSENT_NOTE,
    )
    for prompt in (Config.LLM_ROOT_PROMPT, Config.LLM_ROOT_PROMPT_NO_CONTEXTOR):
        text = prompt.format(**fmt)
        assert '"comment": "Let me find' in text
        assert "never what happened" in text


@pytest.mark.unit
def test_the_voice_settings_offer_to_silence_comments():
    pytest.importorskip("textual")
    from jarvis.tui.config_modal import _SETTINGS

    (setting,) = [s for s in _SETTINGS if s.key == "VOICE_SPEAK_COMMENTS"]
    assert setting.category == "Voice" and setting.kind == "bool"
