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
