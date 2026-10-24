"""Tests for the opt-in Ollama-compatible HTTP server.

Covers: off-by-default, the non-loopback guard, token only off-loopback, and
request round trips for the endpoints Ollama clients probe and use.
"""

import http.client
import json

import pytest

from jarvis.config import Config
from jarvis.llm.base import BaseLLMProvider
from jarvis.server.ollama_compat import OllamaCompatServer


class _StubProvider(BaseLLMProvider):
    def chat(self, messages):
        return "hello from stub"

    def is_available(self):
        return True

    def stream_chat(self, messages):
        yield "chunk-a"
        yield "chunk-b"


@pytest.fixture
def stub_provider():
    return _StubProvider("stub-model")


@pytest.fixture
def running_server(stub_provider, tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "OLLAMA_SERVER_ENABLED", True)
    monkeypatch.setattr(Config, "OLLAMA_SERVER_HOST", "127.0.0.1")
    monkeypatch.setattr(Config, "OLLAMA_SERVER_PORT", 0)  # OS picks a free port
    monkeypatch.setattr(Config, "OLLAMA_SERVER_ALLOW_NONLOCAL", False)

    server = OllamaCompatServer(stub_provider)
    server.start()
    assert server.is_running
    yield server
    server.stop()


def _request(server, method, path, body=None):
    host, port = server._httpd.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    payload = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, body=payload)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


def test_default_port_is_11456():
    assert Config.OLLAMA_SERVER_PORT == 11456


class TestStartupGuards:
    def test_disabled_by_default_never_binds(self, stub_provider, monkeypatch):
        monkeypatch.setattr(Config, "OLLAMA_SERVER_ENABLED", False)
        server = OllamaCompatServer(stub_provider)
        server.start()
        assert not server.is_running

    def test_nonloopback_refused_without_second_optin(self, stub_provider, monkeypatch):
        monkeypatch.setattr(Config, "OLLAMA_SERVER_ENABLED", True)
        monkeypatch.setattr(Config, "OLLAMA_SERVER_HOST", "0.0.0.0")
        monkeypatch.setattr(Config, "OLLAMA_SERVER_ALLOW_NONLOCAL", False)
        server = OllamaCompatServer(stub_provider)
        server.start()
        assert not server.is_running

    def test_nonloopback_requires_token(self, stub_provider, tmp_path, monkeypatch):
        monkeypatch.setattr(Config, "OPENAI_SERVER_TOKEN_FILE", str(tmp_path / "token"))
        monkeypatch.setattr(Config, "OLLAMA_SERVER_ENABLED", True)
        monkeypatch.setattr(Config, "OLLAMA_SERVER_HOST", "0.0.0.0")
        monkeypatch.setattr(Config, "OLLAMA_SERVER_PORT", 0)
        monkeypatch.setattr(Config, "OLLAMA_SERVER_ALLOW_NONLOCAL", True)
        server = OllamaCompatServer(stub_provider)
        server.start()
        try:
            assert server._httpd.token
            status, _ = _request(server, "GET", "/api/tags")
            assert status == 401
            status, _ = _request(server, "GET", "/")
            assert status == 200
        finally:
            server.stop()


class TestEndpoints:
    def test_root_banner(self, running_server):
        status, data = _request(running_server, "GET", "/")
        assert status == 200
        assert data == b"Ollama is running"

    def test_version(self, running_server):
        status, data = _request(running_server, "GET", "/api/version")
        assert status == 200
        assert "version" in json.loads(data)

    def test_tags_lists_current_model(self, running_server):
        status, data = _request(running_server, "GET", "/api/tags")
        assert status == 200
        assert json.loads(data)["models"][0]["name"] == "stub-model"

    def test_unknown_path_404(self, running_server):
        status, _ = _request(running_server, "GET", "/api/nope")
        assert status == 404

    def test_chat_non_streaming(self, running_server):
        status, data = _request(
            running_server,
            "POST",
            "/api/chat",
            {"messages": [{"role": "user", "content": "hi"}], "stream": False},
        )
        assert status == 200
        result = json.loads(data)
        assert result["message"] == {"role": "assistant", "content": "hello from stub"}
        assert result["done"] is True

    def test_chat_streams_ndjson_by_default(self, running_server):
        status, data = _request(
            running_server,
            "POST",
            "/api/chat",
            {"messages": [{"role": "user", "content": "hi"}]},
        )
        assert status == 200
        lines = [json.loads(line) for line in data.decode().splitlines()]
        assert [l["message"]["content"] for l in lines] == ["chunk-a", "chunk-b", ""]
        assert [l["done"] for l in lines] == [False, False, True]

    def test_generate_non_streaming(self, running_server):
        status, data = _request(
            running_server,
            "POST",
            "/api/generate",
            {"prompt": "hi", "system": "be brief", "stream": False},
        )
        assert status == 200
        result = json.loads(data)
        assert result["response"] == "hello from stub"
        assert result["done"] is True

    def test_show(self, running_server):
        status, data = _request(
            running_server, "POST", "/api/show", {"model": "stub-model"}
        )
        assert status == 200
        assert "details" in json.loads(data)

    def test_invalid_json_400(self, running_server):
        host, port = running_server._httpd.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("POST", "/api/chat", body=b"{not json")
        assert conn.getresponse().status == 400
        conn.close()
