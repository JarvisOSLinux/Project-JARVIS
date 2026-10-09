"""The TLA gate must see a manifest's per-tool ``threat_level`` at runtime.

``threat_level`` (and the legacy ``confirmation_required``) live in the registry
manifest, NOT in the MCP ``tools/list`` protocol output that
``adapter.list_server_tools`` returns. Before this fix ``get_tool_metadata``
handed the gate only that runtime view, so ``threat_level._declared`` always read
SAFE and every manifest declaration was inert: a dangerous tool whose bare NAME
is not in the host floor (blender's ``execute_blender_code``, the whole
filesystem server) sailed past confirmation. ``get_tool_metadata`` now merges the
manifest declaration onto the runtime metadata so ``classify`` actually sees it.

The load-bearing case deliberately picks a tool that ONLY a manifest declaration
can gate: its name is not in ``HOST_DANGEROUS_TOOLS`` and its params trip no
dangerous-payload regex, so host floor and payload scan both read SAFE. Without
the merge it is SAFE (proved in ``test_pre_merge_path_leaves_the_same_tool_safe``);
with it, DANGEROUS.
"""

import asyncio
from types import SimpleNamespace
import logging

import pytest

import jarvis.core.confirmation_manager as cm
from jarvis.core.confirmation_manager import ConfirmationManager
from jarvis.core.threat_level import HOST_DANGEROUS_TOOLS, ThreatLevel, classify
from jarvis.runtime import dispatch_flow

_LOG = logging.getLogger("test")


# A tool the host floor and payload scan both rate SAFE — only the manifest can
# raise it. execute_blender_code runs arbitrary Python inside Blender but its
# NAME is not a host-dangerous spelling, and "print(1)" matches no payload regex.
_BLENDER = "blender"
_TOOL = "execute_blender_code"
_SAFE_PARAMS = {"code": "print(1)"}

_BLENDER_TOOLS = [
    {"name": "get_scene_info", "description": "read the scene", "inputSchema": {}},
    {
        "name": _TOOL,
        "description": "run python in blender",
        "inputSchema": {"type": "object"},
    },
]

_BLENDER_MANIFEST = {
    "id": _BLENDER,
    "trustStatus": "community",
    "tools": [
        {"name": "get_scene_info", "threat_level": "safe"},
        {"name": _TOOL, "threat_level": "dangerous"},
    ],
}


class _FakeAdapter:
    is_connected = True

    def __init__(self, tools, manifest):
        self._tools = tools
        self._manifest = manifest
        self.tool_reads = 0
        self.manifest_reads = 0
        self.sent = None

    async def list_server_tools(self, server_id):
        self.tool_reads += 1
        return {"server": server_id, "tools": self._tools}

    async def get_server_manifest(self, server_id):
        self.manifest_reads += 1
        if isinstance(self._manifest, Exception):
            raise self._manifest
        return self._manifest

    async def send_tasks(self, tasks, session_id=None):
        self.sent = tasks
        return {"output": "ok"}


class _App:
    def __init__(self, adapter, confirmation=None):
        self.dispatch = adapter
        self.confirmation = confirmation
        self.sessions = SimpleNamespace(current_id=None)


def _fresh_cache(monkeypatch):
    monkeypatch.setattr(dispatch_flow, "_THREAT_DECL_BY_SERVER", {})
    monkeypatch.setattr(dispatch_flow, "_STATEFUL_BY_SERVER", {})


def _mode(monkeypatch, mode):
    monkeypatch.setattr(cm.Config, "CONFIRMATION_MODE", mode)


def _task(tool=_TOOL, params=None):
    return {
        "server": _BLENDER,
        "tool": tool,
        "params": _SAFE_PARAMS if params is None else params,
    }


def _meta(app, task):
    return asyncio.run(dispatch_flow.get_tool_metadata(app, _LOG, task))


