"""A pass over the TUI's keys (#267, phase 1).

- F1-F4 opened their window but did not close it: they are priority bindings
  on the app, so the window's own binding for the same key never saw it.
- Ctrl+I, shown in the footer as "Input", is Tab in most terminals; Esc now
  takes you back to typing from the sidebar or the chat, where it left
  nothing focused.
- C in the setups list cancelled a setup at once, which tells the request
  waiting on it to give up; it now asks for a second C.
- Esc in the settings window threw away unsaved edits without a word; it now
  says what is unsaved, and a second Esc discards.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

textual = pytest.importorskip("textual")


def _tui(drive, setups=()):
    from jarvis.tui import app as tui_app
    from jarvis.tui import lifecycle as tui_lifecycle

    async def no_unmount(app):
        return None

    async def run():
        app = tui_app.JarvisTUI()
        app.jarvis = SimpleNamespace(
            setups=SimpleNamespace(
                list=lambda: list(setups),
                find=lambda i: next(
                    (SimpleNamespace(**s) for s in setups if s["setup_id"] == i), None
                ),
            ),
            events=SimpleNamespace(
                inject_setup_result=lambda m: app.injected.append(m)
            ),
            goals=SimpleNamespace(get_active_goals=lambda: []),
            dispatch=SimpleNamespace(is_connected=False),
            confirmation=SimpleNamespace(list_pending=lambda: []),
        )
        app.injected = []
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            return await drive(app, pilot)

    with (
        patch.object(tui_lifecycle, "on_mount", lambda app: None),
        patch.object(tui_lifecycle, "on_unmount", no_unmount),
    ):
        return asyncio.run(run())


@pytest.mark.unit
@pytest.mark.parametrize("key", ["f1", "f2", "f3", "f4"])
def test_each_f_key_closes_what_it_opened(key):
    async def drive(app, pilot):
        await pilot.press(key)
        await pilot.pause(0.3)
        opened = len(app.screen_stack)
        await pilot.press(key)
        await pilot.pause(0.3)
        return opened, len(app.screen_stack)

    assert _tui(drive) == (2, 1)


@pytest.mark.unit
def test_esc_goes_back_to_typing():
    async def drive(app, pilot):
        app.query_one("#session-list").focus()
        await pilot.press("escape")
        return app.focused.id if app.focused else None

    assert _tui(drive) == "input"


@pytest.mark.unit
def test_cancelling_a_setup_takes_a_second_c():
    setups = [
        {
            "setup_id": "s1",
            "server_id": "demo",
            "server_name": "Demo",
            "missing": ["TOKEN"],
            "created_at": 1,
        }
    ]

    async def drive(app, pilot):
        from textual.widgets import Static

        await pilot.press("f3")
        await pilot.pause(0.3)
        await pilot.press("c")
        await pilot.pause()
        hint = str(app.screen.query_one("#setups-hint", Static).render())
        still_open = len(app.screen_stack)
        await pilot.press("c")
        await pilot.pause(0.3)
        return hint, still_open, app.injected

    hint, still_open, injected = _tui(drive, setups)
    assert hint.startswith("Press C again to cancel the setup of Demo")
    assert still_open == 2
    assert injected == [{"type": "cancel_setup", "id": "s1"}]


@pytest.mark.unit
def test_esc_with_unsaved_settings_asks_first(monkeypatch):
    from textual.widgets import Input, Static

    from jarvis.config import Config
    from jarvis.tui.config_modal import _SETTINGS

    field = next(s for s in _SETTINGS if s.kind == "int")
    monkeypatch.setattr(Config, field.key, 5)

    async def drive(app, pilot):
        await pilot.press("f2")
        await pilot.pause(0.3)
        await pilot.press("escape")  # nothing edited: closes at once
        await pilot.pause(0.2)
        closed_clean = len(app.screen_stack)
        await pilot.press("f2")
        await pilot.pause(0.3)
        app.screen.query_one(f"#setting-{field.key}", Input).value = "7"
        await pilot.press("escape")
        await pilot.pause(0.2)
        warning = str(app.screen.query_one("#settings-error", Static).render())
        still_open = len(app.screen_stack)
        await pilot.press("escape")
        await pilot.pause(0.2)
        return closed_clean, warning, still_open, len(app.screen_stack)

    closed_clean, warning, still_open, after = _tui(drive)
    assert closed_clean == 1
    assert warning == f"Not saved: {field.label}. Ctrl+S saves; Esc again discards."
    assert (still_open, after) == (2, 1)
