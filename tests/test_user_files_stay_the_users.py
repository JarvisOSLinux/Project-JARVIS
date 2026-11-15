"""Files JARVIS writes as root in the user's dirs stay the user's.

The owner's run (2026-11-15): `jarvis providers add` ran as root through the
root shell server and rewrote ~/.config/jarvis/providers.json as root:root
0600; the daemon, the user, then hit PermissionError on every provider read.
Running as root is faked (os.geteuid) and os.chown records what it would give
whom, by inode, so a temp file renamed into place still counts.
"""

import json
import logging
import os
import sys

import pytest

from jarvis.config import Config
from jarvis.platform import ownership

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(sys.platform == "win32", reason="no root or chown"),
]


@pytest.fixture
def as_root(monkeypatch):
    given = {}

    def chown(path, uid, gid, *, follow_symlinks=True):
        assert follow_symlinks is False, "a symlink's target is never given away"
        given[os.lstat(path).st_ino] = (uid, gid)

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", chown)
    return given


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """A config and a data dir, not made yet, in a home the user owns."""
    config, data = tmp_path / "config" / "jarvis", tmp_path / "data" / "jarvis"
    monkeypatch.setattr(Config, "PROVIDERS_FILE", str(config / "providers.json"))
    monkeypatch.setattr(Config, "JARVIS_DATA_DIR", str(data))
    monkeypatch.setattr(
        Config, "OPENAI_SERVER_TOKEN_FILE", str(config / "openai_server_token")
    )
    return tmp_path, config, data


def _not_given_back(home, given):
    user = os.stat(home)
    missed = []
    for folder, names, files in os.walk(home):
        for name in names + files:
            path = os.path.join(folder, name)
            if given.get(os.lstat(path).st_ino) != (user.st_uid, user.st_gid):
                missed.append(os.path.relpath(path, home))
    return missed


def _providers(config, data, monkeypatch, tmp_path):
    from jarvis.core.providers import save_providers

    save_providers({"providers": [{"name": "a", "type": "api", "model": "m"}]})


def _jarvis_conf(config, data, monkeypatch, tmp_path):
    import jarvis.cli as cli

    monkeypatch.setattr(cli, "ENV_FILE", config / "jarvis.conf")
    monkeypatch.setattr(cli, "_jarvis_config_dir", None)
    monkeypatch.setattr(Config, "JARVIS_TEST_OWNER", "", raising=False)
    monkeypatch.setenv("JARVIS_TEST_OWNER", "")
    cli._update_env_setting("JARVIS_TEST_OWNER", "1")


def _web_allowlist(config, data, monkeypatch, tmp_path):
    from jarvis.server.web_gateway import Allowlist, AuditLog

    Allowlist(str(config / "web_allowlist.json")).set("alice", "owner")
    AuditLog(str(data / "web_audit.jsonl")).record("alice", "owner", {"type": "x"})


def _params(config, data, monkeypatch, tmp_path):
    from jarvis.core import params_store
    from jarvis.platform import current

    monkeypatch.setattr(type(current), "config_dir", lambda self: config)
    params_store._save({"srv": {"k": "v"}})


def _openai_token(config, data, monkeypatch, tmp_path):
    from jarvis.server.openai_compat import _load_or_create_token

    _load_or_create_token()


def _confirmations_and_constraints(config, data, monkeypatch, tmp_path):
    from jarvis.core.confirmation_manager import _atomic_write_json
    from jarvis.core.constraint_store import add_constraint

    _atomic_write_json(data / "confirmations.json", {})
    add_constraint("no etc", "/etc")


def _skills_and_attachments(config, data, monkeypatch, tmp_path):
    from jarvis.core.skill_store import save_skill
    from jarvis.runtime import attachments

    save_skill("io.github.x.y", "notes")
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-png-payload")
    attachments.adopt([attachments.store_file(photo)], "3feaa17d")
    photo.unlink()


def _sessions_goals_askpass(config, data, monkeypatch, tmp_path):
    from jarvis.dispatch.goal_manager import GoalManager
    from jarvis.runtime.askpass import install_helper
    from jarvis.sessions import BlockLog

    BlockLog(str(data / "session_blocks")).append("s1", {"kind": "x"})
    GoalManager(archive_dir=str(data))._archive_goals([])
    install_helper()


