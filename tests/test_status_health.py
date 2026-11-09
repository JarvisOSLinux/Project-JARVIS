"""/status as a health check (#272).

Each section reports what works and why something doesn't, from JARVIS's own
state plus fast local checks. Sign-ins are left out (they belong to servers);
registry updates only with `full`, since that goes online.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from jarvis.config import Config
from jarvis.runtime import health


def _checks(section):
    return {c.name: (c.state, c.detail) for c in section.checks}


class _Pool:
    active_provider_name = "main"
    last_prompt_tokens = 0

    def __init__(self, rows):
        self._rows = rows

    def get_status(self):
        return self._rows


@pytest.mark.unit
class TestLanguageModel:
    def test_each_provider_state_reads_plainly(self):
        app = SimpleNamespace(
            llm=SimpleNamespace(
                provider=_Pool(
                    [
                        {"name": "main", "model": "m1", "status": "active"},
                        {"name": "spare", "model": "m2", "status": "active"},
                        {
                            "name": "busy",
                            "model": "m3",
                            "status": "cooldown",
                            "cooldown_remaining": 42,
                            "last_error": "HTTP 429 Too Many Requests",
                        },
                        {
                            "name": "broke",
                            "model": "m4",
                            "status": "exhausted",
                            "cooldown_remaining": 3600,
                        },
                        {"name": "badkey", "model": "m5", "status": "error"},
                    ]
                )
            )
        )
        got = _checks(health._language_model(app))
        assert got["main (m1)"] == ("ok", "in use")
        assert got["spare (m2)"] == ("ok", "ready")
        assert got["busy (m3)"] == (
            "warn",
            "cooling down, 42 s left: HTTP 429 Too Many Requests",
        )
        assert got["broke (m4)"] == ("warn", "quota used up, 3600 s left")
        assert got["badkey (m5)"][0] == "fail" and "401/403" in got["badkey (m5)"][1]

    def test_no_provider_says_how_to_add_one(self):
        got = _checks(health._language_model(SimpleNamespace(llm=None)))
        assert got["Providers"] == (
            "fail",
            "none configured; add one with /providers add",
        )


@pytest.mark.unit
class TestEngines:
    def test_dispatch_memory_and_a_missing_dmcp(self, monkeypatch):
        monkeypatch.setattr(Config, "DMCP_BINARY", "definitely-not-dmcp-xyz")
        monkeypatch.setattr(Config, "ALLOW_EMBEDDING_SEARCH", False)
        app = SimpleNamespace(
            dispatch=SimpleNamespace(is_connected=False),
            contextor=SimpleNamespace(is_connected=True),
        )
        got = _checks(asyncio.run(health._engines(app)))
        assert got["dispatch"] == ("fail", "not connected; no tool can run")
        assert got["dmcp"] == ("fail", "'definitely-not-dmcp-xyz' is not on PATH")
        assert got["Memory"] == ("ok", "connected")
        assert got["Search"][0] == "off"

    def test_search_falls_back_when_the_embedding_model_is_unreachable(
        self, monkeypatch
    ):
        monkeypatch.setattr(Config, "ALLOW_EMBEDDING_SEARCH", True)
        monkeypatch.setattr(health, "_ollama_has_model", lambda e: False)
        app = SimpleNamespace(_embeddings=SimpleNamespace(model="nomic-embed-text"))
        check = asyncio.run(health._search_check(app))
        assert (check.state, check.detail) == (
            "warn",
            "keyword only: the embedding model nomic-embed-text is not reachable",
        )
        monkeypatch.setattr(health, "_ollama_has_model", lambda e: True)
        check = asyncio.run(health._search_check(app))
        assert (check.state, check.detail) == ("ok", "semantic (nomic-embed-text)")


@pytest.mark.unit
class TestVoice:
    def test_missing_packages_are_named_not_mistaken_for_missing_devices(
        self, monkeypatch
    ):
        monkeypatch.setattr(Config, "OUTPUT_MODE", "voice")
        monkeypatch.setattr(Config, "STT_PROVIDER", "vosk")
        monkeypatch.setattr(health, "_has_module", lambda name: False)
        app = SimpleNamespace(
            output_manager=SimpleNamespace(_has_tts=False), voice_manager=None
        )
        got = _checks(asyncio.run(health._voice(app)))
        assert (
            got["Output mode"][0] == "warn" and "shown as text" in got["Output mode"][1]
        )
        assert "sounddevice isn't installed" in got["Text-to-speech"][1]
        assert got["Listening"] == (
            "off",
            "sounddevice and vosk not installed: pip install 'project-jarvis[voice-input]'",
        )

    def test_working_voice_reads_ready(self, monkeypatch):
        monkeypatch.setattr(Config, "OUTPUT_MODE", "voice")
        monkeypatch.setattr(health, "_has_module", lambda name: True)
        monkeypatch.setattr(
            "jarvis.voice.audio.check_audio_input_available", lambda: True
        )
        monkeypatch.setattr(
            "jarvis.voice.audio.check_audio_output_available", lambda: True
        )
        app = SimpleNamespace(
            output_manager=SimpleNamespace(_has_tts=True), voice_manager=object()
        )
        got = _checks(asyncio.run(health._voice(app)))
        assert got["Output mode"] == ("ok", "voice (replies are spoken)")
        assert got["Text-to-speech"][0] == "ok" and got["Listening"][0] == "ok"


@pytest.mark.unit
class TestSafetyWorkSession:
    def test_allow_all_and_granted_sudo_are_warnings(self, monkeypatch):
        monkeypatch.setattr(Config, "CONFIRMATION_MODE", "allow_all")
        monkeypatch.setattr("jarvis.core.sudo_manager.is_sudo_enabled", lambda: True)
        got = _checks(asyncio.run(health._safety()))
        assert got["Confirmations"] == ("warn", "allow_all: nothing asks first")
        assert got["Passwordless sudo"][0] == "warn"

    def test_work_lists_running_tasks_and_what_waits(self):
        app = SimpleNamespace(
            goals=SimpleNamespace(get_active_goals=lambda: ["g"]),
            dispatch=SimpleNamespace(
                is_connected=True,
                get_task_status=AsyncMock(
                    return_value=[
                        {
                            "pid": 4,
                            "type": "mcp",
                            "server": "com.example.mcp.shell",
                            "tool": "execute_command",
                            "state": "running",
                        },
                        {
                            "pid": 5,
                            "type": "mcp",
                            "server": "s",
                            "tool": "t",
                            "state": "exited",
                        },
                        {
                            "pid": 6,
                            "type": "timer",
                            "label": "tea",
                            "fires_in": 30,
                            "state": "running",
                        },
                    ]
                ),
            ),
            confirmation=SimpleNamespace(list_pending=lambda: [1, 2]),
            setups=SimpleNamespace(list=lambda: []),
        )
        got = _checks(asyncio.run(health._work(app)))
        assert got["Requests"] == ("ok", "1 active")
        assert got["Task 4"] == ("ok", "shell · execute_command")
        assert got["Task 6"] == ("ok", 'timer "tea", fires in 30 s')
        assert "Task 5" not in got
        assert got["Confirmations waiting"] == ("warn", "2: jarvis confirm")
        assert got["Setups waiting"] == ("ok", "none")

    def test_context_share_needs_the_window_size(self, monkeypatch):
        pool = _Pool([])
        pool.last_prompt_tokens = 8200
        app = SimpleNamespace(
            sessions=SimpleNamespace(current=None), llm=SimpleNamespace(provider=pool)
        )
        monkeypatch.setattr(Config, "LLM_CONTEXT_WINDOW", 10000)
        got = _checks(health._session(app))
        assert got["Session"][0] == "off"
        assert got["Context"][0] == "warn" and got["Context"][1].startswith("82% used")
        monkeypatch.setattr(Config, "LLM_CONTEXT_WINDOW", 0)
        assert "set LLM_CONTEXT_WINDOW" in _checks(health._session(app))["Context"][1]


@pytest.mark.unit
class TestUpdates:
    def test_only_full_goes_online_and_names_the_server(self, monkeypatch):
        calls = []
        monkeypatch.setattr(health.shutil, "which", lambda b: "/usr/bin/dmcp")

        def run(argv, timeout=health.LOCAL_TIMEOUT):
            calls.append(argv[1:])
            if argv[1:3] == ["update", "--check"]:
                return '[{"id": "com.example.mcp.shell", "update_available": true}, {"id": "b", "update_available": false}]'
            return "[]"

        monkeypatch.setattr(health, "_run", run)
        app = SimpleNamespace()
        quick = asyncio.run(health.collect_status(app))
        assert not any(c[:2] == ["update", "--check"] for c in calls)
        assert [s.title for s in quick][-1] == "Session"
        full = asyncio.run(health.collect_status(app, full=True))
        assert _checks(full[-1]) == {
            "shell": ("warn", "update available: dmcp update com.example.mcp.shell")
        }

    def test_status_never_mentions_sign_ins(self, monkeypatch):
        monkeypatch.setattr(health, "_run", lambda argv, timeout=0: None)
        text = repr(asyncio.run(health.collect_status(SimpleNamespace(), full=True)))
        assert "sign" not in text.lower() and "login" not in text.lower()


@pytest.mark.unit
def test_the_tui_draws_one_mark_per_check():
    pytest.importorskip("textual")
    from jarvis.tui.local_input import status_markup

    text = status_markup(
        [
            health.Section(
                "Engines",
                [
                    health.Check("dispatch", "ok", "connected"),
                    health.Check("dmcp", "fail", "not on PATH [x]"),
                ],
            )
        ]
    )
    assert "[green]✓[/green] dispatch: connected" in text
    assert "[red]✗[/red] dmcp: not on PATH \\[x]" in text


@pytest.mark.unit
def test_status_in_the_tui_prints_the_health_check(monkeypatch):
    pytest.importorskip("textual")
    from jarvis.tui import app as tui_app
    from jarvis.tui import lifecycle as tui_lifecycle

    monkeypatch.setattr(health, "_run", lambda argv, timeout=0: None)

    async def no_unmount(app):
        return None

    monkeypatch.setattr(tui_lifecycle, "on_mount", lambda app: None)
    monkeypatch.setattr(tui_lifecycle, "on_unmount", no_unmount)

    async def run():
        app = tui_app.JarvisTUI()
        app.jarvis = SimpleNamespace(
            llm=None,
            sessions=SimpleNamespace(current=None, current_id=None),
            events=SimpleNamespace(inject_user_input=Mock()),
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            box = app.query_one("#input")
            box.focus()
            box.value = "/status"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return list(app._export_lines)

    lines = asyncio.run(run())
    text = "\n".join(lines)
    assert "you > /status" in text
    for title in ("Language model", "Engines", "Voice", "Safety", "Work", "Session"):
        assert title in text
    assert "✗ Providers: none configured" in text
