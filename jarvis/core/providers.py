"""Shared provider pool CRUD — used by both CLI and TUI."""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Tuple

from ..config import Config


def load_providers() -> dict:
    """Load providers.json, returning empty structure if absent."""
    providers_file = Config.PROVIDERS_FILE
    if os.path.isfile(providers_file):
        with open(providers_file) as f:
            return json.load(f)
    return {"providers": []}


def save_providers(data: dict) -> None:
    """Write providers.json, creating parent dirs if needed."""
    providers_file = Config.PROVIDERS_FILE
    os.makedirs(os.path.dirname(providers_file), exist_ok=True)
    with open(providers_file, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


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
) -> Tuple[str, int]:
    """Add a provider. Returns (name, position) or raises ValueError."""
    ptype = ptype.lower()
    if ptype not in ("ollama", "api", "lmstudio"):
        raise ValueError(
            f"Unknown provider type '{ptype}'. Use 'ollama', 'api', or 'lmstudio'."
        )

    if ptype == "lmstudio" and not url:
        url = "http://localhost:1234/v1"

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


def edit_provider(name: str, **fields) -> List[str]:
    """Update fields on an existing provider. Returns list of updated field names."""
    field_map = {
        "model": "model",
        "url": "url",
        "key": "api_key",
        "type": "type",
        "temperature": "temperature",
    }

    data = load_providers()
    target = None
    for p in data.get("providers", []):
        if p.get("name") == name:
            target = p
            break

    if target is None:
        raise ValueError(f"Provider '{name}' not found.")

    updated = []
    for flag_key, json_key in field_map.items():
        if flag_key in fields and fields[flag_key] is not None:
            target[json_key] = fields[flag_key]
            updated.append(flag_key)

    if not updated:
        raise ValueError(
            "No recognized fields. Use model, url, key, temperature, or type."
        )

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