@pytest.mark.parametrize(
    "write",
    [
        _providers,
        _jarvis_conf,
        _web_allowlist,
        _params,
        _openai_token,
        _confirmations_and_constraints,
        _skills_and_attachments,
        _sessions_goals_askpass,
    ],
)
def test_every_writer_gives_what_it_made_to_the_user(
    write, dirs, as_root, monkeypatch, tmp_path
):
    home, config, data = dirs
    write(config, data, monkeypatch, tmp_path)
    assert any(os.scandir(home)), "the writer wrote nothing"
    assert _not_given_back(home, as_root) == []


def test_the_log_files_too(dirs, as_root, monkeypatch):
    from jarvis.core.logger import JarvisLogger

    home, _, data = dirs
    monkeypatch.setattr(JarvisLogger, "_file_handler", None)
    monkeypatch.setattr(JarvisLogger, "_log_file", None)
    JarvisLogger._attach_file_handler(str(data / "jarvis.log"))
    try:
        JarvisLogger.child_stderr("dispatch").close()
    finally:
        JarvisLogger._file_handler.close()
    assert _not_given_back(home, as_root) == []


def test_the_file_keeps_its_mode(dirs, as_root, monkeypatch, tmp_path):
    _providers(dirs[1], dirs[2], monkeypatch, tmp_path)
    assert os.stat(Config.PROVIDERS_FILE).st_mode & 0o777 == 0o600


def test_a_dir_root_owns_keeps_root_s_files(as_root):
    # A system install: /etc/jarvis and its files are root's.
    ownership.keep_owner("/etc/hostname")
    assert as_root == {}


def test_nothing_changes_when_not_root(dirs, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(dirs[0]).st_uid)
    monkeypatch.setattr(os, "chown", lambda *a, **k: calls.append(a))
    _providers(dirs[1], dirs[2], monkeypatch, tmp_path)
    assert calls == []


def test_only_dirs_it_makes_are_given_back(tmp_path, as_root):
    (tmp_path / "a").mkdir()
    ownership.make_dirs(tmp_path / "a" / "b" / "c", 0o700)
    made = {os.stat(tmp_path / "a" / p).st_ino for p in ("b", "b/c")}
    assert set(as_root) == made


class TestStartUpReport:
    def _fake_owner(self, monkeypatch, foreign, uid=0):
        real = os.lstat

        def lstat(path, *a, **k):
            st = real(path, *a, **k)
            if os.fspath(path) not in foreign:
                return st
            return os.stat_result(
                (st.st_mode, st.st_ino, st.st_dev, st.st_nlink, uid) + tuple(st)[5:]
            )

        monkeypatch.setattr(os, "lstat", lstat)

    def test_names_the_files_and_the_command(self, tmp_path, monkeypatch, caplog):
        config = tmp_path / "jarvis"
        config.mkdir()
        (config / "providers.json").write_text(json.dumps({"providers": []}))
        (config / "jarvis.conf").write_text("")
        self._fake_owner(monkeypatch, {str(config / "providers.json")})
        with caplog.at_level(logging.ERROR):
            ownership.report_foreign_files(logging.getLogger("t"), [config, config])
        errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert str(config / "providers.json") in errors[0]
        assert "jarvis.conf" not in errors[0]
        import grp
        import pwd

        user = pwd.getpwuid(os.geteuid()).pw_name
        group = grp.getgrgid(os.getegid()).gr_name
        assert errors[0].endswith(f"Fix: sudo chown -R {user}:{group} {config}")

    def test_quiet_when_all_is_the_user_s(self, tmp_path, caplog):
        (tmp_path / "jarvis").mkdir()
        (tmp_path / "jarvis" / "providers.json").write_text("{}")
        with caplog.at_level(logging.ERROR):
            ownership.report_foreign_files(
                logging.getLogger("t"), [tmp_path / "jarvis"]
            )
        assert caplog.records == []

    def test_a_system_install_s_dirs_are_not_the_user_s(self, caplog):
        with caplog.at_level(logging.ERROR):
            ownership.report_foreign_files(logging.getLogger("t"), ["/etc"])
        assert caplog.records == []
