"""Shared provider pool CRUD — used by both CLI and TUI."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.request
from typing import Any, Callable, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from ..config import Config
from .logger import get_logger

logger = get_logger(__name__)

# Version 2 dropped the "ollama" type (JARVIS runs llama-server itself).
PROVIDERS_VERSION = 2

OLLAMA_LOCAL_URL = "http://localhost:11434"
TYPES = ("llamacpp", "api", "lmstudio", "ollama-cloud")
KEY_REMOVED = "key removed: the address changed to another host, so add the key again"
_OLLAMA_CLOUD_MODELS = "https://ollama.com/api/tags"


def load_providers() -> dict:
    """Load providers.json, returning empty structure if absent.

    A file from before version 2 is migrated here, the one door every reader
    uses, so no caller can save it back unmigrated.
    """
    providers_file = Config.PROVIDERS_FILE
    if not os.path.isfile(providers_file):
        return {"version": PROVIDERS_VERSION, "providers": []}
    with open(providers_file) as f:
        data = json.load(f)
    if data.get("version", 1) < PROVIDERS_VERSION:
        data = _migrate_file(providers_file, data)
    return data


def save_providers(data: dict) -> None:
    """Write providers.json owner-only and atomically: it holds API keys."""
    data.setdefault("version", PROVIDERS_VERSION)
    _write_private(Config.PROVIDERS_FILE, data)


def _write_private(path: str, data: dict) -> None:
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".providers-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _ollama_cloud_names() -> List[str]:
    """The model names ollama.com serves; empty when it cannot be asked."""
    try:
        with urllib.request.urlopen(_OLLAMA_CLOUD_MODELS, timeout=5) as response:
            listing = json.loads(response.read())
        return [m.get("name") or m.get("model") for m in listing.get("models", [])]
    except Exception:
        return []


def _cloud_model(model: str, cloud_names: Iterable[str]) -> str:
    """The name ollama.com's API wants for a model Ollama's app called X:cloud.

    The app tags cloud models (``nemotron-3-ultra:cloud``, ``gpt-oss:120b-cloud``)
    and the API does not; and ``gemma4:cloud`` is ``gemma4:31b`` there, so a
    bare name is looked up in ollama.com's own list.
    """
    base = re.sub(r"(:cloud|-cloud)$", "", model)
    names = [n for n in cloud_names if n]
    if not names or base in names:
        return base
    tagged = [n for n in names if n.startswith(base + ":")]
    return tagged[0] if len(tagged) == 1 else base


def _is_ollama_cloud(spec: dict) -> bool:
    model = spec.get("model", "")
    return model.endswith((":cloud", "-cloud")) or "ollama.com" in spec.get("url", "")


def migrate(
    data: dict, cloud_names: Optional[Callable[[], List[str]]] = None
) -> Tuple[dict, List[str]]:
    """Version 1 -> 2: every "ollama" entry becomes one that still works.

    A cloud model becomes ``ollama-cloud`` with its key unchanged; any other
    keeps using the user's own Ollama through its OpenAI-compatible API. An
    entry with no "type" was an Ollama one in version 1. Nothing is dropped:
    an entry that cannot be converted stays, marked inactive with the reason.
    Returns the new data and one line per change, for the user.
    """
    names: Optional[List[str]] = None
    notes = []
    for spec in data.get("providers", []):
        if spec.get("type", "ollama") != "ollama":
            continue
        name = spec.get("name", "?")
        model = spec.get("model", "")
        spec["type"] = "ollama"
        if not model:
            spec["inactive"] = (
                "an Ollama provider with no model; set its model or remove it"
            )
            notes.append(
                f"'{name}' is inactive: it is an Ollama provider with no model."
            )
        elif _is_ollama_cloud(spec):
            if names is None:
                names = (cloud_names or _ollama_cloud_names)()
            spec["type"] = "ollama-cloud"
            spec["model"] = _cloud_model(model, names)
            spec.pop("url", None)
            keyed = (
                "its API key kept"
                if spec.get("api_key")
                else "set OLLAMA_API_KEY or add a key"
            )
            notes.append(
                f"'{name}' now talks to ollama.com directly (ollama-cloud, model "
                f"'{spec['model']}', {keyed})."
            )
        else:
            spec["type"] = "api"
            spec["url"] = (spec.get("url") or OLLAMA_LOCAL_URL).rstrip("/")
            notes.append(
                f"'{name}' now uses your Ollama through its OpenAI-compatible API "
                f"(api, {spec['url']}); change it to llamacpp to have JARVIS run "
                f"'{model}' itself."
            )
    data["version"] = PROVIDERS_VERSION
    return data, notes


def _migrate_file(path: str, data: dict) -> dict:
    """Migrate, and rewrite the file only when an entry changed.

    A system install keeps the file in /etc/jarvis, which the daemon cannot
    write: there the converted entries are used for this run and the admin
    is told, instead of JARVIS failing to start.
    """
    data, notes = migrate(data)
    if not notes:
        return data
    backup = f"{path}.v1.bak"
    try:
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
            os.chmod(backup, 0o600)
        _write_private(path, data)
    except OSError as exc:
        logger.warning(
            f"providers.json needs updating (Ollama is no longer built in) but "
            f"could not be rewritten ({exc}); using the converted entries for "
            f"this run. Rewrite {path} as below, or run JARVIS once as its owner."
        )
    else:
        logger.warning(
            f"providers.json updated for this JARVIS (Ollama is no longer built "
            f"in); the old file is kept as {backup}"
        )
    for note in notes:
        logger.warning(f"  {note}")
    return data


def generate_name(ptype: str, model: str, existing: List[str]) -> str:
    """Auto-generate a unique provider name from type and model."""
    base = f"{ptype}-{re.sub(r'[^a-zA-Z0-9]', '-', model)}"
    base = re.sub(r"-+", "-", base).strip("-").lower()
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def list_providers() -> List[dict]:
    """Return the list of configured providers."""
    return load_providers().get("providers", [])


def add_provider(
    ptype: str,
    model: str,
    name: Optional[str] = None,
    url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: Optional[float] = None,
    vision: bool = False,
) -> Tuple[str, int]:
    """Add a provider. Returns (name, position) or raises ValueError."""
    ptype = ptype.lower()
    if ptype not in TYPES:
        raise ValueError(
            f"Unknown provider type '{ptype}'. Use 'llamacpp', 'api', 'lmstudio', or 'ollama-cloud'."
        )

    if url and url_error(url):
        raise ValueError(url_error(url))

    if ptype in ("lmstudio", "ollama-cloud") and not url:
        url = default_url(ptype)

    if ptype in ("api", "lmstudio") and not url:
        raise ValueError("API/LM Studio providers require a url.")

    data = load_providers()
    existing_names = [p.get("name") for p in data.get("providers", [])]

    resolved_name = name or generate_name(ptype, model, existing_names)
    if resolved_name in existing_names:
        raise ValueError(f"Provider '{resolved_name}' already exists.")

    entry: dict = {"name": resolved_name, "type": ptype, "model": model}
    if url:
        entry["url"] = url
    if api_key:
        entry["api_key"] = api_key
    if temperature is not None:
        entry["temperature"] = temperature
    if vision:
        entry["vision"] = True

    data.setdefault("providers", []).append(entry)
    save_providers(data)
    return resolved_name, len(data["providers"])


def remove_provider(name: str) -> None:
    """Remove a provider by name. Raises ValueError if not found."""
    data = load_providers()
    providers = data.get("providers", [])
    before = len(providers)
    data["providers"] = [p for p in providers if p.get("name") != name]

    if len(data["providers"]) == before:
        raise ValueError(f"Provider '{name}' not found.")

    save_providers(data)


def move_provider(name: str, position: int) -> None:
    """Move a provider to a 1-based position. Raises ValueError on errors."""
    data = load_providers()
    providers = data.get("providers", [])

    idx = None
    for i, p in enumerate(providers):
        if p.get("name") == name:
            idx = i
            break

    if idx is None:
        raise ValueError(f"Provider '{name}' not found.")

    if position < 1 or position > len(providers):
        raise ValueError(f"Position must be between 1 and {len(providers)}.")

    entry = providers.pop(idx)
    providers.insert(position - 1, entry)
    data["providers"] = providers
    save_providers(data)


_DEFAULT_PORTS = {"http": 80, "https": 443}
_DEFAULT_URLS = {"lmstudio": "http://localhost:1234/v1"}


def default_url(ptype: str) -> str:
    """Where a provider of `ptype` with no url of its own sends requests."""
    if ptype == "ollama-cloud":
        return Config.OLLAMA_CLOUD_URL
    return _DEFAULT_URLS.get(ptype, "")


def effective_url(entry: dict) -> str:
    return entry.get("url") or default_url(str(entry.get("type", "")))


def origin(url: Any) -> Tuple[str, str, Optional[int]]:
    """Scheme, host and port: where a request to `url` goes.

    Total: an address that does not parse is ("", "", None), never an error,
    since providers.json is read where a raise would stop JARVIS starting.
    """
    if not isinstance(url, str):
        return "", "", None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return "", "", None
    scheme = parts.scheme.lower()
    if port == _DEFAULT_PORTS.get(scheme):
        port = None
    return scheme, (parts.hostname or "").lower(), port


def url_error(url: Any) -> Optional[str]:
    """Why `url` cannot be a provider's address, or None."""
    scheme, host, _ = origin(url)
    if scheme not in _DEFAULT_PORTS or not host:
        return f"'{url}' is not an address: it must start with http:// or https:// and name a host."
    return None


