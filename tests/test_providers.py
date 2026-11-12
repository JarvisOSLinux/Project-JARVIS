"""Tests for provider CRUD (jarvis.core.providers) and provider creation
(jarvis.llm.providers), covering the key-less API provider fix (#177) and
the lmstudio provider type (#179).
"""

import json

import pytest

from jarvis.core import providers as providers_module
from jarvis.llm.providers import create_provider
from jarvis.llm.providers.api import APIProvider


@pytest.fixture
def isolated_providers_file(tmp_path, monkeypatch):
    providers_file = tmp_path / "providers.json"
    monkeypatch.setattr(providers_module.Config, "PROVIDERS_FILE", str(providers_file))
    return str(providers_file)


class TestAPIProviderKeyless:
    def test_api_url_still_required(self):
        with pytest.raises(ValueError, match="api_url is required"):
            APIProvider(model="gpt-4", api_url="", api_key="")

    def test_api_key_is_optional(self):
        provider = APIProvider(model="gpt-4", api_url="http://localhost:1234/v1")
        assert "Authorization" not in provider.headers

    def test_api_key_sets_authorization_header(self):
        provider = APIProvider(
            model="gpt-4", api_url="http://localhost:8080", api_key="sk-test"
        )
        assert provider.headers["Authorization"] == "Bearer sk-test"


class TestCreateProviderLmstudio:
    def test_lmstudio_dispatches_to_api_provider(self):
        provider = create_provider(
            provider="lmstudio",
            model="local-model",
            api_url="http://localhost:1234/v1",
        )
        assert isinstance(provider, APIProvider)

    def test_unknown_provider_lists_lmstudio(self):
        with pytest.raises(ValueError, match="lmstudio"):
            create_provider(provider="bogus", model="x")


class TestAddProviderLmstudio:
    def test_lmstudio_defaults_url(self, isolated_providers_file):
        name, _ = providers_module.add_provider(ptype="lmstudio", model="local-model")
        with open(isolated_providers_file) as f:
            data = json.load(f)
        entry = next(p for p in data["providers"] if p["name"] == name)
        assert entry["url"] == "http://localhost:1234/v1"
        assert "api_key" not in entry

    def test_lmstudio_explicit_url_respected(self, isolated_providers_file):
        name, _ = providers_module.add_provider(
            ptype="lmstudio", model="local-model", url="http://192.168.1.5:1234/v1"
        )
        with open(isolated_providers_file) as f:
            data = json.load(f)
        entry = next(p for p in data["providers"] if p["name"] == name)
        assert entry["url"] == "http://192.168.1.5:1234/v1"

    def test_api_type_no_longer_requires_key(self, isolated_providers_file):
        name, _ = providers_module.add_provider(
            ptype="api", model="local-model", url="http://localhost:8080/v1"
        )
        with open(isolated_providers_file) as f:
            data = json.load(f)
        entry = next(p for p in data["providers"] if p["name"] == name)
        assert "api_key" not in entry

    def test_api_type_still_requires_url(self, isolated_providers_file):
        with pytest.raises(ValueError, match="require a url"):
            providers_module.add_provider(ptype="api", model="local-model")

    def test_unknown_type_rejected(self, isolated_providers_file):
        with pytest.raises(ValueError, match="lmstudio"):
            providers_module.add_provider(ptype="bogus", model="local-model")


