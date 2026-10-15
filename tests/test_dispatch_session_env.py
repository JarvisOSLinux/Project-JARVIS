"""dispatch, and every server it runs, gets the user's desktop session.

The MCP SDK starts dispatch with HOME, LOGNAME, PATH, SHELL and USER only. A
sign-in wrapper then could not open its link in the browser (no DISPLAY or
WAYLAND_DISPLAY), and secret-tool could not reach the keyring ("Cannot
autolaunch D-Bus without X11 $DISPLAY"), so tokens fell back to a file.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from jarvis.dispatch import transport


@pytest.mark.unit
def test_dispatch_gets_the_desktop_session(monkeypatch):
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-dispatch")
    env = transport._desktop_session_env()
    assert env == {
        "DISPLAY": ":0",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "XDG_RUNTIME_DIR": "/run/user/1000",
    }


@pytest.mark.unit
def test_connect_hands_the_session_to_dispatch(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    captured = {}

    def fake_stdio_client(params):
        captured["env"] = params.env
        raise RuntimeError("stop before spawning")

    adapter = SimpleNamespace(session=None, _client=None, _connected=False)
    with patch("mcp.client.stdio.stdio_client", fake_stdio_client):
        with pytest.raises(RuntimeError):
            asyncio.run(transport.connect(adapter, Mock()))
    assert captured["env"]["DISPLAY"] == ":0"
    assert captured["env"]["RUST_LOG"] == "dispatch=warn"
