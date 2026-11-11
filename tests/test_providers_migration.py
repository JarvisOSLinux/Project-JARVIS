"""providers.json version 1 -> 2: Ollama entries keep working (#291 review).

Version 2 has no "ollama" type. Someone on Ollama must not start JARVIS with
no model: a cloud model goes to ollama.com directly with its key unchanged,
a local one keeps using their Ollama through its OpenAI-compatible API, and
nothing is dropped.
"""

import json
import os
import stat

import pytest

from jarvis.config import Config
from jarvis.core import providers as providers_mod

KEY = "sk-ollama-0123456789abcdef"
CLOUD_NAMES = ["nemotron-3-ultra", "gpt-oss:120b", "gemma4:31b", "nemotron-3-nano:30b"]


@pytest.fixture
def file(tmp_path, monkeypatch):
    path = tmp_path / "providers.json"
    monkeypatch.setattr(Config, "PROVIDERS_FILE", str(path))
    monkeypatch.setattr(providers_mod, "_ollama_cloud_names", lambda: CLOUD_NAMES)
    return path


def _write(path, providers, **extra):
    path.write_text(json.dumps({"providers": providers, **extra}, indent=2))


def test_yakups_cloud_entry_keeps_its_key_byte_for_byte(file):
    _write(
        file,
        [
            {
                "name": "main",
                "type": "ollama",
                "model": "nemotron-3-ultra:cloud",
                "api_key": KEY,
            }
        ],
    )
    data = providers_mod.load_providers()
    entry = data["providers"][0]
    assert entry == {
        "name": "main",
        "type": "ollama-cloud",
        "model": "nemotron-3-ultra",
        "api_key": KEY,
    }
    assert data["version"] == providers_mod.PROVIDERS_VERSION
    assert json.loads(file.read_text())["providers"][0]["api_key"] == KEY


@pytest.mark.parametrize(
    "app_name, api_name",
    [
        ("nemotron-3-ultra:cloud", "nemotron-3-ultra"),
        ("gpt-oss:120b-cloud", "gpt-oss:120b"),
        ("gemma4:cloud", "gemma4:31b"),
        ("someday-model:cloud", "someday-model"),
    ],
)
def test_cloud_names_become_what_the_api_serves(file, app_name, api_name):
    _write(file, [{"name": "c", "type": "ollama", "model": app_name}])
    assert providers_mod.load_providers()["providers"][0]["model"] == api_name


def test_cloud_list_unreachable_strips_the_tag(file, monkeypatch):
    monkeypatch.setattr(providers_mod, "_ollama_cloud_names", lambda: [])
    _write(file, [{"name": "c", "type": "ollama", "model": "gemma4:cloud"}])
    assert providers_mod.load_providers()["providers"][0]["model"] == "gemma4"


def test_local_model_keeps_using_ollama(file):
    _write(
        file,
        [
            {"name": "local", "type": "ollama", "model": "llama3.2"},
            {
                "name": "remote",
                "type": "ollama",
                "model": "qwen3",
                "url": "http://gpu-box:11434/",
            },
        ],
    )
    local, remote = providers_mod.load_providers()["providers"]
    assert local == {
        "name": "local",
        "type": "api",
        "model": "llama3.2",
        "url": "http://localhost:11434",
    }
    assert remote["type"] == "api" and remote["url"] == "http://gpu-box:11434"


def test_an_entry_with_no_type_was_an_ollama_one(file):
    _write(file, [{"name": "old", "model": "mistral"}])
    entry = providers_mod.load_providers()["providers"][0]
    assert entry["type"] == "api" and entry["url"] == "http://localhost:11434"


def test_nothing_is_dropped(file):
    _write(
        file,
        [
            {"name": "broken", "type": "ollama"},
            {
                "name": "keep",
                "type": "api",
                "model": "gpt-4o",
                "url": "https://api.openai.com",
                "api_key": KEY,
            },
        ],
    )
    broken, keep = providers_mod.load_providers()["providers"]
    assert broken["type"] == "ollama" and "no model" in broken["inactive"]
    assert keep == {
        "name": "keep",
        "type": "api",
        "model": "gpt-4o",
        "url": "https://api.openai.com",
        "api_key": KEY,
    }


