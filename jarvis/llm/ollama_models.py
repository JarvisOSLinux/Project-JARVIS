"""List and download Ollama models for the GUI's model picker.

Works against a local server and against Ollama Cloud: both speak the same
API, the cloud one with an ``Authorization: Bearer <api_key>`` header.
Cloud-hosted models are used where they are listed and cannot be pulled.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterator, List, Optional

import httpx

DEFAULT_URL = "http://localhost:11434"
CLOUD_URL = "https://ollama.com"


class _OllamaHttp:
    """The two Ollama endpoints the picker needs (``/api/tags``, ``/api/pull``).

    JARVIS runs its own models and no longer depends on the ``ollama`` package,
    but the picker still lists and pulls from an Ollama server the user runs.
    """

    def __init__(self, url: str, api_key: Optional[str]) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(base_url=url, headers=headers, timeout=None)

    def list(self) -> Dict[str, Any]:
        resp = self._http.get("/api/tags", timeout=30)
        resp.raise_for_status()
        return resp.json()

    def pull(self, model: str, stream: bool = True) -> Iterator[Dict[str, Any]]:
        with self._http.stream(
            "POST", "/api/pull", json={"model": model, "stream": stream}
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])
                yield chunk


def _get_shared_client(url: str, api_key: Optional[str]) -> _OllamaHttp:
    return _OllamaHttp(url, api_key)


def is_cloud(url: Optional[str]) -> bool:
    """True for Ollama Cloud's own host (models there are not downloaded)."""
    return bool(url) and (url or "").rstrip("/").lower().endswith("//ollama.com")


def _client(url: Optional[str], api_key: Optional[str]) -> Any:
    return _get_shared_client((url or DEFAULT_URL).rstrip("/"), api_key or None)


def _field(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def list_models(url: Optional[str], api_key: Optional[str]) -> List[Dict[str, Any]]:
    """Models the server at ``url`` offers, as ``{name, size, modified}``."""
    listing = _client(url, api_key).list()
    models = _field(listing, "models") or []
    out = []
    for m in models:
        name = _field(m, "model") or _field(m, "name")
        if not name:
            continue
        modified = _field(m, "modified_at")
        out.append(
            {
                "name": name,
                "size": _field(m, "size") or 0,
                "modified": str(modified) if modified else "",
            }
        )
    return sorted(out, key=lambda m: m["name"])


def pull_model(
    model: str,
    url: Optional[str],
    api_key: Optional[str],
    progress: Callable[[Dict[str, Any]], None],
) -> None:
    """Download ``model``, calling ``progress({status, completed, total})``.

    Raises ValueError for a request that cannot work and lets the client's
    own errors (connection refused, unknown model) through.
    """
    model = (model or "").strip()
    if not model:
        raise ValueError("pull_model requires 'model'")
    if is_cloud(url):
        raise ValueError(
            "Ollama Cloud models are not downloaded: pick one from the list"
        )
    for chunk in _client(url, api_key).pull(model, stream=True):
        progress(
            {
                "status": _field(chunk, "status") or "",
                "completed": _field(chunk, "completed") or 0,
                "total": _field(chunk, "total") or 0,
            }
        )
