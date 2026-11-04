"""The settings JARVIS can change while it runs (jarvis/core/settings.py): one
list for the TUI's settings window and every GUI-socket client."""

import logging
from unittest.mock import patch

import pytest

from jarvis.config import Config
from jarvis.core import settings
from jarvis.core.logger import JarvisLogger


@pytest.fixture
def conf_file(tmp_path, monkeypatch):
    """The config file in a temp dir, and every listed setting restored after."""
    import jarvis.cli as cli

    path = tmp_path / "jarvis.conf"
    monkeypatch.setattr(cli, "ENV_FILE", path)
    for s in settings.SETTINGS:
        monkeypatch.setattr(Config, s.key, getattr(Config, s.key))
        monkeypatch.setenv(s.key, settings.text(s))
    return path


@pytest.mark.unit
def test_every_listed_setting_is_one_jarvis_has():
    for s in settings.SETTINGS:
        assert hasattr(Config, s.key), s.key
        if s.kind == "select":
            assert settings.value(s) in [v for v, _ in s.options], s.key


@pytest.mark.unit
@pytest.mark.parametrize(
    "key, raw, expected",
    [
        ("VOICE_SPEAK_COMMENTS", "off", False),
        ("VOICE_SPEAK_COMMENTS", "TRUE", True),
        ("VOICE_SPEAK_COMMENTS", False, False),
        ("CONFIRMATION_TIMEOUT", "30", 30),
        ("CONFIRMATION_TIMEOUT", 0, 0),
        ("CONFIRMATION_TIMEOUT", 12.0, 12),
        ("RAG_MIN_SCORE", "0.45", 0.45),
        ("RAG_MIN_SCORE", 1, 1.0),
        ("OUTPUT_MODE", " voice ", "voice"),
        ("LOG_LEVEL", "DEBUG", "DEBUG"),
    ],
)
def test_values_are_read_as_their_type(key, raw, expected):
    val, error = settings.parse(settings.find(key), raw)
    assert error is None
    assert val == expected and type(val) is type(expected)


@pytest.mark.unit
@pytest.mark.parametrize(
    "key, raw, error",
    [
        (
            "VOICE_SPEAK_COMMENTS",
            "maybe",
            "Speak what JARVIS is about to do: on or off",
        ),
        (
            "CONFIRMATION_TIMEOUT",
            "-1",
            "Deny unanswered requests after (seconds): at least 0",
        ),
        (
            "CONFIRMATION_TIMEOUT",
            "1.5",
            "Deny unanswered requests after (seconds): a whole number",
        ),
        (
            "CONFIRMATION_TIMEOUT",
            True,
            "Deny unanswered requests after (seconds): a whole number",
        ),
        ("RAG_TOP_K", "0", "Memories per search: at least 1"),
        ("RAG_MIN_SCORE", "1.5", "Minimum memory match: at most 1"),
        ("RAG_MIN_SCORE", "nan", "Minimum memory match: a number"),
        ("RAG_MIN_SCORE", None, "Minimum memory match: a number"),
        ("LOG_LEVEL", "debug", "Log level: one of DEBUG, INFO, WARNING, ERROR"),
        (
            "WAKE_CHIME_PATH",
            "/nonexistent.wav",
            "Wake sound: not a file: /nonexistent.wav",
        ),
    ],
)
def test_bad_values_are_named(key, raw, error):
    assert settings.parse(settings.find(key), raw) == (None, error)


@pytest.mark.unit
def test_apply_saves_and_makes_it_live_at_once(conf_file):
    val, error = settings.apply("CONFIRMATION_TIMEOUT", "45")
    assert (val, error) == (45, None)
    assert Config.CONFIRMATION_TIMEOUT == 45
    assert "CONFIRMATION_TIMEOUT=45" in conf_file.read_text().splitlines()


@pytest.mark.unit
def test_a_bad_value_writes_nothing(conf_file):
    before = Config.RAG_TOP_K
    assert settings.apply("RAG_TOP_K", "lots") == (
        None,
        "Memories per search: a whole number",
    )
    assert Config.RAG_TOP_K == before
    assert not conf_file.exists()


@pytest.mark.unit
def test_a_key_outside_the_list_is_never_written(conf_file):
    assert settings.apply("JARVIS_SUDO_ENABLED", "true") == (
        None,
        "JARVIS_SUDO_ENABLED is not a setting JARVIS can change",
    )
    assert not conf_file.exists()


@pytest.mark.unit
def test_the_log_level_changes_without_a_restart(conf_file):
    log = JarvisLogger.get_logger("jarvis.test_settings_catalog")
    before = log.level
    try:
        settings.apply("LOG_LEVEL", "DEBUG")
        assert log.level == logging.DEBUG
    finally:
        JarvisLogger.set_level(logging.getLevelName(before))


@pytest.mark.unit
def test_describe_gives_a_client_what_it_draws():
    with patch.object(Config, "RAG_MIN_SCORE", 0.3):
        entries = {e["key"]: e for e in settings.describe()}
    score = entries["RAG_MIN_SCORE"]
    assert score == {
        "key": "RAG_MIN_SCORE",
        "label": "Minimum memory match",
        "section": settings.ADVANCED,
        "kind": "float",
        "value": 0.3,
        "hint": "0 to 1; a memory less similar than this is left out",
        "min": 0.0,
        "max": 1.0,
    }
    assert entries["OUTPUT_MODE"]["options"] == [
        {"value": "text", "label": "Shown"},
        {"value": "voice", "label": "Shown and spoken"},
    ]


@pytest.mark.unit
def test_the_tui_saves_through_the_list(conf_file, monkeypatch):
    pytest.importorskip("textual")
    from textual.widgets import Input, Static

    from tests.test_tui_keys import _tui

    monkeypatch.setattr(Config, "RAG_TOP_K", 5)

    async def drive(app, pilot):
        await pilot.press("f2")
        await pilot.pause(0.3)
        app.screen.query_one("#setting-RAG_TOP_K", Input).value = "0"
        await pilot.press("ctrl+s")
        await pilot.pause(0.2)
        refused = str(app.screen.query_one("#settings-error", Static).render())
        app.screen.query_one("#setting-RAG_TOP_K", Input).value = "8"
        await pilot.press("ctrl+s")
        await pilot.pause(0.3)
        return refused, len(app.screen_stack)

    refused, open_screens = _tui(drive)
    assert refused == "Fix: Memories per search: at least 1"
    assert open_screens == 1
    assert Config.RAG_TOP_K == 8
    assert "RAG_TOP_K=8" in conf_file.read_text().splitlines()
