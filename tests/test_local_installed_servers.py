"""Installed servers listed with their manifest keywords (dmcp_registry)."""

import asyncio
import json
import logging

import pytest

from jarvis.dispatch import dmcp_registry


def _listed(monkeypatch, manifest_path):
    async def run_dmcp(logger, *args, **kwargs):
        return json.dumps(
            [{"id": "s", "name": "S", "manifest_path": str(manifest_path)}]
        )

    monkeypatch.setattr(dmcp_registry, "run_dmcp", run_dmcp)
    return asyncio.run(dmcp_registry._local_installed_servers(logging.getLogger("t")))


@pytest.mark.unit
class TestManifestKeywords:
    def test_keywords_come_from_the_manifest(self, monkeypatch, tmp_path):
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"keywords": ["fx"], "description": "d"}))
        (server,) = _listed(monkeypatch, manifest)
        assert server["keywords"] == ["fx"] and server["description"] == "d"

    def test_a_bad_manifest_is_named_and_the_server_still_listed(
        self, monkeypatch, tmp_path, caplog
    ):
        manifest = tmp_path / "manifest.json"
        manifest.write_text("{not json")
        with caplog.at_level("WARNING", logger="t"):
            (server,) = _listed(monkeypatch, manifest)
        assert server["id"] == "s" and server["keywords"] == []
        assert str(manifest) in caplog.text

    def test_a_manifest_that_is_not_an_object_gives_no_keywords(
        self, monkeypatch, tmp_path
    ):
        manifest = tmp_path / "manifest.json"
        manifest.write_text("[1, 2]")
        (server,) = _listed(monkeypatch, manifest)
        assert server["keywords"] == [] and server["description"] == ""
