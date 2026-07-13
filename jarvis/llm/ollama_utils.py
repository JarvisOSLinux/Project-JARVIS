"""Shared helpers for talking to a local/remote Ollama server.

Used by both the chat provider (``jarvis.llm.providers.ollama``) and the
embeddings provider (``jarvis.contextor.embeddings``) so the reachability
check and auto-start logic exist in exactly one place instead of two
near-identical copies.
"""

import subprocess
import time

from ..core.logger import get_logger

logger = get_logger(__name__)


def is_ollama_up(base_url: str) -> bool:
    """Return True if Ollama responds on base_url/api/tags within 2 s."""
    import urllib.request

    try:
        urllib.request.urlopen(f"{base_url}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def try_start_ollama(base_url: str, log_prefix: str = "") -> bool:
    """Start Ollama via platform service manager or direct spawn.

    Returns True if it comes up. ``log_prefix`` is prepended to log lines
    (e.g. ``"Embeddings"``) so callers keep their existing log identity.
    """
    prefix = f"{log_prefix}: " if log_prefix else ""
    logger.info(f"{prefix}Ollama not reachable — attempting auto-start")

    from ..platform import current as platform

    if platform.try_start_service("ollama", base_url):
        logger.info(f"{prefix}Ollama started via system service manager")
        return True

    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.warning(f"{prefix}ollama binary not found; cannot auto-start")
        return False
    except Exception as exc:
        logger.warning(f"{prefix}Failed to spawn ollama serve: {exc}")
        return False

    for _ in range(5):
        time.sleep(1)
        if is_ollama_up(base_url):
            logger.info(f"{prefix}Ollama started via direct spawn")
            return True

    logger.warning(
        f"{prefix}Ollama auto-start attempted but did not become ready in time"
    )
    return False