def test_backup_is_the_original_and_owner_only(file):
    original = json.dumps(
        {
            "providers": [
                {"name": "c", "type": "ollama", "model": "x:cloud", "api_key": KEY}
            ]
        }
    )
    file.write_text(original)
    providers_mod.load_providers()
    backup = file.with_name("providers.json.v1.bak")
    assert backup.read_text() == original
    if os.name == "posix":
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
        assert stat.S_IMODE(file.stat().st_mode) == 0o600


def test_second_run_changes_nothing(file, monkeypatch):
    _write(
        file,
        [
            {
                "name": "c",
                "type": "ollama",
                "model": "nemotron-3-ultra:cloud",
                "api_key": KEY,
            }
        ],
    )
    providers_mod.load_providers()
    after_first = file.read_text()
    backup = file.with_name("providers.json.v1.bak").read_text()
    monkeypatch.setattr(
        providers_mod,
        "_ollama_cloud_names",
        lambda: pytest.fail("asked ollama.com again"),
    )
    providers_mod.load_providers()
    assert file.read_text() == after_first
    assert file.with_name("providers.json.v1.bak").read_text() == backup


def test_the_key_is_never_logged(file, caplog):
    _write(
        file,
        [
            {
                "name": "c",
                "type": "ollama",
                "model": "nemotron-3-ultra:cloud",
                "api_key": KEY,
            }
        ],
    )
    with caplog.at_level("DEBUG"):
        providers_mod.load_providers()
    assert "ollama.com directly" in caplog.text
    assert KEY not in caplog.text


def test_a_cli_edit_before_the_daemon_ever_ran_still_migrates(file):
    _write(file, [{"name": "c", "type": "ollama", "model": "llama3.2"}])
    providers_mod.add_provider("api", "gpt-4o", url="https://api.openai.com")
    types = [p["type"] for p in json.loads(file.read_text())["providers"]]
    assert types == ["api", "api"]


def test_new_file_is_written_owner_only(file):
    providers_mod.add_provider("llamacpp", "Qwen/Qwen3-4B-GGUF:Q4_K_M")
    data = json.loads(file.read_text())
    assert data["version"] == providers_mod.PROVIDERS_VERSION
    if os.name == "posix":
        assert stat.S_IMODE(file.stat().st_mode) == 0o600


def test_pool_starts_with_the_migrated_entry_and_skips_inactive(file):
    from jarvis.core.component_factory import ComponentFactory

    _write(
        file,
        [
            {"name": "broken", "type": "ollama"},
            {
                "name": "main",
                "type": "ollama",
                "model": "nemotron-3-ultra:cloud",
                "api_key": KEY,
            },
        ],
    )
    pool = ComponentFactory._build_provider_pool()
    assert [e.name for e in pool.entries] == ["main"]
    provider = pool.entries[0].provider
    assert provider.api_url == "https://ollama.com"
    assert provider.model == "nemotron-3-ultra"
    assert provider.headers["Authorization"] == f"Bearer {KEY}"


def test_a_file_with_nothing_to_convert_is_left_alone(file):
    _write(
        file, [{"name": "q", "type": "llamacpp", "model": "Qwen/Qwen3-4B-GGUF:Q4_K_M"}]
    )
    before = file.read_text()
    data = providers_mod.load_providers()
    assert data["version"] == providers_mod.PROVIDERS_VERSION
    assert file.read_text() == before
    assert not file.with_name("providers.json.v1.bak").exists()


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs a read-only dir"
)
def test_a_read_only_system_file_still_starts(file, caplog):
    _write(
        file,
        [
            {
                "name": "c",
                "type": "ollama",
                "model": "nemotron-3-ultra:cloud",
                "api_key": KEY,
            }
        ],
    )
    before = file.read_text()
    file.parent.chmod(0o555)
    try:
        with caplog.at_level("WARNING"):
            data = providers_mod.load_providers()
    finally:
        file.parent.chmod(0o755)
    assert data["providers"][0]["type"] == "ollama-cloud"
    assert file.read_text() == before
    assert "could not be rewritten" in caplog.text and KEY not in caplog.text


def test_the_suite_never_reaches_the_real_config_dir():
    from jarvis.platform import current

    real = str(current.config_dir())
    assert not Config.PROVIDERS_FILE.startswith(real)
