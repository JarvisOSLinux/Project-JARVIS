"""Local GGUF model store: ``jarvis model pull|list|rm`` and runner lookups.

A model spec is a path to a ``.gguf`` file or a Hugging Face reference
``user/repo[:quant]``. Pulled files live in ``MODELS_DIR/llm/<user>/<repo>/``;
the runner calls :func:`ensure` so a provider added without an explicit pull
still downloads on first start.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ..config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)

HF_URL = "https://huggingface.co"
DEFAULT_QUANT = "Q4_K_M"
CHUNK = 1 << 20
SPEC_RE = re.compile(r"^([\w.-]+)/([\w.-]+)(?::([\w.-]+))?$")
SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)

Progress = Callable[[str, int, int], None]


class ModelError(RuntimeError):
    """A model spec is malformed, unknown, or could not be downloaded."""


def store_dir() -> Path:
    return Path(Config.MODELS_DIR) / "llm"


def parse_spec(spec: str) -> Tuple[str, str, Optional[str]]:
    """Split ``user/repo[:quant]`` into (user, repo, quant)."""
    match = SPEC_RE.match(spec)
    if not match:
        raise ModelError(
            f"'{spec}' is not a .gguf path or a Hugging Face reference "
            "(expected user/repo or user/repo:quant)"
        )
    return match.group(1), match.group(2), match.group(3)


def _repo_dir(user: str, repo: str) -> Path:
    return store_dir() / user / repo


def _first_shard(files: List[Path]) -> Path:
    return sorted(files)[0]


def _matches(name: str, quant: Optional[str]) -> bool:
    if name.lower().startswith("mmproj") or not name.lower().endswith(".gguf"):
        return False
    return quant is None or quant.lower() in name.lower()


def local_path(spec: str) -> Optional[Path]:
    """Path to the pulled file for ``spec``, or None when it is not on disk."""
    if os.path.isfile(spec):
        return Path(spec)
    try:
        user, repo, quant = parse_spec(spec)
    except ModelError:
        return None
    directory = _repo_dir(user, repo)
    if not directory.is_dir():
        return None
    wanted = quant or DEFAULT_QUANT
    found = [p for p in directory.glob("*.gguf") if _matches(p.name, wanted)]
    if not found and quant is None:
        found = [p for p in directory.glob("*.gguf") if _matches(p.name, None)]
    return _first_shard(found) if found else None


def _headers() -> dict:
    token = os.environ.get("HF_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _choose_files(siblings: List[str], quant: Optional[str]) -> List[str]:
    ggufs = sorted(n for n in siblings if _matches(n, None))
    if not ggufs:
        raise ModelError("the repository has no .gguf files")
    if quant is not None:
        chosen = [n for n in ggufs if _matches(n, quant)]
        if not chosen:
            raise ModelError(
                f"no .gguf file matches quant '{quant}'; available: " + ", ".join(ggufs)
            )
    else:
        chosen = [n for n in ggufs if _matches(n, DEFAULT_QUANT)] or ggufs[:1]
    first = chosen[0]
    shard = SHARD_RE.search(first)
    if shard:
        prefix = first[: shard.start()]
        return [n for n in chosen if n.startswith(prefix) and SHARD_RE.search(n)]
    return [first]


def _download(
    httpx, url: str, dest: Path, label: str, progress: Optional[Progress]
) -> None:
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = _headers()
    if have:
        headers["Range"] = f"bytes={have}-"
    with httpx.stream(
        "GET", url, headers=headers, follow_redirects=True, timeout=30.0
    ) as response:
        if response.status_code == 416:
            part.rename(dest)
            return
        response.raise_for_status()
        resumed = response.status_code == 206
        if not resumed:
            have = 0
        total = have + int(response.headers.get("content-length", 0))
        with open(part, "ab" if resumed else "wb") as out:
            done = have
            for chunk in response.iter_bytes(CHUNK):
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(label, done, total)
    part.rename(dest)


def pull(spec: str, progress: Optional[Progress] = None) -> Path:
    """Download ``spec`` into the store and return the path to load.

    Already-pulled models return immediately; interrupted downloads resume.
    """
    existing = local_path(spec)
    if existing is not None:
        return existing
    import httpx

    user, repo, quant = parse_spec(spec)
    try:
        info = httpx.get(
            f"{HF_URL}/api/models/{user}/{repo}", headers=_headers(), timeout=30.0
        )
        if info.status_code == 404:
            raise ModelError(f"'{user}/{repo}' not found on Hugging Face")
        info.raise_for_status()
        siblings = [s["rfilename"] for s in info.json().get("siblings", [])]
        names = _choose_files(siblings, quant)
        directory = _repo_dir(user, repo)
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            dest = directory / Path(name).name
            if dest.exists():
                continue
            _download(
                httpx,
                f"{HF_URL}/{user}/{repo}/resolve/main/{name}",
                dest,
                name,
                progress,
            )
    except httpx.HTTPError as exc:
        raise ModelError(f"download of '{spec}' failed: {exc}") from exc
    path = local_path(spec)
    if path is None:
        raise ModelError(f"'{spec}' downloaded but no matching file was found")
    return path


def ensure(spec: str) -> Path:
    """Local path for ``spec``, downloading it first when missing (logs progress)."""
    last = {"pct": -1}

    def log_progress(label: str, done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 0
        if pct // 10 != last["pct"] // 10:
            logger.info(f"Pulling {label}: {pct}%")
        last["pct"] = pct

    return pull(spec, log_progress)


def list_models() -> List[Tuple[str, int]]:
    """(``user/repo/file``, size in bytes) for every pulled model file."""
    root = store_dir()
    if not root.is_dir():
        return []
    return [
        (str(p.relative_to(root)), p.stat().st_size)
        for p in sorted(root.glob("*/*/*.gguf"))
    ]


def remove(spec: str) -> int:
    """Delete the pulled files for ``spec``; returns how many were removed."""
    user, repo, quant = parse_spec(spec)
    directory = _repo_dir(user, repo)
    if not directory.is_dir():
        raise ModelError(f"'{spec}' is not pulled")
    victims = [
        p for p in directory.iterdir() if quant is None or _matches(p.name, quant)
    ]
    if not victims:
        raise ModelError(f"'{spec}' is not pulled")
    for path in victims:
        path.unlink()
    if not any(directory.iterdir()):
        shutil.rmtree(directory)
    return len(victims)
