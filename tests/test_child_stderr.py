"""Helper processes never write to the terminal (found 2026-10-23).

`dispatch serve` was started with the MCP SDK's default errlog, the
terminal's stderr, so in the TUI its warnings ("WARN MCP task failed pid=2
…") were drawn over the screen until Textual repainted. contextor's stderr was
a pipe nobody read, which blocks it once the pipe fills. Both now write to
`<name>.log` beside the JARVIS log.
"""

import asyncio
import logging
import os
import sys
from types import SimpleNamespace

import pytest

from jarvis.core.logger import JarvisLogger


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(JarvisLogger, "_log_file", tmp_path / "jarvis.log")
    monkeypatch.setattr(JarvisLogger, "_screen_owned", False)
    return tmp_path


@pytest.mark.unit
class TestChildStderr:
    def test_beside_the_jarvis_log_owner_only_and_appended(self, log_file):
        for line in ("first\n", "second\n"):
            with JarvisLogger.child_stderr("dispatch") as f:
                f.write(line)
        path = log_file / "dispatch.log"
        assert path.read_text() == "first\nsecond\n"
        assert oct(path.stat().st_mode & 0o777) == "0o600"

    def test_rotated_once_at_the_size_cap(self, log_file, monkeypatch):
        from jarvis.core import logger

        monkeypatch.setattr(logger, "LOG_FILE_MAX_BYTES", 10)
        (log_file / "dispatch.log").write_text("x" * 20)
        JarvisLogger.child_stderr("dispatch").close()
        assert (log_file / "dispatch.log.1").read_text() == "x" * 20
        assert (log_file / "dispatch.log").read_text() == ""

    def test_without_a_log_file_the_tui_gets_nothing_on_screen(self, monkeypatch):
        monkeypatch.setattr(JarvisLogger, "_log_file", None)
        monkeypatch.setattr(JarvisLogger, "_screen_owned", True)
        with JarvisLogger.child_stderr("dispatch") as f:
            assert f.name == os.devnull
        monkeypatch.setattr(JarvisLogger, "_screen_owned", False)
        assert JarvisLogger.child_stderr("dispatch") is sys.__stderr__


@pytest.mark.unit
def test_dispatch_is_started_with_its_own_errlog(log_file, monkeypatch):
    import mcp.client.stdio as stdio

    from jarvis.dispatch import transport

    seen = {}

    def fake_stdio_client(params, errlog=None):
        seen["errlog"] = errlog.name
        raise RuntimeError("stop here")

    monkeypatch.setattr(stdio, "stdio_client", fake_stdio_client)
    adapter = SimpleNamespace(session=None, _client=None, _connected=False)
    with pytest.raises(RuntimeError):
        asyncio.run(transport.connect(adapter, logging.getLogger("test")))
    assert seen["errlog"] == str(log_file / "dispatch.log")


@pytest.mark.unit
def test_contextor_is_started_with_its_own_stderr(log_file, monkeypatch):
    from jarvis.contextor import adapter as contextor

    seen = {}

    def fake_popen(args, **kwargs):
        seen["stderr"] = kwargs["stderr"].name
        raise FileNotFoundError

    monkeypatch.setattr(contextor.subprocess, "Popen", fake_popen)
    assert contextor.ContextorAdapter().connect() is False
    assert seen["stderr"] == str(log_file / "contextor.log")
