"""Tests for the opt-in OpenAI-compatible HTTP server (#181).

Covers: off-by-default, the non-loopback guard, token generation/
persistence, and a full request round trip (auth required, /v1/models,
blocking and streaming /v1/chat/completions).
"""

import http.client
import json
import os

import pytest

from jarvis.config import Config
from jarvis.llm.base import BaseLLMProvider
from jarvis.server.openai_compat import OpenAICompatServer, _load_or_create_token


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
def token_file(tmp_path, monkeypatch):
    path = str(tmp_path / "openai_server_token")
    monkeypatch.setattr(Config, "OPENAI_SERVER_TOKEN_FILE", path)
    return path


class TestTokenPersistence:
    def test_creates_and_persists_token(self, token_file):
        token1 = _load_or_create_token()
        assert os.path.isfile(token_file)
        token2 = _load_or_create_token()
        assert token1 == token2

    def test_token_file_is_owner_only(self, token_file):
        _load_or_create_token()
        mode = os.stat(token_file).st_mode & 0o777
        assert mode == 0o600


class TestStartupGuards:
    def test_disabled_by_default_never_binds(self, stub_provider, monkeypatch):
        monkeypatch.setattr(Config, "OPENAI_SERVER_ENABLED", False)
        server = OpenAICompatServer(stub_provider)
        server.start()
        assert not server.is_running

    def test_nonloopback_refused_without_second_optin(self, stub_provider, monkeypatch):
        monkeypatch.setattr(Config, "OPENAI_SERVER_ENABLED", True)
        monkeypatch.setattr(Config, "OPENAI_SERVER_HOST", "0.0.0.0")
        monkeypatch.setattr(Config, "OPENAI_SERVER_ALLOW_NONLOCAL", False)
        server = OpenAICompatServer(stub_provider)
        server.start()
        assert not server.is_running


@pytest.fixture
def running_server(stub_provider, token_file, monkeypatch):
    monkeypatch.setattr(Config, "OPENAI_SERVER_ENABLED", True)
    monkeypatch.setattr(Config, "OPENAI_SERVER_HOST", "127.0.0.1")
    monkeypatch.setattr(Config, "OPENAI_SERVER_PORT", 0)  # OS picks a free port
    monkeypatch.setattr(Config, "OPENAI_SERVER_ALLOW_NONLOCAL", False)

    server = OpenAICompatServer(stub_provider)
    server.start()
    assert server.is_running
    yield server
    server.stop()


def _request(server, method, path, token=None, body=None):
    host, port = server._httpd.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode() if body is not None else None
    if payload is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


class TestRequestRoundTrip:
    def test_missing_auth_is_rejected(self, running_server):
        status, _ = _request(running_server, "GET", "/v1/models")
        assert status == 401

    def test_wrong_token_is_rejected(self, running_server):
        status, _ = _request(running_server, "GET", "/v1/models", token="wrong")
        assert status == 401

    def test_v1_models_lists_current_model(self, running_server):
        token = running_server._httpd.token
        status, data = _request(running_server, "GET", "/v1/models", token=token)
        assert status == 200
        result = json.loads(data)
        assert result["data"][0]["id"] == "stub-model"

    def test_blocking_chat_completion(self, running_server):
        token = running_server._httpd.token
        status, data = _request(
            running_server,
            "POST",
            "/v1/chat/completions",
            token=token,
            body={
                "model": "stub-model",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert status == 200
        result = json.loads(data)
        assert result["choices"][0]["message"]["content"] == "hello from stub"

    def test_streaming_chat_completion(self, running_server):
        token = running_server._httpd.token
        host, port = running_server._httpd.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps(
                {
                    "model": "stub-model",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        response = conn.getresponse()
        assert response.status == 200
        raw = response.read().decode()
        conn.close()

        assert "chunk-a" in raw
        assert "chunk-b" in raw
        assert raw.strip().endswith("data: [DONE]")

    def test_unknown_path_is_404(self, running_server):
        token = running_server._httpd.token
        status, _ = _request(running_server, "GET", "/v1/unknown", token=token)
        assert status == 404
