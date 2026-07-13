"""Tests for the first-class embed() capability on BaseLLMProvider,
OllamaProvider, APIProvider, and ProviderPool (#180).
"""

from unittest.mock import MagicMock

import pytest

from jarvis.llm.base import BaseLLMProvider
from jarvis.llm.provider_pool import ProviderEntry, ProviderPool
from jarvis.llm.providers.api import APIProvider
from jarvis.llm.providers.ollama import OllamaProvider


class _MinimalProvider(BaseLLMProvider):
    """Bare-bones provider that only implements the abstract methods."""

    def chat(self, messages):
        return "ok"

    def is_available(self):
        return True


class TestBaseLLMProviderDefault:
    def test_embed_not_implemented_by_default(self):
        provider = _MinimalProvider("test-model")
        with pytest.raises(NotImplementedError, match="does not support embeddings"):
            provider.embed(["hello"])


class TestOllamaProviderEmbed:
    def test_embed_calls_client_per_text(self, monkeypatch):
        provider = OllamaProvider("nomic-embed-text")
        monkeypatch.setattr(provider, "_maybe_autostart", lambda: None)
        monkeypatch.setattr(provider, "_ensure_client", lambda: None)

        mock_client = MagicMock()
        mock_client.embeddings.side_effect = [
            {"embedding": [0.1, 0.2]},
            {"embedding": [0.3, 0.4]},
        ]
        provider._client = mock_client

        result = provider.embed(["a", "b"])

        assert result == [[0.1, 0.2], [0.3, 0.4]]
        assert mock_client.embeddings.call_count == 2
        mock_client.embeddings.assert_any_call(model="nomic-embed-text", prompt="a")


class TestAPIProviderEmbed:
    def test_embed_posts_and_sorts_by_index(self, monkeypatch):
        provider = APIProvider(
            model="text-embedding-3-small", api_url="http://localhost:8080"
        )
        monkeypatch.setattr(provider, "_ensure_client", lambda: None)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.9, 0.9], "index": 1},
                {"embedding": [0.1, 0.1], "index": 0},
            ]
        }
        mock_response.raise_for_status.return_value = None

        mock_client_cm = MagicMock()
        mock_client_cm.__enter__.return_value.post.return_value = mock_response

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client_cm
        mock_httpx.HTTPError = Exception
        provider._httpx = mock_httpx

        result = provider.embed(["a", "b"])

        assert result == [[0.1, 0.1], [0.9, 0.9]]


class TestProviderPoolEmbed:
    def test_falls_through_unsupported_to_supporting_provider(self):
        chat_only = _MinimalProvider("chat-model")
        embed_capable = _MinimalProvider("embed-model")
        embed_capable.embed = lambda texts: [[1.0] for _ in texts]  # type: ignore

        pool = ProviderPool(
            [
                ProviderEntry(provider=chat_only, name="chat-only"),
                ProviderEntry(provider=embed_capable, name="embed-capable"),
            ]
        )

        result = pool.embed(["x"])
        assert result == [[1.0]]

    def test_raises_not_implemented_when_no_provider_supports_it(self):
        pool = ProviderPool(
            [ProviderEntry(provider=_MinimalProvider("m"), name="only")]
        )
        with pytest.raises(NotImplementedError, match="No active provider"):
            pool.embed(["x"])
