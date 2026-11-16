"""The provider form (#316).

Adding NVIDIA in the TUI saved an Ollama provider pointing at the laptop: the
form started on "Ollama (local)" with the localhost URL filled in. And the API
provider appended /v1 to every address, so LM Studio's default (…/v1) and
Gemini's (…/v1beta/openai) could not work.
"""

import pytest

textual = pytest.importorskip("textual")

from textual.app import App  # noqa: E402
from textual.widgets import Input, Select, Static, Switch  # noqa: E402

from jarvis.config import Config  # noqa: E402
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
async def test_ollama_is_an_api_provider_and_needs_no_key_on_this_computer():
    async with _Host(ProviderModal(mode="add")).run_test() as pilot:
        await pilot.pause()
        await _fill(pilot, type="api", model="qwen3:4b", url="http://localhost:11434")
        await _confirm(pilot)
        result = pilot.app.results[0]
        assert (result.ptype, result.url, result.api_key) == (
            "api",
            "http://localhost:11434",
            "",
        )


@pytest.mark.asyncio
async def test_a_remote_api_provider_still_needs_a_key():
    async with _Host(ProviderModal(mode="add")).run_test() as pilot:
        await pilot.pause()
        await _fill(
            pilot, type="api", model="m", url="https://integrate.api.nvidia.com"
        )
        await _confirm(pilot)
        assert "API key is required" in _error(pilot)
        assert pilot.app.results == []


@pytest.mark.asyncio
async def test_a_key_on_a_local_llamacpp_provider_is_questioned_once():
    async with _Host(ProviderModal(mode="add")).run_test() as pilot:
        await pilot.pause()
        await _fill(
            pilot,
            type="llamacpp",
            model="nvidia/nemotron-3.5-lightning-30b-a3b",
            key="nvapi-x",
        )
        await _confirm(pilot)
        assert "sends no API key" in _error(pilot)
        assert "Press Add again" in _error(pilot)
        assert pilot.app.results == []
        await _confirm(pilot)
        assert pilot.app.results[0].ptype == "llamacpp"


@pytest.mark.asyncio
@pytest.mark.parametrize("ptype", ["api", "lmstudio"])
async def test_a_provider_at_jarvis_own_runner_is_questioned_once(ptype):
    url = f"http://127.0.0.1:{Config.LLM_PORT}"
    async with _Host(ProviderModal(mode="add")).run_test() as pilot:
        await pilot.pause()
        await _fill(pilot, type=ptype, model="qwen3:4b", url=url)
        await _confirm(pilot)
        assert "llama-server JARVIS runs itself" in _error(pilot)
        assert pilot.app.results == []
        await _confirm(pilot)
        assert (pilot.app.results[0].ptype, pilot.app.results[0].url) == (ptype, url)


@pytest.mark.asyncio
async def test_a_changed_field_is_questioned_again():
    async with _Host(ProviderModal(mode="add")).run_test() as pilot:
        await pilot.pause()
        await _fill(pilot, type="llamacpp", model="m", key="nvapi-x")
        await _confirm(pilot)
        await _fill(pilot, key="nvapi-y")
        await _confirm(pilot)
        assert "sends no API key" in _error(pilot)
        assert pilot.app.results == []


@pytest.mark.asyncio
async def test_editing_keeps_the_saved_type_and_url():
    existing = {"name": "general", "type": "ollama-cloud", "model": "nemotron-3-ultra"}
    async with _Host(ProviderModal(mode="edit", existing=existing)).run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert screen.query_one("#input-type", Select).value == "ollama-cloud"
        await _confirm(pilot)
        assert pilot.app.results[0].ptype == "ollama-cloud"


@pytest.mark.asyncio
async def test_an_lm_studio_provider_opens_for_editing():
    existing = {"name": "studio", "type": "lmstudio", "model": "qwen3-8b"}
    async with _Host(ProviderModal(mode="edit", existing=existing)).run_test() as pilot:
        await pilot.pause()
        assert pilot.app.screen.query_one("#input-type", Select).value == "lmstudio"
        await _confirm(pilot)
        assert pilot.app.results[0].ptype == "lmstudio"


@pytest.mark.asyncio
async def test_an_inactive_ollama_provider_opens_with_no_type_chosen():
    existing = {"name": "broken", "type": "ollama", "inactive": "no model"}
    async with _Host(ProviderModal(mode="edit", existing=existing)).run_test() as pilot:
        await pilot.pause()
        assert pilot.app.screen.query_one("#input-type", Select).value is Select.NULL
        await _fill(pilot, model="Qwen/Qwen3-4B-GGUF:Q4_K_M")
        await _confirm(pilot)
        assert "Choose a type" in _error(pilot)
        await _fill(pilot, type="llamacpp")
        await _confirm(pilot)
        assert pilot.app.results[0].ptype == "llamacpp"


def _vision(pilot):
    return pilot.app.screen.query_one("#input-vision", Switch)


@pytest.mark.asyncio
async def test_the_vision_switch_starts_off_and_is_returned():
    async with _Host(ProviderModal(mode="add")).run_test() as pilot:
        await pilot.pause()
        assert _vision(pilot).value is False
        await _fill(pilot, type="llamacpp", model="ggml-org/gemma-3-4b-it-GGUF")
        _vision(pilot).value = True
        await _confirm(pilot)
        assert pilot.app.results[0].vision is True


@pytest.mark.asyncio
async def test_settings_adds_and_edits_a_provider_that_can_see(tmp_path, monkeypatch):
    """F2 → Providers: only `jarvis providers add --vision` could make one."""
    from jarvis.core import providers
    from jarvis.core.providers import list_providers
    from jarvis.tui.config_modal import ConfigModal

    # providers.Config: a test that reloads jarvis.config leaves a new class there.
    monkeypatch.setattr(providers.Config, "PROVIDERS_FILE", str(tmp_path / "p.json"))
    async with _Host(ConfigModal(initial_tab="providers")).run_test() as pilot:
        await pilot.pause()
        pilot.app.screen._add_provider()
        await pilot.pause()
        await _fill(pilot, type="llamacpp", model="g")
        _vision(pilot).value = True
        await _confirm(pilot)
        assert list_providers()[0]["vision"] is True

        pilot.app.screen._edit_provider()
        await pilot.pause()
        assert _vision(pilot).value is True, "the edit form shows what is saved"
        _vision(pilot).value = False
        await _confirm(pilot)
        assert list_providers()[0]["vision"] is False