def edit_provider(name: str, **fields) -> List[str]:
    """Update fields on an existing provider. Returns list of updated field names.

    A key belongs to the address it was saved for. Moving a provider to
    another host without giving a key removes its stored one, so an edit
    (from a phone, say) cannot send the key to a server of its choosing.
    """
    if fields.get("type") is not None:
        ptype = str(fields["type"]).lower()
        if ptype not in TYPES:
            raise ValueError(
                f"Unknown provider type '{fields['type']}'. Use "
                + ", ".join(f"'{t}'" for t in TYPES)
                + "."
            )
        fields = {**fields, "type": ptype}
    field_map = {
        "model": "model",
        "url": "url",
        "key": "api_key",
        "type": "type",
        "temperature": "temperature",
        "vision": "vision",
    }

    data = load_providers()
    target = None
    for p in data.get("providers", []):
        if p.get("name") == name:
            target = p
            break

    if target is None:
        raise ValueError(f"Provider '{name}' not found.")

    if fields.get("url") is not None and url_error(fields["url"]):
        raise ValueError(url_error(fields["url"]))

    was = origin(effective_url(target))
    updated = []
    for flag_key, json_key in field_map.items():
        if flag_key in fields and fields[flag_key] is not None:
            value = fields[flag_key]
            if flag_key == "vision" and isinstance(value, str):
                value = value.strip().lower() in ("true", "1", "yes", "on")
            target[json_key] = value
            updated.append(flag_key)

    if not updated:
        raise ValueError(
            "No recognized fields. Use model, url, key, temperature, type, or vision."
        )

    if origin(effective_url(target)) != was:
        # Hand-written headers go even with a new key: they may hold one too.
        dropped = target.pop("headers", None) is not None
        if "key" not in updated:
            dropped = target.pop("api_key", None) is not None or dropped
        if dropped:
            updated.append(KEY_REMOVED)

    save_providers(data)
    return updated


def parse_flags(args: list) -> dict:
    """Parse --flag value pairs from an argument list."""
    flags: dict = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            key = args[i][2:]
            flags[key] = args[i + 1]
            i += 2
        else:
            i += 1
    return flags
