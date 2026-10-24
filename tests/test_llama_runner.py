"""Tests for the llama-server supervisor and the llamacpp provider."""

import subprocess
from unittest.mock import MagicMock

import pytest

from jarvis.config import Config
from jarvis.llm import runner
from jarvis.llm.providers import create_provider
from jarvis.llm.providers.llamacpp import LlamaCppProvider


@pytest.fixture(autouse=True)
def _clean_registry():
    runner._servers.clear()
    yield
    runner._servers.clear()


class TestCommand:
    def test_hugging_face_spec_uses_hf_flag(self):
        server = runner.LlamaServer("Qwen/Qwen3-4B-GGUF:Q4_K_M", 11445)
        cmd = server._command("llama-server")
        assert cmd[cmd.index("-hf") + 1] == "Qwen/Qwen3-4B-GGUF:Q4_K_M"
        assert "-m" not in cmd
        assert "--jinja" in cmd
        assert "--embedding" not in cmd

    def test_existing_file_uses_m_flag(self, tmp_path):
        gguf = tmp_path / "model.gguf"
        gguf.write_bytes(b"GGUF")
        cmd = runner.LlamaServer(str(gguf), 11445)._command("llama-server")
        assert cmd[cmd.index("-m") + 1] == str(gguf)
        assert "-hf" not in cmd

    def test_embedding_server_gets_embedding_flag(self):
        cmd = runner.LlamaServer("m", 11446, embedding=True)._command("llama-server")
        assert "--embedding" in cmd
        assert "--jinja" not in cmd

    def test_binds_loopback_on_requested_port(self):
        cmd = runner.LlamaServer("m", 11445)._command("llama-server")
        assert cmd[cmd.index("--host") + 1] == Config.LLM_HOST == "127.0.0.1"
        assert cmd[cmd.index("--port") + 1] == "11445"


class TestRegistry:
    def test_same_model_and_port_shares_one_server(self):
        assert runner.get_server("m", 11445) is runner.get_server("m", 11445)

    def test_other_model_on_same_port_stops_the_old_server(self, monkeypatch):
        first = runner.get_server("a", 11445)
        stopped = MagicMock()
        monkeypatch.setattr(first, "stop", stopped)
        second = runner.get_server("b", 11445)
        assert second is not first
        stopped.assert_called_once()


class TestEnsureRunning:
    def test_missing_binary_raises_with_install_hint(self, monkeypatch):
        server = runner.LlamaServer("m", 11445)
        monkeypatch.setattr(server, "is_up", lambda: False)
        monkeypatch.setattr(runner, "find_binary", lambda: None)
        with pytest.raises(runner.RunnerError, match="pacman -S llama.cpp"):
            server.ensure_running()

    def test_already_up_spawns_nothing(self, monkeypatch):
        server = runner.LlamaServer("m", 11445)
        monkeypatch.setattr(server, "is_up", lambda: True)
        spawn = MagicMock()
        monkeypatch.setattr(server, "_spawn", spawn)
        server.ensure_running()
        spawn.assert_not_called()

    def test_process_exit_while_loading_raises(self, monkeypatch):
        server = runner.LlamaServer("m", 11445)
        monkeypatch.setattr(server, "is_up", lambda: False)
        monkeypatch.setattr(Config, "LLM_START_TIMEOUT", 5.0)
        proc = MagicMock()
        proc.poll.return_value = 1
        monkeypatch.setattr(server, "_spawn", lambda: setattr(server, "_proc", proc))
        with pytest.raises(runner.RunnerError, match="exited with code 1"):
            server.ensure_running()

    def test_timeout_raises(self, monkeypatch):
        server = runner.LlamaServer("m", 11445)
        monkeypatch.setattr(server, "is_up", lambda: False)
        monkeypatch.setattr(Config, "LLM_START_TIMEOUT", 0.0)
        proc = MagicMock()
        proc.poll.return_value = None
        monkeypatch.setattr(server, "_spawn", lambda: setattr(server, "_proc", proc))
        with pytest.raises(runner.RunnerError, match="not ready after"):
            server.ensure_running()


class TestStop:
    def test_terminates_then_kills_a_stuck_process(self):
        server = runner.LlamaServer("m", 11445)
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("llama-server", 10), 0]
        server._proc = proc
        server.stop()
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    def test_stop_without_process_is_a_noop(self):
        runner.LlamaServer("m", 11445).stop()


class TestProvider:
    def test_factory_builds_managed_provider(self):
        provider = create_provider(provider="llamacpp", model="m")
        assert isinstance(provider, LlamaCppProvider)
        assert provider.api_url == f"http://{Config.LLM_HOST}:{Config.LLM_PORT}"

    def test_base_url_means_unmanaged(self):
        provider = LlamaCppProvider("m", base_url="http://gpu-box:8080")
        assert provider._server is None
        assert provider.api_url == "http://gpu-box:8080"

    def test_chat_starts_server_first(self, monkeypatch):
        provider = LlamaCppProvider("m")
        calls = []
        monkeypatch.setattr(
            provider._server, "ensure_running", lambda: calls.append("start")
        )
        monkeypatch.setattr(
            "jarvis.llm.providers.api.APIProvider.chat",
            lambda self, messages: calls.append("chat") or "hi",
        )
        assert provider.chat([{"role": "user", "content": "x"}]) == "hi"
        assert calls == ["start", "chat"]

    def test_auto_start_off_skips_spawn(self, monkeypatch):
        monkeypatch.setattr(Config, "LLM_AUTO_START", False)
        provider = LlamaCppProvider("m")
        ensure = MagicMock()
        monkeypatch.setattr(provider._server, "ensure_running", ensure)
        provider._ensure_server()
        ensure.assert_not_called()

    def test_unavailable_without_binary_or_server(self, monkeypatch):
        provider = LlamaCppProvider("m")
        monkeypatch.setattr(provider._server, "is_up", lambda: False)
        monkeypatch.setattr(runner, "find_binary", lambda: None)
        assert provider.is_available() is False

    def test_temperature_reaches_payload(self, monkeypatch):
        provider = LlamaCppProvider("m", base_url="http://x:1", temperature=0.2)
        response = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {},
        }
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        monkeypatch.setattr("httpx.Client", lambda **kw: client)
        provider.chat([{"role": "user", "content": "x"}])
        sent = client.__enter__.return_value.post.call_args.kwargs["json"]
        assert sent["temperature"] == 0.2