class TestAKeyBelongsToItsAddress:
    """A provider moved to another host loses its stored key unless one is
    given: an edit from a phone, which never sees keys, must not be able to
    send one to a server of its choosing."""

    def _add(self):
        providers_module.add_provider(
            "api", "m", name="cloud", url="https://ollama.com/v1", api_key="secret"
        )

    def _entry(self):
        return providers_module.list_providers()[0]

    def test_another_host_drops_the_key(self, isolated_providers_file):
        self._add()
        updated = providers_module.edit_provider("cloud", url="https://evil.example/v1")
        assert "api_key" not in self._entry()
        assert any("key removed" in u for u in updated)

    def test_the_same_host_keeps_it(self, isolated_providers_file):
        self._add()
        providers_module.edit_provider("cloud", url="https://OLLAMA.com/")
        assert self._entry()["api_key"] == "secret"

    def test_another_port_or_scheme_is_another_address(self, isolated_providers_file):
        self._add()
        providers_module.edit_provider("cloud", url="http://ollama.com/v1")
        assert "api_key" not in self._entry()

    def test_a_new_key_with_the_new_host_is_kept(self, isolated_providers_file):
        self._add()
        providers_module.edit_provider(
            "cloud", url="https://integrate.api.nvidia.com", key="nvapi-new"
        )
        assert self._entry()["api_key"] == "nvapi-new"

    def test_headers_go_with_the_key(self, isolated_providers_file):
        self._add()
        data = providers_module.load_providers()
        data["providers"][0]["headers"] = {"x-api-key": "h"}
        providers_module.save_providers(data)
        providers_module.edit_provider("cloud", url="https://evil.example")
        assert "headers" not in self._entry()

    def test_other_fields_leave_the_key(self, isolated_providers_file):
        self._add()
        providers_module.edit_provider("cloud", model="other", temperature=0.2)
        assert self._entry()["api_key"] == "secret"

    def test_an_unknown_type_is_refused(self, isolated_providers_file):
        self._add()
        with pytest.raises(ValueError, match="Unknown provider type"):
            providers_module.edit_provider("cloud", type="")
        with pytest.raises(ValueError, match="Unknown provider type"):
            providers_module.edit_provider("cloud", type="ollama")
        providers_module.edit_provider("cloud", type="LMStudio")
        assert self._entry()["type"] == "lmstudio"

    def test_headers_go_even_with_a_new_key(self, isolated_providers_file):
        self._add()
        data = providers_module.load_providers()
        data["providers"][0]["headers"] = {"x-api-key": "h"}
        providers_module.save_providers(data)
        providers_module.edit_provider("cloud", url="https://evil.example", key="x")
        entry = self._entry()
        assert entry["api_key"] == "x"
        assert "headers" not in entry

    def test_the_same_address_written_differently_keeps_it(
        self, isolated_providers_file
    ):
        self._add()
        providers_module.edit_provider("cloud", url="https://ollama.com:443/v1")
        assert self._entry()["api_key"] == "secret"

    def test_a_cloud_entry_with_no_url_keeps_its_key_at_its_own_address(
        self, isolated_providers_file
    ):
        data = {
            "version": 2,
            "providers": [
                {"name": "c", "type": "ollama-cloud", "model": "m", "api_key": "k"}
            ],
        }
        providers_module.save_providers(data)
        updated = providers_module.edit_provider("c", url="https://ollama.com")
        assert providers_module.list_providers()[0]["api_key"] == "k"
        assert providers_module.KEY_REMOVED not in updated

    @pytest.mark.parametrize("url", ["http://[x", "localhost:11434", "ftp://h", ""])
    def test_a_bad_address_is_refused(self, isolated_providers_file, url):
        if url:
            with pytest.raises(ValueError, match="not an address"):
                providers_module.add_provider("api", "m", url=url)
        self._add()
        if url:
            with pytest.raises(ValueError, match="not an address"):
                providers_module.edit_provider("cloud", url=url)

    def test_origin_never_raises(self):
        assert providers_module.origin("http://[x") == ("", "", None)
        assert providers_module.origin(None) == ("", "", None)
        assert providers_module.origin(5) == ("", "", None)
        assert providers_module.origin("HTTPS://Ollama.com:443/v1") == (
            "https",
            "ollama.com",
            None,
        )


class TestThePoolSurvivesABadEntry:
    def test_a_malformed_address_already_saved_does_not_stop_jarvis(
        self, isolated_providers_file, monkeypatch
    ):
        from jarvis.core.component_factory import ComponentFactory

        data = {
            "version": 2,
            "providers": [
                {
                    "name": "bad",
                    "type": "ollama-cloud",
                    "model": "m",
                    "url": "http://[x",
                },
                {"name": "ok", "type": "api", "model": "m", "url": "http://h/v1"},
            ],
        }
        monkeypatch.setenv("OLLAMA_API_KEY", "envkey")
        entries = ComponentFactory._pool_entries(data)
        assert "ok" in [e.name for e in entries]
        for e in entries:
            assert e.provider.headers.get("Authorization") != "Bearer envkey"

    @pytest.mark.parametrize(
        "url, gets_key",
        [
            ("https://ollama.com", True),
            ("https://ollama.com:443", True),
            ("http://ollama.com", False),
            ("https://ollama.com:8443", False),
            ("https://evil.example", False),
        ],
    )
    def test_ollamas_environment_key_needs_its_exact_address(
        self, isolated_providers_file, monkeypatch, url, gets_key
    ):
        from jarvis.core.component_factory import ComponentFactory

        data = {
            "version": 2,
            "providers": [
                {"name": "c", "type": "ollama-cloud", "model": "m", "url": url}
            ],
        }
        monkeypatch.setenv("OLLAMA_API_KEY", "envkey")
        provider = ComponentFactory._pool_entries(data)[0].provider
        assert (provider.headers.get("Authorization") == "Bearer envkey") is gets_key
