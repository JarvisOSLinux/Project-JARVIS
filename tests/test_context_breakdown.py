"""/context breaks the last prompt down by part (#272).

It showed prompt, reply and total tokens. Now it shows what filled the
prompt: the instructions, the conversation summary, the exchanges kept in
full, and this turn's blocks (tool docs, search results, memories, goals, task
results, the user's message), scaled to the provider's token count, with the
free space when the window size is known.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvis.config import Config
from jarvis.llm.chat import LLM
from jarvis.llm.context_breakdown import (
    SUMMARY_MARKER,
    breakdown,
    message_parts,
    token_rows,
)
from jarvis.runtime.session_commands import context_report

TURN = "\n".join(
    [
        'GOALS: [{"id": "g1"}]',
        "SESSION_TITLE: Calendar",
        "RELEVANT MEMORIES:\n  [prefs] (relevance: 61%) short answers",
        "SERVER_DOCS: com.example.mcp.cal (2 tool(s))\n  list_events: …",
        'SEARCH_RESULTS (top 1 for "calendar"):\n  com.example.mcp.cal [INSTALLED]',
        "DISPATCH_RESULT: [hash=h] 200 <h>WARNING: a mirror failed\nall done</h>",
        "NEW INPUT: what's on my calendar?",
    ]
)


@pytest.mark.unit
class TestParts:
    def test_each_known_block_lands_in_its_part(self):
        parts = message_parts(TURN)
        for part in (
            "Goals",
            "Other",
            "Memories",
            "Tool docs",
            "Search results",
            "Task results",
            "Your message",
        ):
            assert parts[part] > 0, part
        assert parts["Your message"] == len("NEW INPUT: what's on my calendar?")

    def test_a_tools_own_upper_case_line_stays_in_its_block(self):
        # "WARNING:" is tool output inside DISPATCH_RESULT, not a block of its own.
        result = (
            "DISPATCH_RESULT: [hash=h] 200 <h>WARNING: a mirror failed\nall done</h>"
        )
        assert message_parts(result)["Task results"] == len(result)

    def test_the_system_prompt_splits_into_instructions_and_summary(self):
        messages = [
            {"role": "system", "content": "RULES" + SUMMARY_MARKER + "\nearlier talk"},
            {"role": "user", "content": "NEW INPUT: hi"},
            {"role": "assistant", "content": "{}"},
            {"role": "user", "content": "NEW INPUT: and now?"},
        ]
        got = breakdown(messages)
        assert got["chars"]["Instructions"] == len("RULES")
        assert got["chars"]["Conversation summary"] == len(
            SUMMARY_MARKER + "\nearlier talk"
        )
        assert got["chars"]["Recent conversation"] == len("NEW INPUT: hi") + len("{}")
        assert got["chars"]["Your message"] == len("NEW INPUT: and now?")
        assert got["exchanges_kept"] == 1

    def test_tokens_are_scaled_to_the_providers_count_or_estimated(self):
        chars = {"Instructions": 300, "Your message": 100}
        assert token_rows(chars, 1000) == [("Instructions", 750), ("Your message", 250)]
        assert token_rows(chars, 0) == [("Instructions", 75), ("Your message", 25)]


def _app(window_tokens, prompt_tokens=8000):
    messages = [
        {"role": "system", "content": "R" * 4000},
        {"role": "user", "content": TURN},
    ]
    llm = SimpleNamespace(
        last_breakdown=breakdown(messages),
        _root_window=3,
        provider=SimpleNamespace(
            last_prompt_tokens=prompt_tokens, last_completion_tokens=50
        ),
    )
    return SimpleNamespace(llm=llm, mcp_dispatch_docs={})


@pytest.mark.unit
class TestReport:
    def test_with_a_window_it_shows_shares_and_free_space(self, monkeypatch):
        monkeypatch.setattr(Config, "LLM_CONTEXT_WINDOW", 32000)
        text = context_report(_app(32000))
        assert text.startswith(
            "Context: 8,000 of 32,000 tokens (25%) in the last prompt"
        )
        assert "Instructions" in text and "Free space" in text and "24,000" in text
        assert "History: the last 3 exchanges stay in full" in text
        assert "scaled from their length" in text

    def test_without_a_window_it_says_how_to_get_the_share(self, monkeypatch):
        monkeypatch.setattr(Config, "LLM_CONTEXT_WINDOW", 0)
        text = context_report(_app(0))
        assert "Free space" not in text and "Set LLM_CONTEXT_WINDOW" in text

    def test_without_a_provider_count_it_says_it_estimated(self, monkeypatch):
        monkeypatch.setattr(Config, "LLM_CONTEXT_WINDOW", 0)
        assert "estimated from length" in context_report(_app(0, prompt_tokens=0))

    def test_before_any_call(self):
        app = SimpleNamespace(llm=SimpleNamespace(last_breakdown=None))
        assert context_report(app) == "Context: no model call yet."


@pytest.mark.unit
def test_every_root_call_records_its_breakdown():
    provider = Mock(model="test")
    provider.chat.side_effect = [
        '{"status": "ready"}',
        '{"action": "respond", "output": "hi"}',
    ]
    llm = LLM(provider=provider, prompts={"root": "RULES"})
    llm.ask("NEW INPUT: hello")
    assert llm.last_breakdown["chars"]["Instructions"] == len("RULES")
    assert llm.last_breakdown["chars"]["Your message"] == len("NEW INPUT: hello")