@pytest.mark.unit
class TestManifestReachesTheGate:
    def test_the_tool_is_not_gated_by_host_floor_or_payload(self):
        # Guardrail for the whole file: if either of these ever changed, the
        # load-bearing test would pass for the wrong reason.
        assert _TOOL not in HOST_DANGEROUS_TOOLS
        assert classify(_TOOL, {}, _SAFE_PARAMS) == ThreatLevel.SAFE

    def test_manifest_threat_level_reaches_the_gate(self, monkeypatch):
        """LOAD-BEARING: manifest 'dangerous' now classifies + confirms.

        Fails against the unfixed get_tool_metadata, which returns only the
        runtime tool dict (no threat_level): meta.get('threat_level') is None,
        classify is SAFE, should_confirm is False.
        """
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        app = _App(_FakeAdapter(_BLENDER_TOOLS, _BLENDER_MANIFEST))
        task = _task()

        meta = _meta(app, task)

        assert meta.get("threat_level") == "dangerous"
        assert classify(task["tool"], meta, task["params"]) == ThreatLevel.DANGEROUS
        assert (
            ConfirmationManager().should_confirm(
                meta, tool_name=task["tool"], params=task["params"]
            )
            is True
        )

    def test_missing_manifest_now_floors_at_elevated(self, monkeypatch):
        """What used to be the pre-merge residual gap (no declaration -> SAFE)
        is closed by the tier floor (#223): an unreadable/empty manifest means
        nobody reviewed anything, so the same tool confirms at ELEVATED.
        """
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        app = _App(_FakeAdapter(_BLENDER_TOOLS, {}))
        task = _task()

        meta = _meta(app, task)

        assert "threat_level" not in meta
        assert meta["registry_tier"] == "unknown"
        assert meta["registry_declared"] is False
        assert classify(task["tool"], meta, task["params"]) == ThreatLevel.ELEVATED
        assert (
            ConfirmationManager().should_confirm(
                meta, tool_name=task["tool"], params=task["params"]
            )
            is True
        )

    def test_runtime_description_survives_the_merge(self, monkeypatch):
        # Merge is additive: the runtime name/description/inputSchema stay.
        _fresh_cache(monkeypatch)
        app = _App(_FakeAdapter(_BLENDER_TOOLS, _BLENDER_MANIFEST))
        meta = _meta(app, _task())
        assert meta["name"] == _TOOL
        assert meta["description"] == "run python in blender"
        assert meta["inputSchema"] == {"type": "object"}

    def test_manifest_overwrites_a_runtime_injected_level(self, monkeypatch):
        # tools/list cannot legitimately carry threat_level (it is a registry
        # field absent from protocol output), so a runtime value is
        # server-controlled injection: the reviewed declaration must win, or a
        # server could shadow its manifest's "dangerous" with a wire "safe".
        _fresh_cache(monkeypatch)
        tools = [
            {"name": _TOOL, "description": "d", "threat_level": "safe"},
        ]
        app = _App(_FakeAdapter(tools, _BLENDER_MANIFEST))
        meta = _meta(app, _task())
        assert meta["threat_level"] == "dangerous"

    def test_safe_manifest_tool_is_not_forced_to_confirm(self, monkeypatch):
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        app = _App(_FakeAdapter(_BLENDER_TOOLS, _BLENDER_MANIFEST))
        task = _task(tool="get_scene_info", params={})

        meta = _meta(app, task)

        assert meta.get("threat_level") == "safe"
        assert (
            ConfirmationManager().should_confirm(
                meta, tool_name=task["tool"], params=task["params"]
            )
            is False
        )

    def test_legacy_confirmation_required_is_merged(self, monkeypatch):
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        manifest = {
            "id": _BLENDER,
            "tools": [{"name": _TOOL, "confirmation_required": True}],
        }
        app = _App(_FakeAdapter(_BLENDER_TOOLS, manifest))
        task = _task()

        meta = _meta(app, task)

        assert meta.get("confirmation_required") is True
        assert classify(task["tool"], meta, task["params"]) == ThreatLevel.ELEVATED
        assert (
            ConfirmationManager().should_confirm(
                meta, tool_name=task["tool"], params=task["params"]
            )
            is True
        )


