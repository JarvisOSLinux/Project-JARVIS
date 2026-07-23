"""Model-agnostic vision (#204).

Covers: image message conversion for both providers (including the
no-images regression case — text-only payloads must serialize exactly as
before), ProviderPool require_vision routing/failover, analyze_image
parsing, provider vision-flag CRUD, and the ROOT analyze_image handler.
"""

import asyncio
import base64
import logging
from unittest.mock import Mock

import pytest

from jarvis.config import Config
from jarvis.core import providers as providers_mod
from jarvis.core.command_parser import TaskParser
from jarvis.llm.base import BaseLLMProvider
from jarvis.llm.provider_pool import (
    NoVisionProviderError,
    ProviderEntry,
    ProviderPool,
)
from jarvis.llm.providers.api import APIProvider
from jarvis.llm.providers.ollama import OllamaProvider
from jarvis.runtime import root_actions

_LOG = logging.getLogger("test")

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-payload"
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"fake-jpeg-payload"
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBP" + b"fake-webp-payload"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ------------------------------------------------------------------
# OllamaProvider message conversion
# ------------------------------------------------------------------


def _ollama_with_mock_client():
    provider = OllamaProvider(model="llama3.2-vision")
    provider._maybe_autostart = lambda: None
    client = Mock()
    client.chat = Mock(return_value={"message": {"content": "ok"}})
    provider._client = client
    return provider, client


@pytest.mark.integration
class TestOllamaImageMessages:
    def test_text_only_payload_unchanged(self):
        provider, client = _ollama_with_mock_client()
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "hi"},
        ]

        result = provider.chat(messages)

        assert result == "ok"
        sent = client.chat.call_args.kwargs["messages"]
        assert sent is messages
        assert sent == [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "hi"},
        ]

    def test_image_message_becomes_base64(self, tmp_path):
        provider, client = _ollama_with_mock_client()
        img = tmp_path / "photo.png"
        img.write_bytes(PNG_BYTES)
        messages = [{"role": "user", "content": "what is this", "images": [str(img)]}]

        provider.chat(messages)

        sent = client.chat.call_args.kwargs["messages"]
        assert sent[0] == {
            "role": "user",
            "content": "what is this",
            "images": [_b64(PNG_BYTES)],
        }
        assert messages[0]["images"] == [str(img)]

    def test_mixed_messages_only_rewrite_image_ones(self, tmp_path):
        provider, client = _ollama_with_mock_client()
        img = tmp_path / "photo.png"
        img.write_bytes(PNG_BYTES)
        text_msg = {"role": "system", "content": "s"}
        image_msg = {"role": "user", "content": "look", "images": [str(img)]}

        provider.chat([text_msg, image_msg])

        sent = client.chat.call_args.kwargs["messages"]
        assert sent[0] is text_msg
        assert sent[1]["images"] == [_b64(PNG_BYTES)]


# ------------------------------------------------------------------
# APIProvider message conversion
# ------------------------------------------------------------------


class _FakeHTTPXResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHTTPXClient:
    def __init__(self, sink, payload):
        self._sink = sink
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        self._sink["url"] = url
        self._sink["json"] = json
        return _FakeHTTPXResponse(self._payload)


class _FakeHTTPXModule:
    class HTTPError(Exception):
        pass

    def __init__(self, sink, payload):
        self._sink = sink
        self._payload = payload

    def Client(self, timeout=None):
        return _FakeHTTPXClient(self._sink, self._payload)


def _api_with_fake_transport():
    provider = APIProvider(model="gpt-4o", api_url="https://api.test", api_key="sk-x")
    sink = {}
    provider._httpx = _FakeHTTPXModule(
        sink,
        {"choices": [{"message": {"content": "desc"}}], "usage": {}},
    )
    return provider, sink


