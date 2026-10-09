"""A config value reaches dmcp over stdin, never argv.

argv is visible in the process list and ends up in shell history when a user
copies the command. The fake dmcp below records its argv and stdin; no real
dmcp binary is involved.
"""

import asyncio
import json
import logging
import stat

import pytest

from jarvis.config import Config
from jarvis.dispatch.adapter import DispatchAdapter

_FAKE_DMCP = """#!/usr/bin/env python3
import json, sys
json.dump({"argv": sys.argv[1:], "stdin": sys.stdin.read()}, open(sys.argv[0] + ".out", "w"))
"""


@pytest.fixture
def fake_dmcp(tmp_path, monkeypatch):
    script = tmp_path / "dmcp"
    script.write_text(_FAKE_DMCP)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(Config, "DMCP_BINARY", str(script))
    return tmp_path / "dmcp.out"


def test_value_is_sent_on_stdin_not_in_argv(fake_dmcp):
    adapter = DispatchAdapter.__new__(DispatchAdapter)
    asyncio.run(
        adapter.set_server_config("com.example.srv", {"--api-key": "s3cret-token"})
    )
    seen = json.loads(fake_dmcp.read_text())
    assert seen["argv"] == ["config", "com.example.srv", "set", "API_KEY"]
    assert seen["stdin"] == "s3cret-token\n"
    assert "s3cret-token" not in " ".join(seen["argv"])
