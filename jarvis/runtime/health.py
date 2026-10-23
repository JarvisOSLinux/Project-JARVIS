"""JARVIS's own health, for `/status` (#272) and, later, socket clients (#267).

Data first: a list of sections, each a list of checks with a state and a
one-line reason. The TUI draws it; jarvis-app and the phone can be sent the
same dicts. Sign-ins are left out on purpose: they belong to each server, not
to JARVIS.

Fast by default. It reads JARVIS's own state and runs local checks (the dmcp
binary, the embedding model on the local Ollama, the audio devices), each off
the event loop and with a short timeout. ``full`` adds the one check that goes
online: whether installed servers have registry updates.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..config import Config

OK, WARN, FAIL, OFF = "ok", "warn", "fail", "off"
LOCAL_TIMEOUT = 3.0
UPDATES_TIMEOUT = 60.0


@dataclass
class Check:
    name: str
    state: str
    detail: str = ""


@dataclass
class Section:
    title: str
    checks: List[Check] = field(default_factory=list)


def as_dicts(sections: List[Section]) -> List[Dict[str, Any]]:
    return [asdict(s) for s in sections]


async def collect_status(app: Any, *, full: bool = False) -> List[Section]:
    sections = [
        _language_model(app),
        await _engines(app),
        await _voice(app),
        await _safety(),
        await _work(app),
        _session(app),
    ]
    if full:
        sections.append(await _updates())
    return sections


# --- the language model ---------------------------------------------------------------


def _language_model(app: Any) -> Section:
    section = Section("Language model")
    pool = getattr(getattr(app, "llm", None), "provider", None)
    if pool is None or not hasattr(pool, "get_status"):
        section.checks.append(
            Check("Providers", FAIL, "none configured; add one with /providers add")
        )
        return section
    active = getattr(pool, "active_provider_name", None)
    for p in pool.get_status():
        label = f"{p.get('name')} ({p.get('model')})"
        status = p.get("status")
        left = p.get("cooldown_remaining")
        error = _short(p.get("last_error"))
        if status == "active":
            check = Check(label, OK, "in use" if p.get("name") == active else "ready")
        elif status == "cooldown":
            check = Check(label, WARN, _join(f"cooling down, {left} s left", error))
        elif status == "exhausted":
            check = Check(label, WARN, _join(f"quota used up, {left} s left", error))
        elif status == "error":
            check = Check(label, FAIL, _join("rejected its key (401/403)", error))
        else:
            check = Check(label, WARN, _join(str(status), error))
        section.checks.append(check)
    if not section.checks:
        section.checks.append(Check("Providers", FAIL, "the pool is empty"))
    return section


# --- the engines ----------------------------------------------------------------------


async def _engines(app: Any) -> Section:
    section = Section("Engines")
    dispatch = getattr(app, "dispatch", None)
    section.checks.append(
        Check("dispatch", OK, "connected")
        if getattr(dispatch, "is_connected", False)
        else Check("dispatch", FAIL, "not connected; no tool can run")
    )
    section.checks.append(await asyncio.to_thread(_dmcp_check))
    contextor = getattr(app, "contextor", None)
    if contextor is None:
        section.checks.append(Check("Memory", OFF, "disabled"))
    elif getattr(contextor, "is_connected", False):
        section.checks.append(Check("Memory", OK, "connected"))
    else:
        section.checks.append(
            Check("Memory", FAIL, "not connected; sessions unavailable")
        )
    section.checks.append(await _search_check(app))
    return section


def _dmcp_check() -> Check:
    binary = shutil.which(Config.DMCP_BINARY)
    if not binary:
        return Check("dmcp", FAIL, f"'{Config.DMCP_BINARY}' is not on PATH")
    servers = None
    listing = _run([binary, "list", "--json"])
    if listing:
        try:
            servers = len(json.loads(listing))
        except ValueError:
            pass
    detail = f"found at {binary}"
    if servers is not None:
        detail += f", {servers} server{'s' if servers != 1 else ''} installed"
    return Check("dmcp", OK, detail)


async def _search_check(app: Any) -> Check:
    embeddings = getattr(app, "_embeddings", None)
    if not Config.ALLOW_EMBEDDING_SEARCH:
        return Check("Search", OFF, "keyword only (ALLOW_EMBEDDING_SEARCH is off)")
    if embeddings is None:
        return Check("Search", WARN, "keyword only: no embedding model was set up")
    model = getattr(embeddings, "model", "?")
    reachable = await asyncio.to_thread(_ollama_has_model, embeddings)
    if reachable is False:
        return Check(
            "Search",
            WARN,
            f"keyword only: the embedding model {model} is not reachable",
        )
    return Check("Search", OK, f"semantic ({model})")


def _ollama_has_model(embeddings: Any) -> Optional[bool]:
    base = getattr(embeddings, "base_url", None)
    model = str(getattr(embeddings, "model", ""))
    if not base:
        return None
    try:
        with urllib.request.urlopen(
            f"{base.rstrip('/')}/api/tags", timeout=LOCAL_TIMEOUT
        ) as resp:
            names = [m.get("name", "") for m in json.load(resp).get("models", [])]
    except Exception:
        return False
    return any(n == model or n.split(":")[0] == model.split(":")[0] for n in names)


# --- voice ----------------------------------------------------------------------------


async def _voice(app: Any) -> Section:
    from ..voice.audio import (
        check_audio_input_available,
        check_audio_output_available,
    )

    section = Section("Voice")
    # The device checks read False both with no device and with no
    # sounddevice package; only the second is fixed by installing an extra.
    audio_pkg = _has_module("sounddevice")
    if audio_pkg:
        mic, speaker = await asyncio.gather(
            asyncio.to_thread(check_audio_input_available),
            asyncio.to_thread(check_audio_output_available),
        )
    else:
        mic = speaker = None
    output = getattr(app, "output_manager", None)
    tts_ready = bool(getattr(output, "_has_tts", False))
    voice_mode = Config.OUTPUT_MODE == "voice"

    if voice_mode and not tts_ready:
        section.checks.append(
            Check(
                "Output mode",
                WARN,
                "voice, but text-to-speech isn't working, so replies are shown as text",
            )
        )
    else:
        section.checks.append(
            Check(
                "Output mode",
                OK,
                "voice (replies are spoken)" if voice_mode else "text",
            )
        )

    if tts_ready:
        section.checks.append(
            Check("Text-to-speech", OK, f"{Config.TTS_PROVIDER} ready")
        )
    elif not audio_pkg:
        section.checks.append(
            Check(
                "Text-to-speech",
                WARN if voice_mode else OFF,
                "sounddevice isn't installed: pip install 'project-jarvis[voice-output]'",
            )
        )
    elif not speaker:
        section.checks.append(Check("Text-to-speech", FAIL, "no audio output device"))
    else:
        section.checks.append(
            Check(
                "Text-to-speech",
                WARN if voice_mode else OFF,
                f"{Config.TTS_PROVIDER} not loaded (its package or voice model is missing)",
            )
        )

    stt_pkg = {"vosk": "vosk", "faster-whisper": "faster_whisper"}.get(
        Config.STT_PROVIDER, Config.STT_PROVIDER
    )
    if getattr(app, "voice_manager", None) is not None:
        section.checks.append(
            Check(
                "Listening",
                OK,
                f"{Config.STT_PROVIDER} speech-to-text, "
                f"{Config.ACTIVATION_PROVIDER} wake word",
            )
        )
    elif not audio_pkg or not _has_module(stt_pkg):
        missing = [m for m in ("sounddevice", stt_pkg) if not _has_module(m)]
        section.checks.append(
            Check(
                "Listening",
                OFF,
                f"{' and '.join(missing)} not installed: "
                "pip install 'project-jarvis[voice-input]'",
            )
        )
    elif not mic:
        section.checks.append(Check("Listening", FAIL, "no microphone found"))
    else:
        section.checks.append(
            Check(
                "Listening", OFF, "not in this mode (text only); `jarvis run` listens"
            )
        )

    section.checks.append(
        Check(
            "Speak comments",
            OK if Config.VOICE_SPEAK_COMMENTS else OFF,
            "on" if Config.VOICE_SPEAK_COMMENTS else "off (shown, not spoken)",
        )
    )
    return section


# --- safety ---------------------------------------------------------------------------


async def _safety() -> Section:
    from ..core.sudo_manager import is_sudo_enabled

    mode = Config.CONFIRMATION_MODE
    section = Section("Safety")
    section.checks.append(
        Check(
            "Confirmations",
            WARN if mode == "allow_all" else OK,
            "allow_all: nothing asks first" if mode == "allow_all" else mode,
        )
    )
    try:
        granted = await asyncio.to_thread(is_sudo_enabled)
    except Exception:
        granted = None
    if granted is None:
        section.checks.append(Check("Passwordless sudo", OFF, "unknown on this system"))
    else:
        section.checks.append(
            Check(
                "Passwordless sudo",
                WARN if granted else OK,
                "granted (jarvis sudo disable)" if granted else "not granted",
            )
        )
    return section


# --- work in progress -----------------------------------------------------------------


async def _work(app: Any) -> Section:
    section = Section("Work")
    goals = getattr(app, "goals", None)
    active = goals.get_active_goals() if goals is not None else []
    section.checks.append(
        Check("Requests", OK, f"{len(active)} active" if active else "none active")
    )
    dispatch = getattr(app, "dispatch", None)
    tasks: List[Dict[str, Any]] = []
    if getattr(dispatch, "is_connected", False):
        try:
            tasks = [
                t
                for t in await dispatch.get_task_status(tail=0)
                if t.get("state") == "running"
            ]
        except Exception:
            tasks = []
    if not tasks:
        section.checks.append(Check("Running tasks", OK, "none"))
    for t in tasks:
        what = (
            f"timer \"{t.get('label')}\", fires in {t.get('fires_in')} s"
            if t.get("type") == "timer"
            else f"{_short_id(t.get('server'))} · {t.get('tool')}"
        )
        section.checks.append(Check(f"Task {t.get('pid')}", OK, what))
    confirmation = getattr(app, "confirmation", None)
    pending = confirmation.list_pending() if confirmation is not None else []
    section.checks.append(
        Check(
            "Confirmations waiting",
            WARN if pending else OK,
            f"{len(pending)}: jarvis confirm" if pending else "none",
        )
    )
    setups = getattr(app, "setups", None)
    waiting = setups.list() if setups is not None else []
    section.checks.append(
        Check(
            "Setups waiting",
            WARN if waiting else OK,
            f"{len(waiting)}: F3 or jarvis setups" if waiting else "none",
        )
    )
    return section


# --- the session ----------------------------------------------------------------------


def _session(app: Any) -> Section:
    section = Section("Session")
    sessions = getattr(app, "sessions", None)
    current = getattr(sessions, "current", None)
    if current is None:
        section.checks.append(
            Check("Session", OFF, "none; your next message starts a new one")
        )
    else:
        section.checks.append(
            Check(
                "Session", OK, f"{current.short_id()} '{current.title or 'untitled'}'"
            )
        )
    pool = getattr(getattr(app, "llm", None), "provider", None)
    tokens = getattr(pool, "last_prompt_tokens", 0) or 0
    window = Config.LLM_CONTEXT_WINDOW
    if not tokens:
        section.checks.append(Check("Context", OK, "no model call yet"))
    elif window > 0:
        share = int(tokens / window * 100)
        section.checks.append(
            Check(
                "Context",
                WARN if share >= 80 else OK,
                f"{share}% used ({tokens}/{window} tokens; /context for the breakdown)",
            )
        )
    else:
        section.checks.append(
            Check(
                "Context",
                OK,
                f"{tokens} tokens in the last prompt (set LLM_CONTEXT_WINDOW for a %)",
            )
        )
    return section


# --- registry updates (full only) -----------------------------------------------------


async def _updates() -> Section:
    section = Section("Server updates")
    binary = shutil.which(Config.DMCP_BINARY)
    if not binary:
        section.checks.append(Check("Registry", FAIL, "dmcp is not on PATH"))
        return section
    out = await asyncio.to_thread(
        _run, [binary, "update", "--check", "--all", "--json"], UPDATES_TIMEOUT
    )
    try:
        rows = json.loads(out) if out else None
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        section.checks.append(Check("Registry", WARN, "could not check (offline?)"))
        return section
    due = [r for r in rows if isinstance(r, dict) and r.get("update_available")]
    if not due:
        section.checks.append(
            Check("Registry", OK, "every installed server is up to date")
        )
    for r in due:
        section.checks.append(
            Check(
                _short_id(r.get("id")),
                WARN,
                f"update available: dmcp update {r.get('id')}",
            )
        )
    return section


# --- helpers --------------------------------------------------------------------------


def _run(argv: List[str], timeout: float = LOCAL_TIMEOUT) -> Optional[str]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _has_module(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _short(text: Any, limit: int = 80) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _join(*parts: str) -> str:
    return ": ".join(p for p in parts if p)


def _short_id(server_id: Any) -> str:
    return str(server_id or "?").split(".")[-1]
