"""File logging is on by default (2026-10-22).

With LOG_FILE empty by default, a duplicated reply on the reference machine
left nothing to read: jarvis.log had not changed in two months. The log now
lives under the data directory unless LOG_FILE="" turns it off; it rotates at
a size cap and every file is owner-only, since it holds what the user said.
"""

import logging
import os
import subprocess
import sys

import pytest

from jarvis.core.logger import _PrivateRotatingFileHandler


def _config_log_file(tmp_path, **env):
    full_env = {k: v for k, v in os.environ.items() if k != "LOG_FILE"}
    full_env.update(JARVIS_DATA_DIR=str(tmp_path), **env)
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from jarvis.config import Config; print(repr(Config.LOG_FILE))",
        ],
        env=full_env,
        capture_output=True,
        text=True,
        check=True,
    )
    return eval(out.stdout.strip().splitlines()[-1])


@pytest.mark.unit
class TestDefault:
    def test_on_by_default_under_the_data_directory(self, tmp_path):
        assert _config_log_file(tmp_path) == str(tmp_path / "jarvis.log")

    @pytest.mark.parametrize("value", ["", "off", "None"])
    def test_an_empty_or_off_value_turns_it_off(self, tmp_path, value):
        assert _config_log_file(tmp_path, LOG_FILE=value) == ""

    def test_a_path_of_your_own_is_kept(self, tmp_path):
        mine = str(tmp_path / "elsewhere.log")
        assert _config_log_file(tmp_path, LOG_FILE=mine) == mine


@pytest.mark.unit
def test_it_rotates_and_every_file_is_owner_only(tmp_path):
    path = tmp_path / "jarvis.log"
    handler = _PrivateRotatingFileHandler(path, maxBytes=200, backupCount=2)
    log = logging.getLogger("jarvis.test.rotation")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        for i in range(40):
            log.info("line %d with some padding to fill the file", i)
    finally:
        log.removeHandler(handler)
        handler.close()
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["jarvis.log", "jarvis.log.1", "jarvis.log.2"]
    assert all((tmp_path / f).stat().st_mode & 0o777 == 0o600 for f in files)
    assert all((tmp_path / f).stat().st_size <= 300 for f in files)
