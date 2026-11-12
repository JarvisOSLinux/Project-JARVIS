"""The settings JARVIS can change while it runs, one list for every front end.

The TUI's settings window and GUI-socket clients (jarvis-app) both draw from
``SETTINGS`` and change a setting only through ``apply``, which checks the
value, writes it to the config file and applies it at once. A client cannot
set a key outside this list: dmcp paths, LLM URLs and the sudo flag are config
too, and an unscoped writer on the socket would reach them.

``Advanced`` settings tune JARVIS rather than use it; front ends keep that
section out of the way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config

ADVANCED = "Advanced"


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    section: str
    kind: str  # "select", "bool", "int", "float", "sound"
    options: Tuple[Tuple[str, str], ...] = ()  # (value, label)
    hint: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None


SETTINGS: Tuple[Setting, ...] = (
    Setting(
        "CONFIRMATION_MODE",
        "Confirmation mode",
        "Safety",
        "select",
        (
            ("smart", "Smart: ask when a tool can change something"),
            ("ask_all", "Ask before every tool"),
            ("allow_all", "Allow all: never ask"),
        ),
    ),
    Setting(
        "CONFIRMATION_TIMEOUT",
        "Deny unanswered requests after (seconds)",
        "Safety",
        "int",
        hint="0 keeps them waiting until you answer",
        minimum=0,
    ),
    Setting(
        "NOTIFICATION_SILENT",
        "No approval pop-ups on this computer's desktop",
        "Safety",
        "bool",
        hint="Requests still wait in jarvis-app, on phones and in jarvis confirm",
    ),
    Setting(
        "SUDO_PASSWORD_POPUP",
        "Ask for the sudo password in jarvis-app and on phones",
        "Safety",
        "bool",
        hint="Linux. Off: root commands ask with polkit's dialog on this computer",
    ),
    Setting(
        "OUTPUT_MODE",
        "Replies",
        "Voice",
        "select",
        (("text", "Shown"), ("voice", "Shown and spoken")),
    ),
    Setting(
        "VOICE_SPEAK_COMMENTS",
        "Speak what JARVIS is about to do",
        "Voice",
        "bool",
        hint="Off still shows it in the chat",
    ),
    Setting(
        "WAKE_CHIME_PATH",
        "Wake sound",
        "Voice",
        "sound",
        hint="A WAV file, played when the wake word is heard; empty for the bundled one",
    ),
    Setting(
        "RAG_TOP_K",
        "Memories per search",
        ADVANCED,
        "int",
        hint="How many past messages JARVIS recalls for each request",
        minimum=1,
    ),
    Setting(
        "RAG_MIN_SCORE",
        "Minimum memory match",
        ADVANCED,
        "float",
        hint="0 to 1; a memory less similar than this is left out",
        minimum=0.0,
        maximum=1.0,
    ),
    Setting(
        "MAX_GOALS_IN_CONTEXT",
        "Goals sent to the model",
        ADVANCED,
        "int",
        hint="The most open requests the model sees each turn",
        minimum=1,
    ),
    Setting(
        "RESET_HISTORY_AFTER_RESPONSE",
        "Clear the model's conversation after each reply",
        ADVANCED,
        "bool",
    ),
    Setting(
        "LOG_LEVEL",
        "Log level",
        ADVANCED,
        "select",
        tuple((level, level) for level in ("DEBUG", "INFO", "WARNING", "ERROR")),
    ),
)

_BY_KEY: Dict[str, Setting] = {s.key: s for s in SETTINGS}
_TRUE = ("true", "on", "yes", "1")
_FALSE = ("false", "off", "no", "0")


def find(key: str) -> Optional[Setting]:
    return _BY_KEY.get(key)


def value(setting: Setting) -> Any:
    """What JARVIS runs with, typed by the setting's kind."""
    raw = getattr(Config, setting.key, "")
    if setting.kind == "bool":
        return raw if isinstance(raw, bool) else str(raw).lower() in _TRUE
    return raw


