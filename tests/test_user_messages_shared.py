"""What the user says reaches every screen (Yakup, 2026-11-03).

He asked for a system update from his phone; jarvis-app on the laptop showed
JARVIS's comments and blocks but not the request, since a client drew its own
message and nobody told the others. Every input now goes to the other GUI
clients as `user_message`, opt-in like activity and action (jarvis-app alerts
on a type it does not know): typed on a GUI client (not echoed to it), typed
into `jarvis send` or the TUI, or spoken.
"""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from jarvis.runtime import io


class Writer:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.extend(json.loads(line) for line in data.decode().splitlines())

    async def drain(self):
        pass


def _app():
    phone, laptop, old = Writer(), Writer(), Writer()
    app = SimpleNamespace(
        _gui_clients={phone: "web:a", laptop: "jarvis-app", old: "jarvis-app"},
        _gui_state="idle",
        _gui_subscriptions={
            phone: {"activity", "action", "user_message"},
            laptop: {"activity", "action", "user_message"},
        },
        events=SimpleNamespace(inject_user_input=lambda text: None),
    )
    return app, phone, laptop, old


def _send(app, writer, msg):
    async def run():
        await io._process_gui_message(app, logging.getLogger("test"), msg, writer)
        await asyncio.sleep(0)

    asyncio.run(run())


def _said(writer):
    return [m for m in writer.sent if m["type"] == "user_message"]


@pytest.mark.unit
def test_a_message_from_the_phone_reaches_the_laptop():
    app, phone, laptop, old = _app()
    _send(app, phone, {"type": "message", "content": " Update my system "})
    [said] = _said(laptop)
    assert said["content"] == "Update my system"
    assert isinstance(said["at"], float)
    # The phone drew it when it sent it.
    assert _said(phone) == []
    # A client that did not subscribe would show an alert for the type.
    assert _said(old) == []


@pytest.mark.unit
def test_spoken_or_typed_elsewhere_reaches_every_subscribed_client():
    app, phone, laptop, old = _app()

    async def run():
        io.on_user_message(app, "what kernel am I on?")
        await asyncio.sleep(0)

    asyncio.run(run())
    assert [m["content"] for m in _said(phone)] == ["what kernel am I on?"]
    assert [m["content"] for m in _said(laptop)] == ["what kernel am I on?"]
    assert old.sent == []


@pytest.mark.unit
def test_a_secret_is_redacted_before_it_reaches_another_screen():
    app, phone, laptop, _ = _app()
    token = "ghp_" + "a1B2" * 9
    _send(app, laptop, {"type": "message", "content": f"use {token} please"})
    [said] = _said(phone)
    assert token not in said["content"]
    assert said["content"].startswith("use ")


@pytest.mark.unit
def test_an_empty_message_is_not_shown():
    app, phone, laptop, _ = _app()
    _send(app, phone, {"type": "message", "content": "   "})
    assert _said(laptop) == []
