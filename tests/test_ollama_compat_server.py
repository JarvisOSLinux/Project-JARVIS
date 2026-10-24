"""Tests for the opt-in Ollama-compatible HTTP server.

Covers: off-by-default, the non-loopback guard, token only off-loopback, and
request round trips for the endpoints Ollama clients probe and use.
"""

import http.client
import json
import threading

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


def _request(server, method, path, body=None, token=None):
    host, port = server._httpd.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    payload = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    conn.request(method, path, body=payload, headers=headers)
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


class _FakeOutputManager:
    def __init__(self):
        self.callbacks = []

    def add_output_callback(self, cb):
        self.callbacks.append(cb)

    def remove_output_callback(self, cb):
        self.callbacks.remove(cb)

    def emit(self, response):
        for cb in list(self.callbacks):
            cb(response)


class _FakeEvents:
    """Stands in for the event loop: answers each injected input in a thread."""

    _running = True

    def __init__(self, output, replies):
        self.output = output
        self.replies = replies
        self.injected = []

    def inject_user_input(self, text):
        self.injected.append(text)
        replies = list(self.replies)
        threading.Thread(target=lambda: [self.output.emit(r) for r in replies]).start()


class _FakeApp:
    _running = True

    def __init__(self, replies):
        self.output_manager = _FakeOutputManager()
        self.events = _FakeEvents(self.output_manager, replies)


@pytest.fixture
def harness_server(stub_provider, tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "OPENAI_SERVER_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setattr(Config, "OLLAMA_SERVER_ENABLED", True)
    monkeypatch.setattr(Config, "OLLAMA_SERVER_HOST", "127.0.0.1")
    monkeypatch.setattr(Config, "OLLAMA_SERVER_PORT", 0)
    monkeypatch.setattr(Config, "OLLAMA_SERVER_ALLOW_NONLOCAL", False)
    monkeypatch.setattr(Config, "OLLAMA_SERVER_HARNESS_MODEL", "jarvis")
    monkeypatch.setattr(Config, "OLLAMA_SERVER_HARNESS_TIMEOUT", 2.0)

    app = _FakeApp(
        [
            {"output": "Let me check.", "comment": True},
            {"output": "It is sunny."},
        ]
    )
    server = OllamaCompatServer(stub_provider, app)
    server.start()
    assert server.is_running
    yield server, app
    server.stop()


class TestHarnessRouting:
    def _chat(self, server, model, token=None, stream=False):
        return _request(
            server,
            "POST",
            "/api/chat",
            {
                "model": model,
                "stream": stream,
                "messages": [
                    {"role": "user", "content": "old"},
                    {"role": "assistant", "content": "x"},
                    {"role": "user", "content": "weather?"},
                ],
            },
            token=token,
        )

    def test_tags_lists_harness_model_first(self, harness_server):
        server, _ = harness_server
        status, data = _request(server, "GET", "/api/tags")
        assert status == 200
        names = [m["name"] for m in json.loads(data)["models"]]
        assert names == ["jarvis", "stub-model"]

    def test_harness_model_requires_token_even_on_loopback(self, harness_server):
        server, app = harness_server
        status, _ = self._chat(server, "jarvis")
        assert status == 401
        assert app.events.injected == []

    def test_harness_turn_returns_notes_then_reply(self, harness_server):
        server, app = harness_server
        status, data = self._chat(server, "jarvis", token=server._httpd.token)
        assert status == 200
        content = json.loads(data)["message"]["content"]
        assert content == "Let me check.\n\nIt is sunny."
        assert app.events.injected == ["weather?"]

    def test_harness_turn_streams(self, harness_server):
        server, _ = harness_server
        status, data = self._chat(
            server, "jarvis:latest", token=server._httpd.token, stream=True
        )
        assert status == 200
        lines = [json.loads(line) for line in data.decode().splitlines()]
        assert [l["message"]["content"] for l in lines] == [
            "Let me check.\n\n",
            "It is sunny.",
            "",
        ]
        assert lines[-1]["done"] is True

    def test_plain_model_skips_harness_and_token(self, harness_server):
        server, app = harness_server
        status, data = self._chat(server, "stub-model")
        assert status == 200
        assert json.loads(data)["message"]["content"] == "hello from stub"
        assert app.events.injected == []

    def test_slash_command_refused(self, harness_server):
        server, app = harness_server
        status, data = _request(
            server,
            "POST",
            "/api/generate",
            {"model": "jarvis", "prompt": "/mode allow_all", "stream": False},
            token=server._httpd.token,
        )
        assert status == 500
        assert "slash" in json.loads(data)["error"]
        assert app.events.injected == []

    def test_timeout_when_no_reply(self, harness_server, monkeypatch):
        server, app = harness_server
        app.events.replies = []
        server._harness._timeout = 0.2
        status, data = self._chat(server, "jarvis", token=server._httpd.token)
        assert status == 500
        assert "no reply" in json.loads(data)["error"]

    def test_stop_unregisters_output_callback(self, harness_server):
        server, app = harness_server
        assert len(app.output_manager.callbacks) == 1
        server.stop()
        assert app.output_manager.callbacks == []