def text(setting: Setting, val: Any = None) -> str:
    """A value as the config file spells it."""
    val = value(setting) if val is None else val
    if setting.kind == "bool":
        return "true" if val else "false"
    return str(val)


def parse(setting: Setting, raw: Any) -> Tuple[Any, Optional[str]]:
    """``raw`` (a field's text or a socket's JSON value) as the setting's type,
    or an error that names the setting."""
    if setting.kind == "bool":
        if isinstance(raw, bool):
            return raw, None
        word = str(raw).strip().lower()
        if word in _TRUE or word in _FALSE:
            return word in _TRUE, None
        return None, f"{setting.label}: on or off"
    if setting.kind in ("int", "float"):
        return _number(setting, raw)
    word = "" if raw is None else str(raw).strip()
    if setting.kind == "select":
        allowed = [v for v, _ in setting.options]
        if word not in allowed:
            return None, f"{setting.label}: one of {', '.join(allowed)}"
        return word, None
    if setting.kind == "sound":
        if not word:
            return Config.DEFAULT_WAKE_CHIME_PATH, None
        from ..voice.chime import validate_chime_path

        error = validate_chime_path(word)
        return (None, f"{setting.label}: {error}") if error else (word, None)
    return None, f"{setting.label}: unknown kind {setting.kind}"


def _number(setting: Setting, raw: Any) -> Tuple[Any, Optional[str]]:
    whole = setting.kind == "int"
    if isinstance(raw, bool):
        raw = None
    try:
        if isinstance(raw, (int, float)):
            if whole and raw != int(raw):
                raise ValueError
            num: Any = int(raw) if whole else float(raw)
        else:
            num = int(str(raw).strip()) if whole else float(str(raw).strip())
    except (TypeError, ValueError, OverflowError):
        num = None
    if num is None or not math.isfinite(num):
        kind = "a whole number" if whole else "a number"
        return None, f"{setting.label}: {kind}"
    if setting.minimum is not None and num < setting.minimum:
        return None, f"{setting.label}: at least {_plain(setting.minimum)}"
    if setting.maximum is not None and num > setting.maximum:
        return None, f"{setting.label}: at most {_plain(setting.maximum)}"
    return num, None


def _plain(num: float) -> str:
    return str(int(num)) if num == int(num) else str(num)


def apply(key: str, raw: Any) -> Tuple[Any, Optional[str]]:
    """Check ``raw``, write it to the config file and make it live now.

    Returns the value as applied, or an error with nothing written.
    """
    setting = find(key)
    if setting is None:
        return None, f"{key} is not a setting JARVIS can change"
    val, error = parse(setting, raw)
    if error:
        return None, error
    from ..cli import _update_env_setting

    try:
        _update_env_setting(key, text(setting, val))
    except OSError as e:
        return None, f"{setting.label}: could not save ({e})"
    setattr(Config, key, val)
    _after(key, val)
    return val, None


def _after(key: str, val: Any) -> None:
    # Both are read once at startup; the rest are read where they are used.
    if key == "LOG_LEVEL":
        from .logger import JarvisLogger

        JarvisLogger.set_level(val)
    elif key == "CONFIRMATION_MODE":
        from .socket_security import warn_if_allow_all

        warn_if_allow_all(val)


def describe() -> List[Dict[str, Any]]:
    """Every setting with its current value, for a client to draw."""
    out = []
    for s in SETTINGS:
        entry: Dict[str, Any] = {
            "key": s.key,
            "label": s.label,
            "section": s.section,
            "kind": s.kind,
            "value": value(s),
        }
        if s.options:
            entry["options"] = [{"value": v, "label": lbl} for v, lbl in s.options]
        if s.hint:
            entry["hint"] = s.hint
        if s.minimum is not None:
            entry["min"] = s.minimum
        if s.maximum is not None:
            entry["max"] = s.maximum
        out.append(entry)
    return out
