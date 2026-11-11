"""The provider form (#316).

Adding NVIDIA in the TUI saved an Ollama provider pointing at the laptop: the
form started on "Ollama (local)" with the localhost URL filled in. And the API
provider appended /v1 to every address, so LM Studio's default (…/v1) and
Gemini's (…/v1beta/openai) could not work.
"""

import pytest

textual = pytest.importorskip("textual")

from textual.app import App  # noqa: E402
from textual.widgets import Input, Select, Static  # noqa: E402

from jarvis.tui.provider_modal import ProviderModal  # noqa: E402


class _Host(App):
    def __init__(self, modal):
        super().__init__()
        self.modal = modal
        self.results = []

    def on_mount(self):
        self.push_screen(self.modal, self.results.append)


async def _fill(pilot, **fields):
    screen = pilot.app.screen
    for name, value in fields.items():
        if name == "type":
            screen.query_one("#input-type", Select).value = value
        else:
            screen.query_one(f"#input-{name}", Input).value = value
        await pilot.pause()


def _error(pilot):
    return str(pilot.app.screen.query_one("#error-label", Static).render())


async def _confirm(pilot):
    pilot.app.screen.action_confirm()
    await pilot.pause()


@pytest.mark.asyncio
async def test_a_new_provider_starts_with_no_type_and_no_url():
    async with _Host(ProviderModal(mode="add")).run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert screen.query_one("#input-type", Select).value is Select.NULL
        assert screen.query_one("#input-url", Input).value == ""
        await _fill(pilot, model="nvidia/nemotron-3.5-lightning-30b-a3b", key="nvapi-x")
        await _confirm(pilot)
        assert "Choose a type" in _error(pilot)
        assert pilot.app.results == []


@pytest.mark.asyncio
async def test_an_api_provider_needs_its_url_and_the_hint_says_what_kind():
    async with _Host(ProviderModal(mode="add")).run_test() as pilot:
        await pilot.pause()
        await _fill(pilot, type="api", model="m", key="nvapi-x")
        url = pilot.app.screen.query_one("#input-url", Input)
        assert "integrate.api.nvidia.com" in url.placeholder
        await _confirm(pilot)
        assert "needs its URL" in _error(pilot)
        await _fill(pilot, url="https://integrate.api.nvidia.com")
        await _confirm(pilot)
        result = pilot.app.results[0]
        assert (result.ptype, result.url, result.api_key) == (
            "api",
            "https://integrate.api.nvidia.com",
            "nvapi-x",
        )


@pytest.mark.asyncio
async def test_a_key_on_a_local_ollama_provider_is_questioned_once():
    async with _Host(ProviderModal(mode="add")).run_test() as pilot:
        await pilot.pause()
        await _fill(
            pilot,
            type="ollama",
            model="nvidia/nemotron-3-super-120b-a12b",
            key="nvapi-x",
        )
        await _confirm(pilot)
        assert "choose API (cloud)" in _error(pilot)
        assert pilot.app.results == []
        await _confirm(pilot)
        assert pilot.app.results[0].ptype == "ollama"


@pytest.mark.asyncio
async def test_an_api_provider_at_ollamas_local_address_is_questioned():
    async with _Host(ProviderModal(mode="add")).run_test() as pilot:
        await pilot.pause()
        await _fill(
            pilot, type="api", model="qwen3:4b", key="k", url="http://localhost:11434"
        )
        await _confirm(pilot)
        assert "Ollama's address on this computer" in _error(pilot)
        await _fill(pilot, url="http://localhost:11434/")
        await _confirm(pilot)
        assert pilot.app.results == [], "an edit asks again"


@pytest.mark.asyncio
async def test_editing_keeps_the_saved_type_and_url():
    existing = {"name": "general", "type": "ollama", "model": "nemotron-3-ultra:cloud"}
    async with _Host(ProviderModal(mode="edit", existing=existing)).run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert screen.query_one("#input-type", Select).value == "ollama"
        await _confirm(pilot)
        assert pilot.app.results[0].ptype == "ollama"