@pytest.mark.integration
class TestAPIImageMessages:
    def test_text_only_payload_unchanged(self):
        provider, sink = _api_with_fake_transport()
        messages = [{"role": "user", "content": "hi"}]

        result = provider.chat(messages)

        assert result == "desc"
        assert sink["json"] == {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        }
        assert sink["json"]["messages"] is messages

    def test_image_message_becomes_content_parts(self, tmp_path):
        provider, sink = _api_with_fake_transport()
        img = tmp_path / "photo.png"
        img.write_bytes(PNG_BYTES)
        messages = [{"role": "user", "content": "what is this", "images": [str(img)]}]

        provider.chat(messages)

        expected_url = f"data:image/png;base64,{_b64(PNG_BYTES)}"
        assert sink["json"]["messages"][0] == {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image_url", "image_url": {"url": expected_url}},
            ],
        }
        assert messages[0]["images"] == [str(img)]

    def test_format_detected_from_magic_over_extension(self, tmp_path):
        provider, sink = _api_with_fake_transport()
        img = tmp_path / "mislabeled.png"
        img.write_bytes(JPEG_BYTES)

        provider.chat([{"role": "user", "content": "x", "images": [str(img)]}])

        url = sink["json"]["messages"][0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")

    def test_webp_magic_detected(self, tmp_path):
        provider, sink = _api_with_fake_transport()
        img = tmp_path / "pic.bin"
        img.write_bytes(WEBP_BYTES)

        provider.chat([{"role": "user", "content": "x", "images": [str(img)]}])

        url = sink["json"]["messages"][0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/webp;base64,")

    def test_extension_fallback_when_magic_unknown(self, tmp_path):
        provider, sink = _api_with_fake_transport()
        img = tmp_path / "pic.webp"
        img.write_bytes(b"no-known-magic-here")

        provider.chat([{"role": "user", "content": "x", "images": [str(img)]}])

        url = sink["json"]["messages"][0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/webp;base64,")


# ------------------------------------------------------------------
# ProviderPool require_vision routing
# ------------------------------------------------------------------


class _ScriptedProvider(BaseLLMProvider):
    def __init__(self, model="m", reply="ok", fail=False):
        super().__init__(model)
        self.reply = reply
        self.fail = fail
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("boom (status code: 500)")
        return self.reply

    def is_available(self):
        return True


@pytest.mark.integration
class TestPoolVisionRouting:
    def test_require_vision_skips_non_vision_entries(self):
        text = _ScriptedProvider(reply="text")
        vis = _ScriptedProvider(reply="seen")
        pool = ProviderPool(
            [
                ProviderEntry(provider=text, name="text"),
                ProviderEntry(provider=vis, name="vis", vision=True),
            ]
        )

        result = pool.chat([{"role": "user", "content": "x"}], require_vision=True)

        assert result == "seen"
        assert text.calls == []

    def test_require_vision_failover_between_vision_entries(self):
        text = _ScriptedProvider(reply="text")
        vis_bad = _ScriptedProvider(fail=True)
        vis_ok = _ScriptedProvider(reply="second")
        pool = ProviderPool(
            [
                ProviderEntry(provider=text, name="text"),
                ProviderEntry(provider=vis_bad, name="vis-bad", vision=True),
                ProviderEntry(provider=vis_ok, name="vis-ok", vision=True),
            ]
        )

        result = pool.chat([{"role": "user", "content": "x"}], require_vision=True)

        assert result == "second"
        assert text.calls == []
        assert pool.entries[1].status == "cooldown"

    def test_no_vision_entry_raises_distinct_error(self):
        text = _ScriptedProvider(reply="text")
        pool = ProviderPool([ProviderEntry(provider=text, name="text")])

        with pytest.raises(NoVisionProviderError):
            pool.chat([{"role": "user", "content": "x"}], require_vision=True)
        assert text.calls == []

    def test_all_vision_entries_failing_raises_distinct_error(self):
        vis_bad = _ScriptedProvider(fail=True)
        pool = ProviderPool(
            [ProviderEntry(provider=vis_bad, name="vis-bad", vision=True)]
        )

        with pytest.raises(NoVisionProviderError):
            pool.chat([{"role": "user", "content": "x"}], require_vision=True)

    def test_text_chat_ignores_vision_flags(self):
        text = _ScriptedProvider(reply="text")
        vis = _ScriptedProvider(reply="seen")
        pool = ProviderPool(
            [
                ProviderEntry(provider=text, name="text"),
                ProviderEntry(provider=vis, name="vis", vision=True),
            ]
        )
        messages = [{"role": "user", "content": "x"}]

        result = pool.chat(messages)

        assert result == "text"
        assert text.calls[0] is messages
        assert vis.calls == []


# ------------------------------------------------------------------
# TaskParser: analyze_image
# ------------------------------------------------------------------


@pytest.mark.integration
class TestAnalyzeImageParsing:
    def test_parse_with_default_query(self):
        result = TaskParser().parse(
            {"action": "analyze_image", "path": "/tmp/photo.png"}
        )

        assert result["action"] == "analyze_image"
        assert result["path"] == "/tmp/photo.png"
        assert result["query"] == "Describe this image in detail."
        assert result["goal_updates"] == []

    def test_parse_with_custom_query(self):
        result = TaskParser().parse(
            {
                "action": "analyze_image",
                "path": "/tmp/photo.png",
                "query": "how many cats?",
                "goal_updates": [{"id": "g1", "status": "completed"}],
            }
        )

        assert result["query"] == "how many cats?"
        assert result["goal_updates"][0]["id"] == "g1"

    def test_parse_missing_path_returns_error(self):
        result = TaskParser().parse({"action": "analyze_image"})

        assert "error" in result


# ------------------------------------------------------------------
# Provider CRUD: vision flag
# ------------------------------------------------------------------


@pytest.mark.integration
class TestProviderVisionFlag:
    @pytest.fixture(autouse=True)
    def _providers_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Config, "PROVIDERS_FILE", str(tmp_path / "providers.json"))

    def test_add_provider_with_vision(self):
        providers_mod.add_provider("ollama", "llama3.2-vision", vision=True)

        entry = providers_mod.list_providers()[0]
        assert entry["vision"] is True

    def test_add_provider_defaults_to_no_vision(self):
        providers_mod.add_provider("ollama", "qwen3:4b")

        entry = providers_mod.list_providers()[0]
        assert entry.get("vision", False) is False

    def test_edit_provider_vision_coerces_strings(self):
        name, _ = providers_mod.add_provider("ollama", "llama3.2-vision")

        providers_mod.edit_provider(name, vision="true")
        assert providers_mod.list_providers()[0]["vision"] is True

        providers_mod.edit_provider(name, vision="false")
        assert providers_mod.list_providers()[0]["vision"] is False


# ------------------------------------------------------------------
# ROOT analyze_image handler
# ------------------------------------------------------------------


class _StubPool:
    def __init__(self, reply="a red square", error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def chat(self, messages, require_vision=False):
        self.calls.append((messages, require_vision))
        if self.error is not None:
            raise self.error
        return self.reply


def _make_app(pool):
    app = Mock()
    app.llm = Mock()
    app.llm.provider = pool
    return app


def _run_handler(app, parsed):
    asyncio.run(
        root_actions._handle_analyze_image(
            app, _LOG, parsed, depth=0, max_chain_depth=15
        )
    )


def _parsed(path, query="what is this?"):
    return {"action": "analyze_image", "path": path, "query": query}


@pytest.mark.integration
class TestAnalyzeImageHandler:
    @pytest.fixture
    def fed(self, monkeypatch):
        calls = []

        async def _capture(app, logger, label, summary, depth):
            calls.append((label, summary))

        monkeypatch.setattr(root_actions, "feed_root_summary", _capture)
        monkeypatch.setattr(root_actions, "emit_activity", lambda *a, **k: None)
        monkeypatch.setattr(Config, "VISION_ENABLED", True)
        return calls

    def test_disabled_toggle(self, fed, monkeypatch, tmp_path):
        monkeypatch.setattr(Config, "VISION_ENABLED", False)
        img = tmp_path / "photo.png"
        img.write_bytes(PNG_BYTES)
        pool = _StubPool()

        _run_handler(_make_app(pool), _parsed(str(img)))

        label, summary = fed[0]
        assert label == "VISION_ERROR"
        assert "vision is disabled" in summary
        assert pool.calls == []

    def test_missing_file(self, fed, tmp_path):
        pool = _StubPool()

        _run_handler(_make_app(pool), _parsed(str(tmp_path / "nope.png")))

        label, summary = fed[0]
        assert label == "VISION_ERROR"
        assert "file not found" in summary
        assert pool.calls == []

    def test_unsupported_extension(self, fed, tmp_path):
        bad = tmp_path / "notes.txt"
        bad.write_text("hello")
        pool = _StubPool()

        _run_handler(_make_app(pool), _parsed(str(bad)))

        label, summary = fed[0]
        assert label == "VISION_ERROR"
        assert "unsupported type" in summary
        assert pool.calls == []

    def test_oversized_file(self, fed, monkeypatch, tmp_path):
        monkeypatch.setattr(root_actions, "VISION_MAX_IMAGE_BYTES", 4)
        img = tmp_path / "big.png"
        img.write_bytes(PNG_BYTES)
        pool = _StubPool()

        _run_handler(_make_app(pool), _parsed(str(img)))

        label, summary = fed[0]
        assert label == "VISION_ERROR"
        assert "too large" in summary
        assert pool.calls == []

    def test_success_feeds_vision_result(self, fed, tmp_path):
        img = tmp_path / "photo.png"
        img.write_bytes(PNG_BYTES)
        pool = _StubPool(reply="a red square")

        _run_handler(_make_app(pool), _parsed(str(img), query="what color?"))

        assert fed[0] == ("VISION_RESULT", "a red square")
        messages, require_vision = pool.calls[0]
        assert require_vision is True
        assert messages == [
            {"role": "user", "content": "what color?", "images": [str(img)]}
        ]

    def test_no_vision_provider_mentions_cli_fix(self, fed, tmp_path):
        img = tmp_path / "photo.png"
        img.write_bytes(PNG_BYTES)
        pool = _StubPool(error=NoVisionProviderError("none"))

        _run_handler(_make_app(pool), _parsed(str(img)))

        label, summary = fed[0]
        assert label == "VISION_ERROR"
        assert "no vision-capable provider configured" in summary
        assert (
            "jarvis providers add --type ollama --model llama3.2-vision --vision"
            in summary
        )

    def test_provider_error_surfaces_message(self, fed, tmp_path):
        img = tmp_path / "photo.png"
        img.write_bytes(PNG_BYTES)
        pool = _StubPool(error=RuntimeError("model exploded"))

        _run_handler(_make_app(pool), _parsed(str(img)))

        assert fed[0] == ("VISION_ERROR", "model exploded")


# ------------------------------------------------------------------
# Prompt templates
# ------------------------------------------------------------------


@pytest.mark.integration
class TestVisionPrompts:
    def test_analyze_image_in_all_four_root_templates(self):
        for prompt in (
            Config.LLM_ROOT_PROMPT,
            Config.LLM_ROOT_PROMPT_UNIFIED,
            Config.LLM_ROOT_PROMPT_NO_CONTEXTOR,
            Config.LLM_ROOT_PROMPT_UNIFIED_NO_CONTEXTOR,
        ):
            assert '"action": "analyze_image"' in prompt
            assert "<absolute image path>" in prompt
