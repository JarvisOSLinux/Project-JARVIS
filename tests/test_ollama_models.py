"""Model picker backend: list and pull Ollama models (local or Ollama Cloud)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.llm import ollama_models
from jarvis.runtime import io as runtime_io


@pytest.mark.unit
class TestIsCloud:
    def test_cloud_host_only(self):
        assert ollama_models.is_cloud("https://ollama.com")
        assert ollama_models.is_cloud("https://ollama.com/")
        assert not ollama_models.is_cloud("http://localhost:11434")
        assert not ollama_models.is_cloud("https://ollama.com.evil.example")
        assert not ollama_models.is_cloud(None)


@pytest.mark.unit
class TestListModels:
    def test_reads_objects_and_dicts_sorted_and_skips_nameless(self):
        listing = SimpleNamespace(
            models=[
                SimpleNamespace(model="qwen3:8b", size=5, modified_at="t1"),
                {"name": "llama3:8b", "size": 7},
                {"size": 1},
            ]
        )
        client = Mock()
        client.list.return_value = listing
        with patch.object(
            ollama_models, "_get_shared_client", return_value=client
        ) as g:
            out = ollama_models.list_models("https://ollama.com", "key")
        g.assert_called_once_with("https://ollama.com", "key")
        assert [m["name"] for m in out] == ["llama3:8b", "qwen3:8b"]
        assert out[1] == {"name": "qwen3:8b", "size": 5, "modified": "t1"}

    def test_default_url_and_no_key(self):
        client = Mock()
        client.list.return_value = {"models": []}
        with patch.object(
            ollama_models, "_get_shared_client", return_value=client
        ) as g:
            assert ollama_models.list_models(None, "") == []
        g.assert_called_once_with("http://localhost:11434", None)


@pytest.mark.unit
class TestPullModel:
    def test_streams_progress(self):
        client = Mock()
        client.pull.return_value = iter(
            [{"status": "pulling x", "completed": 1, "total": 4}, {"status": "success"}]
        )
        seen = []
        with patch.object(ollama_models, "_get_shared_client", return_value=client):
            ollama_models.pull_model("m:1", None, None, seen.append)
        client.pull.assert_called_once_with("m:1", stream=True)
        assert seen == [
            {"status": "pulling x", "completed": 1, "total": 4},
            {"status": "success", "completed": 0, "total": 0},
        ]

    def test_cloud_and_empty_refused(self):
        with pytest.raises(ValueError, match="not downloaded"):
            ollama_models.pull_model("m", "https://ollama.com", "k", Mock())
        with pytest.raises(ValueError, match="requires"):
            ollama_models.pull_model("  ", None, None, Mock())


def _app():
    return SimpleNamespace(_gui_clients={Mock()})


@pytest.mark.unit
class TestSocketHandlers:
    @pytest.mark.asyncio
    async def test_list_models_replies_to_sender(self):
        writer = Mock()
        with (
            patch(
                "jarvis.llm.ollama_models.list_models",
                return_value=[{"name": "a", "size": 1, "modified": ""}],
            ) as lm,
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
        ):
            await runtime_io._process_gui_message(
                _app(),
                Mock(),
                {"type": "list_models", "url": "https://ollama.com", "api_key": "k"},
                writer,
            )
        lm.assert_called_once_with("https://ollama.com", "k")
        (to, reply), _ = gw.await_args
        assert to is writer
        assert reply["type"] == "model_list" and reply["models"][0]["name"] == "a"
        assert "error" not in reply

    @pytest.mark.asyncio
    async def test_list_models_error_hides_key(self):
        with (
            patch(
                "jarvis.llm.ollama_models.list_models",
                side_effect=RuntimeError("401 for Bearer oll-0123456789abcdef"),
            ),
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
        ):
            await runtime_io._process_gui_message(
                _app(),
                Mock(),
                {"type": "list_models", "api_key": "oll-0123456789abcdef"},
                Mock(),
            )
        (_, reply), _ = gw.await_args
        assert reply["models"] == [] and "401" in reply["error"]
        assert "oll-0123456789abcdef" not in reply["error"]

    @pytest.mark.asyncio
    async def test_pull_streams_then_finishes(self):
        def fake_pull(model, url, key, progress):
            progress({"status": "pulling", "completed": 2, "total": 4})

        writer = Mock()
        with (
            patch("jarvis.llm.ollama_models.pull_model", side_effect=fake_pull),
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
        ):
            await runtime_io._process_gui_message(
                _app(), Mock(), {"type": "pull_model", "model": "m:1"}, writer
            )
            import asyncio

            await asyncio.sleep(0.05)
        sent = [c.args[1] for c in gw.await_args_list]
        assert {
            "type": "model_pull",
            "model": "m:1",
            "status": "pulling",
            "completed": 2,
            "total": 4,
            "done": False,
        } in sent
        assert sent[-1] == {"type": "model_pull", "model": "m:1", "done": True}

    @pytest.mark.asyncio
    async def test_pull_failure_reports_error(self):
        with (
            patch(
                "jarvis.llm.ollama_models.pull_model",
                side_effect=ValueError("pull_model requires 'model'"),
            ),
            patch.object(runtime_io, "_gui_write", new=AsyncMock()) as gw,
        ):
            await runtime_io._process_gui_message(
                _app(), Mock(), {"type": "pull_model"}, Mock()
            )
        (_, last), _ = gw.await_args
        assert last["done"] is True and "requires" in last["error"]
