"""The TUI's server config form, driven through a running Textual app.

Two bugs lived here unseen because the only test called the modal's handler
directly. The form crashed the TUI the moment it showed an optional property
nobody had saved (Textual rejects the empty class name it was given), and what
the user typed never reached the modal: each row posted its change to the App,
not up through the modal, so "Install & Save" stayed disabled on a required
key and the result carried only the defaults.
"""

import asyncio

import pytest

from jarvis.core import params_store

# The TUI is an optional extra and CI's test job does not install it -- the
# same skip tests/test_tui_input.py uses.
pytest.importorskip("textual")

from textual.app import App  # noqa: E402

from jarvis.tui.server_config_modal import ServerConfigModal  # noqa: E402

# Shaped like the Microsoft 365 wrapper's settings: optional fields with and
# without a default, plus a required secret.
PROPS = [
    {"key": "PRESET", "label": "Presets", "default": "mail,calendar"},
    {"key": "CLIENT_ID", "label": "Client id"},
    {"key": "API_KEY", "label": "API key", "required": True, "sensitive": True},
]


@pytest.fixture
def params_file(tmp_path, monkeypatch):
    path = tmp_path / "jarvis_params.toml"
    monkeypatch.setattr(params_store, "_params_path", lambda: path)
    return path


class _Host(App):
    async def on_mount(self) -> None:
        self.result = asyncio.get_running_loop().create_future()
        await self.push_screen(
            ServerConfigModal("demo", "Demo", "", PROPS, {}, self.result)
        )


def _drive(steps):
    async def run():
        app = _Host()
        async with app.run_test(size=(100, 50)) as pilot:
            await pilot.pause()
            await steps(app, pilot)
        return app

    return asyncio.run(run())


@pytest.mark.unit
def test_optional_unsaved_properties_render(params_file):
    async def steps(app, pilot):
        assert isinstance(app.screen, ServerConfigModal)
        assert app.screen.query_one("#input-CLIENT_ID").value == ""

    _drive(steps)


@pytest.mark.unit
def test_typed_values_reach_the_result(params_file):
    async def steps(app, pilot):
        install = app.screen.query_one("#btn-install")
        assert install.disabled
        app.screen.query_one("#input-API_KEY").focus()
        await pilot.press(*"s3cret")
        app.screen.query_one("#input-PRESET").focus()
        await pilot.press("end", *",files")
        await pilot.pause()
        assert not install.disabled
        await pilot.click("#btn-install")
        await pilot.pause()

    app = _drive(steps)
    result = app.result.result()
    assert result.confirmed
    assert result.values["API_KEY"] == "s3cret"
    assert result.values["PRESET"] == "mail,calendar,files"


@pytest.mark.unit
def test_typing_saves_plain_values_but_never_secrets(params_file):
    async def steps(app, pilot):
        app.screen.query_one("#input-API_KEY").focus()
        await pilot.press(*"s3cret")
        app.screen.query_one("#input-CLIENT_ID").focus()
        await pilot.press(*"abc")
        await pilot.pause()

    _drive(steps)
    saved = params_file.read_text()
    assert "abc" in saved
    assert "s3cret" not in saved
