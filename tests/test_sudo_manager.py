"""Tests for the sudo toggle (Project-JARVIS #158).

``sudo_manager`` writes to ``/etc/sudoers.d`` in production; every test here
redirects ``SUDOERS_DROPIN`` to a tmp path and fakes ``geteuid``/validation so
nothing touches the real system and root is never required.
"""

import getpass
import os

import pytest

import jarvis.core.sudo_manager as sm


@pytest.mark.unit
class TestIsSudoEnabled:
    def test_reflects_dropin_presence(self, monkeypatch, tmp_path):
        dropin = tmp_path / "jarvis"
        monkeypatch.setattr(sm, "SUDOERS_DROPIN", dropin)
        assert sm.is_sudo_enabled() is False
        dropin.write_text("user ALL=(ALL) ALL\n")
        assert sm.is_sudo_enabled() is True


@pytest.mark.unit
class TestTargetUser:
    def test_prefers_sudo_user(self, monkeypatch):
        monkeypatch.setenv("SUDO_USER", "yakup")
        assert sm._target_user() == "yakup"

    def test_falls_back_to_current_user(self, monkeypatch):
        monkeypatch.delenv("SUDO_USER", raising=False)
        assert sm._target_user() == getpass.getuser()


@pytest.mark.unit
class TestRootGating:
    def test_enable_is_a_noop_without_root(self, monkeypatch, tmp_path):
        dropin = tmp_path / "jarvis"
        monkeypatch.setattr(sm, "SUDOERS_DROPIN", dropin)
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        assert sm.enable_sudo() is False
        assert not dropin.exists()

    def test_disable_is_a_noop_without_root(self, monkeypatch, tmp_path):
        dropin = tmp_path / "jarvis"
        dropin.write_text("x")
        monkeypatch.setattr(sm, "SUDOERS_DROPIN", dropin)
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        assert sm.disable_sudo() is False
        assert dropin.exists()


@pytest.mark.unit
class TestInstallAndRemove:
    def test_enable_installs_validated_dropin(self, monkeypatch, tmp_path):
        dropin = tmp_path / "sudoers.d" / "jarvis"
        monkeypatch.setattr(sm, "SUDOERS_DROPIN", dropin)
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(sm, "_validate_sudoers", lambda p: True)
        monkeypatch.setenv("SUDO_USER", "yakup")

        assert sm.enable_sudo() is True
        assert dropin.exists()
        assert "yakup ALL=(ALL) ALL" in dropin.read_text()
        assert oct(dropin.stat().st_mode & 0o777) == "0o440"

    def test_enable_rejects_invalid_sudoers_and_leaves_no_temp(
        self, monkeypatch, tmp_path
    ):
        dropin = tmp_path / "sudoers.d" / "jarvis"
        monkeypatch.setattr(sm, "SUDOERS_DROPIN", dropin)
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(sm, "_validate_sudoers", lambda p: False)

        assert sm.enable_sudo() is False
        assert not dropin.exists()
        # The rejected candidate must not be left behind in /etc/sudoers.d.
        assert list((tmp_path / "sudoers.d").iterdir()) == []

    def test_disable_removes_dropin(self, monkeypatch, tmp_path):
        directory = tmp_path / "sudoers.d"
        directory.mkdir()
        dropin = directory / "jarvis"
        dropin.write_text("yakup ALL=(ALL) ALL\n")
        monkeypatch.setattr(sm, "SUDOERS_DROPIN", dropin)
        monkeypatch.setattr(os, "geteuid", lambda: 0)

        assert sm.disable_sudo() is True
        assert not dropin.exists()

    def test_disable_is_idempotent_when_absent(self, monkeypatch, tmp_path):
        dropin = tmp_path / "sudoers.d" / "jarvis"
        monkeypatch.setattr(sm, "SUDOERS_DROPIN", dropin)
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        assert sm.disable_sudo() is True
