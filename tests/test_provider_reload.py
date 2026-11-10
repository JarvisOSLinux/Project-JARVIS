"""A provider added or changed while JARVIS runs is used without a restart (#315).

The TUI's provider form, `jarvis providers` and the app all write
providers.json while JARVIS runs; the pool used to read it once at start-up.
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvis.config import Config
from jarvis.core import component_factory
from jarvis.core.component_factory import ComponentFactory
from jarvis.llm.base import BaseLLMProvider
from jarvis.llm.chat import describe_provider_error


class _Stub(BaseLLMProvider):
    """Answers with its model name, or raises what its model name says."""

    def chat(self, messages):
        if self.model.startswith("fail-402"):
            raise RuntimeError("Add usage credits or subscribe (status code: 402)")
        if self.model.startswith("fail-404"):
            raise RuntimeError(
                "Client error '404 Not Found' for url 'https://x/v1/chat/completions'"
            )
        return f"answer from {self.model}"

    def is_available(self):
        return True


@pytest.fixture
def providers(tmp_path, monkeypatch):
    path = tmp_path / "providers.json"
    monkeypatch.setattr(Config, "PROVIDERS_FILE", str(path))
    monkeypatch.setattr(
        component_factory,
        "create_llm_provider",
        lambda provider, model, **kw: _Stub(model),
    )

    def write(*entries):
        path.write_text(json.dumps({"providers": list(entries)}))
        # Two writes in one test can land within the filesystem's clock tick.
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000 * write.n))
        write.n += 1

    write.n = 1
    return write


def _entry(name, model):
    return {"name": name, "type": "api", "model": model, "url": "https://x"}


def _ask(pool):
    return pool.chat([{"role": "user", "content": "hi"}])


def test_a_provider_added_while_running_is_used(providers):
    providers(_entry("a", "fail-402"))
    pool = ComponentFactory._build_provider_pool()
    with pytest.raises(RuntimeError):
        _ask(pool)
    providers(_entry("a", "fail-402"), _entry("nvidia", "nemotron"))
    assert _ask(pool) == "answer from nemotron"


def test_an_unchanged_provider_keeps_its_rest_across_an_edit(providers):
    providers(_entry("general", "fail-402"), _entry("b", "model-b"))
    pool = ComponentFactory._build_provider_pool()
    assert _ask(pool) == "answer from model-b"
    general = pool.entries[0]
    assert general.status == "exhausted"
    providers(_entry("general", "fail-402"), _entry("b", "model-b2"))
    assert _ask(pool) == "answer from model-b2"
    assert pool.entries[0] is general and general.status == "exhausted"


def test_an_edited_provider_starts_fresh(providers):
    providers(_entry("general", "fail-402"))
    pool = ComponentFactory._build_provider_pool()
    with pytest.raises(RuntimeError):
        _ask(pool)
    providers(_entry("general", "fixed-model"))
    assert _ask(pool) == "answer from fixed-model"


def test_a_file_caught_mid_write_keeps_the_list_and_is_read_again(providers, tmp_path):
    providers(_entry("a", "model-a"))
    pool = ComponentFactory._build_provider_pool()
    path = tmp_path / "providers.json"
    path.write_text('{"providers": [')
    assert _ask(pool) == "answer from model-a"
    providers(_entry("a", "model-a"), _entry("b", "model-b"))
    _ask(pool)
    assert [e.name for e in pool.entries] == ["a", "b"]


def test_removing_every_provider_says_so(providers, tmp_path):
    providers(_entry("a", "model-a"))
    pool = ComponentFactory._build_provider_pool()
    (tmp_path / "providers.json").unlink()
    with pytest.raises(RuntimeError, match="No LLM provider is configured"):
        _ask(pool)
    providers(_entry("a", "model-a"))
    assert _ask(pool) == "answer from model-a"


def test_unchanged_file_is_not_read_again(providers, monkeypatch):
    providers(_entry("a", "model-a"))
    pool = ComponentFactory._build_provider_pool()
    reads = []
    original = component_factory._ProvidersFile.read
    monkeypatch.setattr(
        component_factory._ProvidersFile,
        "read",
        lambda self: reads.append(1) or original(self),
    )
    for _ in range(3):
        _ask(pool)
    assert reads == []


@pytest.mark.asyncio
async def test_a_jarvis_started_without_a_provider_builds_one_later(providers):
    from jarvis.runtime import root_handlers

    app = SimpleNamespace(llm=None)
    assert root_handlers._ensure_llm(app) is None
    providers(_entry("nvidia", "nemotron"))
    llm = root_handlers._ensure_llm(app)
    assert llm is not None and app.llm is llm
    assert root_handlers._ensure_llm(app) is llm
    assert "restart" not in root_handlers._NO_LLM_MSG


def test_every_provider_is_named_with_its_own_reason_and_retry(providers):
    providers(_entry("nvidia", "fail-404"), _entry("general", "fail-402"))
    pool = ComponentFactory._build_provider_pool()
    with pytest.raises(RuntimeError) as failure:
        _ask(pool)
    told = describe_provider_error(failure.value)
    nvidia, general = told.split(" | ")
    assert nvidia.startswith("nvidia: the model is not available there (404)")
    assert nvidia.endswith("retrying it in 30 s")
    assert general.startswith(
        "general: the account's usage or credits are used up (402)"
    )
    assert general.endswith("retrying it in 60 min")


def test_a_pool_error_with_continuation_lines_keeps_them_with_their_provider():
    error = RuntimeError(
        "All LLM providers are unavailable:\n"
        "  nvidia: cooldown (retry in 16s) — Client error '404 Not Found' for url 'u'\n"
        "For more information check: https://developer.mozilla.org/404\n"
        "  general: exhausted (retry in 3586s) — Add usage credits (ref: x) (status code: 402)"
    )
    told = describe_provider_error(error)
    assert "nvidia: the model is not available there (404)" in told
    assert "retrying it in 16 s" in told and "retrying it in 60 min" in told
    assert "(s;" not in told and "mozilla" not in told
