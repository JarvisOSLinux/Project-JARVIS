"""When no model can be reached, say so, and why.

A DNS blip on the way to a cloud model reached the user as "I had trouble
formatting my response. Could you try again?", which pointed at the wrong
thing entirely.
"""

from unittest.mock import Mock

import pytest

from jarvis.llm import LLM
from jarvis.llm.chat import describe_provider_error

DNS = RuntimeError(
    "All LLM providers are unavailable:\n"
    '  general: cooldown (retry in 25s) — Post "https://ollama.com:443/api/chat?ts=1": '
    "dial tcp: lookup ollama.com: no such host (status code: 502)"
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "error, expected",
    [
        (
            DNS,
            "could not look up ollama.com: a DNS or network problem; retrying it in 25 s",
        ),
        (ConnectionError("Connection refused"), "is Ollama running?"),
        (RuntimeError("Read timed out"), "did not answer in time"),
        (RuntimeError("HTTP 401 Unauthorized"), "rejected the API key"),
        (RuntimeError("HTTP 429 Too Many Requests"), "rate-limiting"),
        (
            RuntimeError("something odd\nthe last line says why"),
            "the last line says why",
        ),
    ],
)
def test_reasons(error, expected):
    assert expected in describe_provider_error(error)


@pytest.mark.unit
def test_an_unreachable_model_is_reported_as_such():
    provider = Mock(model="test")
    provider.chat.side_effect = ['{"status": "ready"}', DNS]  # preload, then the ask
    llm = LLM(provider=provider, prompts={"root": "test"})
    result = llm.ask("what's on my calendar?")
    assert result["action"] == "respond"
    assert "can't reach my language model" in result["output"]
    assert "ollama.com" in result["output"]
    assert "formatting" not in result["output"]
