"""The setup form (#254, phase 2), driven through running Textual apps.

One form for `jarvis setups fill` (inline under the prompt) and the TUI (a
modal, reached from the Setups view on F3 or /setups). It returns values and
writes nothing; what it returns is each changed field and each required one
that was unset, never an untouched default.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

# The TUI is an optional extra and CI's test job does not install it -- the
# same skip tests/test_tui_input.py uses.
pytest.importorskip("textual")

from textual.app import App  # noqa: E402
from textual.widgets import Button, Input  # noqa: E402

from jarvis.core.setup_queue import FormSpec, SetupQueue, form_spec  # noqa: E402
from jarvis.tui.setup_form import (  # noqa: E402
    MASK,
    SetupForm,
    SetupFormApp,
    SetupFormScreen,
)

FIELDS = [
    {"key": "URL", "label": "Server URL", "required": True},
    {"key": "PASSWORD", "label": "Password", "required": True, "sensitive": True},
    {"key": "TIMEOUT", "label": "Timeout", "default": "30"},
    {"key": "MODE", "label": "Mode"},
]


def _spec(**kw):
    base = dict(
        server_id="demo",
        server_name="Demo",
        summary="A demo server",
        fields=FIELDS,
        missing=["URL", "PASSWORD"],
    )
    base.update(kw)
    return FormSpec(**base)


def _run(spec, steps):
    """Run the form as `jarvis setups fill` does; return (result, what steps saw)."""

    async def run():
        app = SetupFormApp(spec)
        seen = {}
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            await steps(app, pilot, seen)
            await pilot.pause()
        return app.return_value, seen

    return asyncio.run(run())


def _form(app):
    return app.query_one(SetupForm)


def _inputs(app):
    return list(app.query(Input))


async def _type(pilot, text):
    await pilot.press(*text)


@pytest.mark.unit
class TestForm:
    def test_review_waits_for_every_required_setting(self):
        async def steps(app, pilot, seen):
            go = app.query_one("#setup-next", Button)
            seen["at_start"] = go.disabled
            await _type(pilot, "https://dav")
            await pilot.press("ctrl+s")  # the password is still missing
            seen["still_editing"] = not _form(app).reviewing
            seen["focus_on_password"] = app.focused is _inputs(app)[1]
            await _type(pilot, "pw")
            seen["after"] = go.disabled

        _, seen = _run(_spec(), steps)
        assert seen == {
            "at_start": True,
            "still_editing": True,
            "focus_on_password": True,
            "after": False,
        }

    def test_saves_what_was_needed_or_changed_never_an_untouched_default(self):
        async def steps(app, pilot, seen):
            await _type(pilot, "https://dav")
            await pilot.press("enter")
            await _type(pilot, "pw")
            await pilot.press("down", "down")  # past Timeout, to Mode
            await _type(pilot, "fast")
            await pilot.press("ctrl+s", "ctrl+s")

        values, _ = _run(_spec(), steps)
        assert values == {"URL": "https://dav", "PASSWORD": "pw", "MODE": "fast"}

    def test_a_required_setting_with_a_default_is_saved_as_shown(self):
        spec = _spec(
            fields=[
                {"key": "PORT", "label": "Port", "required": True, "default": "993"}
            ],
            missing=["PORT"],
        )

        async def steps(app, pilot, seen):
            await pilot.press("ctrl+s", "ctrl+s")

        values, _ = _run(spec, steps)
        assert values == {"PORT": "993"}

    def test_the_review_masks_secrets_and_back_returns_to_the_fields(self):
        async def steps(app, pilot, seen):
            await _type(pilot, "https://dav")
            await pilot.press("tab")
            await _type(pilot, "hunter2")
            await pilot.press("ctrl+s")
            text = str(app.query_one("#setup-review").render())
            seen["review"] = text
            await pilot.press("escape")
            seen["back"] = not _form(app).reviewing
            await pilot.press("escape")  # now cancels

        values, seen = _run(_spec(), steps)
        assert "hunter2" not in seen["review"] and MASK in seen["review"]
        assert "Server URL: https://dav" in seen["review"]
        assert "Unchanged: Timeout, Mode" in seen["review"]
        assert seen["back"] is True
        assert values is None

    def test_ctrl_r_shows_and_hides_a_secret_only(self):
        async def steps(app, pilot, seen):
            url, password = _inputs(app)[:2]
            await pilot.press("ctrl+r")
            seen["url"] = url.password
            await pilot.press("tab", "ctrl+r")
            seen["shown"] = password.password
            await pilot.press("ctrl+r")
            seen["hidden"] = password.password

        _, seen = _run(_spec(), steps)
        assert seen == {"url": False, "shown": False, "hidden": True}

    def test_arrows_move_between_fields_both_ways(self):
        async def steps(app, pilot, seen):
            inputs = _inputs(app)
            await pilot.press("down", "down")
            seen["down"] = app.focused is inputs[2]
            await pilot.press("up")
            seen["up"] = app.focused is inputs[1]

        _, seen = _run(_spec(), steps)
        assert seen == {"down": True, "up": True}

    def test_current_values_prefill_and_a_set_secret_is_kept_unless_retyped(self):
        spec = _spec(
            missing=[],
            current={"URL": "https://old", "TIMEOUT": "60"},
            secrets_set=["PASSWORD"],
        )

        async def steps(app, pilot, seen):
            inputs = _inputs(app)
            seen["prefill"] = [i.value for i in inputs]
            seen["placeholder"] = inputs[1].placeholder
            await pilot.press("ctrl+s", "ctrl+s")

        values, seen = _run(spec, steps)
        assert seen["prefill"] == ["https://old", "", "60", ""]
        assert "already set" in seen["placeholder"]
        assert values == {}

    def test_a_suggested_value_is_saved_unless_changed_back(self):
        spec = _spec(
            kind="configure",
            missing=[],
            current={"URL": "https://old", "TIMEOUT": "60"},
            proposed={"URL": "https://new", "TIMEOUT": "60"},
        )

        async def steps(app, pilot, seen):
            seen["title"] = str(app.query_one("#setup-title").render())
            seen["prefill"] = [i.value for i in _inputs(app)]
            await pilot.press("ctrl+s")
            seen["review"] = str(app.query_one("#setup-review").render())
            await pilot.press("ctrl+s")

        values, seen = _run(spec, steps)
        assert seen["title"] == "Change settings for Demo"
        assert seen["prefill"] == ["https://new", "", "60", ""]
        # TIMEOUT was proposed as what it already is: nothing to write.
        assert values == {"URL": "https://new"}
        assert "Server URL: https://new  (suggested by JARVIS)" in seen["review"]

    def test_the_modal_dismisses_with_the_values(self):
        class Host(App):
            result = "unset"

            async def on_mount(self):
                self.push_screen(SetupFormScreen(_spec()), self._done)

            def _done(self, values):
                self.result = values

        async def run():
            app = Host()
            async with app.run_test(size=(100, 50)) as pilot:
                await pilot.pause()
                await _type(pilot, "https://dav")
                await pilot.press("enter")
                await _type(pilot, "pw")
                await pilot.press("enter", "enter", "enter")  # through to review
                await pilot.press("enter")  # Save has focus
                await pilot.pause()
                return app.result

        assert asyncio.run(run()) == {"URL": "https://dav", "PASSWORD": "pw"}


@pytest.mark.unit
class TestFormSpec:
    def test_dmcp_config_wins_and_secrets_are_never_prefilled(self):
        setup = {
            "server_id": "demo",
            "server_name": "Demo",
            "fields": FIELDS,
            "missing": ["URL"],
        }
        spec = form_spec(
            setup,
            config={"URL": "https://dmcp", "PASSWORD": "s3cret", "TIMEOUT": ""},
            remembered={"URL": "https://old", "TIMEOUT": "45", "PASSWORD": "x"},
        )
        assert spec.current == {"URL": "https://dmcp", "TIMEOUT": "45"}
        assert spec.secrets_set == ["PASSWORD"]
        assert "s3cret" not in repr(spec)


# --- the TUI ---------------------------------------------------------------------------


MANIFEST = {"name": "Demo", "configurableProperties": FIELDS}


def _jarvis(queue):
    return SimpleNamespace(
        setups=queue,
        dispatch=SimpleNamespace(
            get_server_manifest=AsyncMock(return_value={"config": {}}),
            set_server_config=AsyncMock(),
        ),
        events=SimpleNamespace(inject_setup_result=Mock()),
    )


def _real_tui(jarvis, drive):
    """Drive the real JarvisTUI with its engine startup stubbed out."""
    from jarvis.tui import app as tui_app
    from jarvis.tui import lifecycle as tui_lifecycle

    async def no_unmount(app):
        return None

    async def run():
        app = tui_app.JarvisTUI()
        app.jarvis = jarvis
        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            await drive(app, pilot)
            await pilot.pause(0.2)

    params = Mock(get=Mock(return_value={}))
    with (
        patch.object(tui_lifecycle, "on_mount", lambda app: None),
        patch.object(tui_lifecycle, "on_unmount", no_unmount),
        patch("jarvis.core.params_store.ParamsStore", lambda sid: params),
    ):
        asyncio.run(run())
    return params


@pytest.mark.unit
class TestTui:
    def test_f3_lists_setups_and_filling_one_saves_and_resumes(self):
        queue = SetupQueue()
        older = queue.add("com.example.old", {"name": "Old"}, ["K"])
        setup = queue.add("demo", MANIFEST, ["URL", "PASSWORD"])
        older.created_at, setup.created_at = 1.0, 2.0
        jarvis = _jarvis(queue)
        screens = []

        async def drive(app, pilot):
            await pilot.press("f3")
            screens.append(type(app.screen).__name__)
            await pilot.press("down", "enter")  # the second: Demo
            await pilot.pause()
            screens.append(type(app.screen).__name__)
            await _type(pilot, "https://dav")
            await pilot.press("enter")
            await _type(pilot, "pw")
            await pilot.press("ctrl+s", "ctrl+s")

        params = _real_tui(jarvis, drive)
        assert screens == ["SetupsScreen", "SetupFormScreen"]
        jarvis.dispatch.set_server_config.assert_awaited_once_with(
            "demo", {"URL": "https://dav", "PASSWORD": "pw"}
        )
        params.set_many.assert_called_once_with({"URL": "https://dav"})
        jarvis.events.inject_setup_result.assert_called_once_with(
            {"type": "setup_completed", "id": setup.setup_id}
        )

    def test_c_cancels_the_highlighted_setup(self):
        queue = SetupQueue()
        setup = queue.add("demo", MANIFEST, ["URL"])
        jarvis = _jarvis(queue)

        async def drive(app, pilot):
            app._open_setups()
            await pilot.pause()
            await pilot.press("c")

        _real_tui(jarvis, drive)
        jarvis.events.inject_setup_result.assert_called_once_with(
            {"type": "cancel_setup", "id": setup.setup_id}
        )

    def test_closing_the_form_leaves_the_setup_pending(self):
        queue = SetupQueue()
        setup = queue.add("demo", MANIFEST, ["URL"])
        jarvis = _jarvis(queue)
        logged = []

        async def drive(app, pilot):
            app._append_log = logged.append
            await app._offer_setup(setup)
            await pilot.pause()
            await pilot.press("escape")

        _real_tui(jarvis, drive)
        jarvis.events.inject_setup_result.assert_not_called()
        jarvis.dispatch.set_server_config.assert_not_awaited()
        assert any("still pending" in line for line in logged)

    def test_the_status_bar_counts_waiting_setups(self):
        from jarvis.tui import status_bar

        queue = SetupQueue()
        app = SimpleNamespace(
            jarvis=SimpleNamespace(
                setups=queue, sessions=SimpleNamespace(current=None)
            ),
            status_text="",
        )
        status_bar.update_status(app)
        assert "setups waiting" not in app.status_text
        queue.add("demo", MANIFEST, ["URL"])
        status_bar.update_status(app)
        assert "setups waiting: 1 (F3)" in app.status_text

    def test_the_queue_tells_its_listener_about_every_change(self):
        queue = SetupQueue()
        calls = []
        queue.on_change = lambda: calls.append(len(queue.list()))
        setup = queue.add("demo", MANIFEST, ["URL"])
        queue.pop(setup.setup_id)
        assert calls == [1, 0]

    def test_a_configure_request_opens_with_the_suggestion_and_saves_it(self):
        queue = SetupQueue()
        setup = queue.add(
            "demo",
            MANIFEST,
            [],
            kind="configure",
            props=[FIELDS[0], FIELDS[2]],
            proposed={"URL": "https://new"},
        )
        jarvis = _jarvis(queue)
        seen = {}

        async def drive(app, pilot):
            await app._offer_setup(setup)
            await pilot.pause()
            seen["title"] = str(app.screen.query_one("#setup-title").render())
            await pilot.press("ctrl+s", "ctrl+s")

        params = _real_tui(jarvis, drive)
        assert seen["title"] == "Change settings for Demo"
        jarvis.dispatch.set_server_config.assert_awaited_once_with(
            "demo", {"URL": "https://new"}
        )
        params.set_many.assert_called_once_with({"URL": "https://new"})
        jarvis.events.inject_setup_result.assert_called_once_with(
            {"type": "setup_completed", "id": setup.setup_id}
        )
