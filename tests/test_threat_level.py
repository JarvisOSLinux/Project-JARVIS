"""Host-side threat classification + confirmation-gate floor (Project-JARVIS #159).

The bundled shell server's `run_command` (which runs `sudo -A`) declares no
`confirmation_required`, so under the default `smart` mode it previously
bypassed the confirmation gate. The host now assigns a minimum threat level per
tool, so a dangerous tool cannot opt out of gating.
"""

import pytest

from jarvis.core.confirmation_manager import ConfirmationManager
from jarvis.core.threat_level import ThreatLevel, classify


@pytest.mark.unit
class TestClassify:
    def test_command_execution_is_dangerous_without_manifest_flag(self):
        # The exact #159 case: run_command declares nothing, must be DANGEROUS.
        assert classify("run_command", {}) == ThreatLevel.DANGEROUS

    def test_benign_tool_is_safe(self):
        assert classify("web_search", {}) == ThreatLevel.SAFE

    def test_manifest_cannot_lower_below_host_floor(self):
        assert classify("run_command", {"threat_level": "safe"}) == ThreatLevel.DANGEROUS

    def test_manifest_can_raise_a_benign_tool(self):
        assert classify("web_search", {"threat_level": "dangerous"}) == ThreatLevel.DANGEROUS

    def test_confirmation_required_is_at_least_elevated(self):
        assert classify("web_search", {"confirmation_required": True}) == ThreatLevel.ELEVATED

    def test_server_qualified_name_is_handled(self):
        assert classify("shellmcp.run_command", {}) == ThreatLevel.DANGEROUS

    def test_none_tool_name_is_safe(self):
        assert classify(None, {}) == ThreatLevel.SAFE


@pytest.mark.unit
class TestShouldConfirmFloor:
    def test_dangerous_tool_confirmed_in_smart_mode_without_flag(self, monkeypatch):
        monkeypatch.setattr("jarvis.config.Config.CONFIRMATION_MODE", "smart")
        mgr = ConfirmationManager()
        # Empty metadata — the previous behavior skipped confirmation entirely.
        assert mgr.should_confirm({}, tool_name="run_command") is True

    def test_safe_tool_not_confirmed_in_smart_mode(self, monkeypatch):
        monkeypatch.setattr("jarvis.config.Config.CONFIRMATION_MODE", "smart")
        mgr = ConfirmationManager()
        assert mgr.should_confirm({}, tool_name="web_search") is False

    def test_confirmation_required_still_gates_in_smart_mode(self, monkeypatch):
        monkeypatch.setattr("jarvis.config.Config.CONFIRMATION_MODE", "smart")
        mgr = ConfirmationManager()
        assert mgr.should_confirm({"confirmation_required": True}, tool_name="web_search") is True

    def test_ask_all_confirms_everything(self, monkeypatch):
        monkeypatch.setattr("jarvis.config.Config.CONFIRMATION_MODE", "ask_all")
        mgr = ConfirmationManager()
        assert mgr.should_confirm({}, tool_name="web_search") is True

    def test_allow_all_bypasses_even_dangerous(self, monkeypatch):
        # allow_all remains the documented power-user escape hatch.
        monkeypatch.setattr("jarvis.config.Config.CONFIRMATION_MODE", "allow_all")
        mgr = ConfirmationManager()
        assert mgr.should_confirm({}, tool_name="run_command") is False