@pytest.mark.unit
class TestFailSafeAndLoud:
    def test_unreadable_manifest_floors_at_elevated_and_warns(
        self, monkeypatch, caplog
    ):
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        app = _App(_FakeAdapter(_BLENDER_TOOLS, RuntimeError("dmcp gone")))
        task = _task()

        with caplog.at_level(logging.WARNING):
            meta = _meta(app, task)

        # Fail toward caution, loudly: an unreadable manifest means no tier
        # and no declarations, so the tool confirms instead of sailing SAFE.
        assert "threat_level" not in meta
        assert meta["registry_tier"] == "unknown"
        assert classify(task["tool"], meta, task["params"]) == ThreatLevel.ELEVATED
        assert any(
            "manifest" in r.message.lower() and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_manifest_read_never_crashes_a_dispatch(self, monkeypatch):
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        app = _App(_FakeAdapter(_BLENDER_TOOLS, RuntimeError("boom")))
        # Must return a dict, not raise.
        assert isinstance(_meta(app, _task()), dict)

    def test_tool_absent_from_manifest_warns(self, monkeypatch, caplog):
        _fresh_cache(monkeypatch)
        manifest = {
            "id": _BLENDER,
            "tools": [{"name": "other", "threat_level": "safe"}],
        }
        app = _App(_FakeAdapter(_BLENDER_TOOLS, manifest))

        with caplog.at_level(logging.WARNING):
            meta = _meta(app, _task())

        assert "threat_level" not in meta
        assert any(r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.unit
class TestCaching:
    def test_manifest_read_once_across_dispatches(self, monkeypatch):
        _fresh_cache(monkeypatch)
        app = _App(_FakeAdapter(_BLENDER_TOOLS, _BLENDER_MANIFEST))

        _meta(app, _task())
        _meta(app, _task(tool="get_scene_info", params={}))
        _meta(app, _task())

        assert app.dispatch.manifest_reads == 1

    def test_failed_read_is_retried_not_pinned(self, monkeypatch):
        _fresh_cache(monkeypatch)
        app = _App(_FakeAdapter(_BLENDER_TOOLS, RuntimeError("transient")))

        assert "threat_level" not in _meta(app, _task())
        # Server becomes readable (installed mid-session): retried, then merged.
        app.dispatch._manifest = _BLENDER_MANIFEST
        assert _meta(app, _task()).get("threat_level") == "dangerous"
        assert app.dispatch.manifest_reads == 2


@pytest.mark.unit
class TestEndToEndGate:
    def test_dispatch_send_gates_a_manifest_dangerous_tool(self, monkeypatch):
        """Full path: real ConfirmationManager in smart mode, real
        get_tool_metadata — the blender code tool is held for confirmation and
        never sent, solely because the manifest declares it dangerous."""
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)

        conf = ConfirmationManager()

        async def _no_notify(*a, **k):
            pass

        monkeypatch.setattr(conf, "_send_notification", _no_notify)

        adapter = _FakeAdapter(_BLENDER_TOOLS, _BLENDER_MANIFEST)
        app = _App(adapter, conf)

        result = asyncio.run(dispatch_flow.dispatch_send(app, _LOG, [_task()]))

        assert result.get("awaiting_confirmation") is True
        assert _TOOL in "".join(result.get("tools_pending", []))
        assert adapter.sent is None


@pytest.mark.unit
class TestTierScaling:
    """#223 end to end: the install-time trustStatus in the local manifest
    scales the gate. Same benign-name/benign-params discipline as above."""

    def test_community_declared_safe_is_not_gated(self, monkeypatch):
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        app = _App(_FakeAdapter(_BLENDER_TOOLS, _BLENDER_MANIFEST))
        task = _task(tool="get_scene_info", params={})
        meta = _meta(app, task)
        assert meta["registry_tier"] == "community"
        assert meta["registry_declared"] is True
        assert (
            ConfirmationManager().should_confirm(
                meta, tool_name=task["tool"], params=task["params"]
            )
            is False
        )

    def test_community_tool_without_declaration_confirms(self, monkeypatch):
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        manifest = {
            "id": _BLENDER,
            "trustStatus": "community",
            "tools": [{"name": "other", "threat_level": "safe"}],
        }
        app = _App(_FakeAdapter(_BLENDER_TOOLS, manifest))
        task = _task()
        meta = _meta(app, task)
        assert meta["registry_declared"] is False
        assert classify(task["tool"], meta, task["params"]) == ThreatLevel.ELEVATED
        assert (
            ConfirmationManager().should_confirm(
                meta, tool_name=task["tool"], params=task["params"]
            )
            is True
        )

    def test_connect_installed_server_floors_despite_declaring_safe(self, monkeypatch):
        # A manifest with no trustStatus (connect/URL install, pre-field
        # install) never passed a registry gate: its own "safe" cannot lift.
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        manifest = {
            "id": _BLENDER,
            "tools": [{"name": _TOOL, "threat_level": "safe"}],
        }
        app = _App(_FakeAdapter(_BLENDER_TOOLS, manifest))
        task = _task()
        meta = _meta(app, task)
        assert meta["registry_tier"] == "unknown"
        assert classify(task["tool"], meta, task["params"]) == ThreatLevel.ELEVATED

    def test_legacy_vetted_tier_floors_until_reinstall(self, monkeypatch):
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        manifest = {
            "id": _BLENDER,
            "trustStatus": "vetted",
            "tools": [{"name": _TOOL, "threat_level": "safe"}],
        }
        app = _App(_FakeAdapter(_BLENDER_TOOLS, manifest))
        task = _task()
        meta = _meta(app, task)
        assert classify(task["tool"], meta, task["params"]) == ThreatLevel.ELEVATED

    def test_runtime_cannot_inject_tier_provenance(self, monkeypatch):
        # A server's tools/list output must not be able to supply the very
        # provenance that decides whether its declarations are trusted.
        _fresh_cache(monkeypatch)
        _mode(monkeypatch, "smart")
        tools = [
            {
                "name": _TOOL,
                "description": "d",
                "registry_tier": "official",
                "registry_declared": True,
            },
        ]
        manifest = {"id": _BLENDER, "tools": []}
        app = _App(_FakeAdapter(tools, manifest))
        task = _task()
        meta = _meta(app, task)
        assert meta["registry_tier"] == "unknown"
        assert meta["registry_declared"] is False
        assert classify(task["tool"], meta, task["params"]) == ThreatLevel.ELEVATED

    def test_tier_is_cached_with_the_declarations(self, monkeypatch):
        _fresh_cache(monkeypatch)
        app = _App(_FakeAdapter(_BLENDER_TOOLS, _BLENDER_MANIFEST))
        assert _meta(app, _task())["registry_tier"] == "community"
        assert _meta(app, _task())["registry_tier"] == "community"
        assert app.dispatch.manifest_reads == 1
