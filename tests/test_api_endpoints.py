"""The API provider takes an address with or without its version path (#316).

It appended /v1 to every address, so LM Studio's default (…/v1) asked for
/v1/v1/chat/completions and Gemini's (…/v1beta/openai) could not be used.
"""

import pytest

from jarvis.llm.providers.api import APIProvider


@pytest.mark.parametrize(
    "url, endpoint",
    [
        (
            "https://integrate.api.nvidia.com",
            "https://integrate.api.nvidia.com/v1/chat/completions",
        ),
        (
            "https://integrate.api.nvidia.com/v1/",
            "https://integrate.api.nvidia.com/v1/chat/completions",
        ),
        ("http://localhost:1234/v1", "http://localhost:1234/v1/chat/completions"),
        (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        ),
        (
            "https://api.groq.com/openai",
            "https://api.groq.com/openai/v1/chat/completions",
        ),
        ("https://openrouter.ai/api", "https://openrouter.ai/api/v1/chat/completions"),
        ("http://localhost:11434", "http://localhost:11434/v1/chat/completions"),
    ],
)
def test_an_address_with_its_version_is_used_as_the_base(url, endpoint):
    assert APIProvider("m", api_url=url)._endpoint("chat/completions") == endpoint


def test_models_and_embeddings_follow_the_same_rule():
    provider = APIProvider("m", api_url="http://localhost:1234/v1")
    assert provider._endpoint("models") == "http://localhost:1234/v1/models"
    assert provider._endpoint("embeddings") == "http://localhost:1234/v1/embeddings"
