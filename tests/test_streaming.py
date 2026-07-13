"""Tests for stream_chat() on BaseLLMProvider, OllamaProvider, APIProvider,
and ProviderPool (#178).
"""

from unittest.mock import MagicMock

import pytest

from jarvis.llm.base import BaseLLMProvider
from jarvis.llm.provider_pool import ProviderEntry, ProviderPool
from jarvis.llm.providers.api import APIProvider
from jarvis.llm.providers.ollama import OllamaProvider


class _MinimalProvider(BaseLLMProvider):
    def chat(self, messages):
        return "buffered full response"

    def is_available(self):
        return True


class TestBaseLLMProviderDefaultStreaming:
    def test_falls_back_to_single_chunk(self):
        provider = _MinimalProvider("test-model")
        chunks = list(provider.stream_chat([{"role": "user", "content": "hi"}]))
        assert chunks == ["buffered full response"]


class TestOllamaProviderStreaming:
    def test_yields_content_deltas_and_captures_usage(self, monkeypatch):
        provider = OllamaProvider("qwen3:4b")
        monkeypatch.setattr(provider, "_maybe_autostart", lambda: None)
        monkeypatch.setattr(provider, "_ensure_client", lambda: None)

        mock_client = MagicMock()
        mock_client.chat.return_value = iter(
            [
                {"message": {"content": "Hel"}, "done": False},
                {"message": {"content": "lo"}, "done": False},
                {
                    "message": {"content": ""},
                    "done": True,
                    "prompt_eval_count": 10,
                    "eval_count": 2,
                },
            ]
        )
        provider._client = mock_client

        chunks = list(provider.stream_chat([{"role": "user", "content": "hi"}]))

        assert chunks == ["Hel", "lo"]
        assert provider.last_prompt_tokens == 10
        assert provider.last_completion_tokens == 2
        assert mock_client.chat.call_args.kwargs["stream"] is True


class TestAPIProviderStreaming:
    def test_parses_sse_deltas(self, monkeypatch):
        provider = APIProvider(model="gpt-4", api_url="http://localhost:8080")
        monkeypatch.setattr(provider, "_ensure_client", lambda: None)

        sse_lines = [
            'data: {"choices": [{"delta": {"content": "Hel"}}]}',
            'data: {"choices": [{"delta": {"content": "lo"}}]}',
            'data: {"choices": [{"delta": {}}], "usage": '
            '{"prompt_tokens": 5, "completion_tokens": 2}}',
            "data: [DONE]",
        ]

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_lines.return_value = iter(sse_lines)

        mock_stream_cm = MagicMock()
        mock_stream_cm.__enter__.return_value = mock_response

        mock_client_cm = MagicMock()
        mock_client_cm.__enter__.return_value.stream.return_value = mock_stream_cm

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client_cm
        mock_httpx.HTTPError = Exception
        provider._httpx = mock_httpx

        chunks = list(provider.stream_chat([{"role": "user", "content": "hi"}]))

        assert chunks == ["Hel", "lo"]
        assert provider.last_prompt_tokens == 5
        assert provider.last_completion_tokens == 2


class TestProviderPoolStreaming:
    def test_streams_from_first_active_provider(self):
        provider = _MinimalProvider("m")
        provider.stream_chat = lambda messages: iter(["a", "b", "c"])  # type: ignore
        pool = ProviderPool([ProviderEntry(provider=provider, name="only")])

        assert list(pool.stream_chat([{"role": "user", "content": "hi"}])) == [
            "a",
            "b",
            "c",
        ]

    def test_fails_over_before_any_chunk_yielded(self):
        def broken_stream(messages):
            raise RuntimeError("connection refused")
            yield  # pragma: no cover - never reached, makes this a generator

        broken = _MinimalProvider("broken")
        broken.stream_chat = broken_stream  # type: ignore

        healthy = _MinimalProvider("healthy")
        healthy.stream_chat = lambda messages: iter(["ok"])  # type: ignore

        pool = ProviderPool(
            [
                ProviderEntry(provider=broken, name="broken"),
                ProviderEntry(provider=healthy, name="healthy"),
            ]
        )

        assert list(pool.stream_chat([{"role": "user", "content": "hi"}])) == ["ok"]

    def test_mid_stream_error_propagates_without_failover(self):
        def flaky_stream(messages):
            yield "partial"
            raise RuntimeError("dropped connection")

        flaky = _MinimalProvider("flaky")
        flaky.stream_chat = flaky_stream  # type: ignore

        healthy = _MinimalProvider("healthy")
        healthy.stream_chat = lambda messages: iter(["should not reach"])  # type: ignore

        pool = ProviderPool(
            [
                ProviderEntry(provider=flaky, name="flaky"),
                ProviderEntry(provider=healthy, name="healthy"),
            ]
        )

        gen = pool.stream_chat([{"role": "user", "content": "hi"}])
        assert next(gen) == "partial"
        with pytest.raises(RuntimeError, match="dropped connection"):
            next(gen)
