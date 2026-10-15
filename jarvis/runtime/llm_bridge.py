"""Synchronous and threaded LLM calls with timing and activity lines."""

from __future__ import annotations

import time
from logging import Logger
from typing import Any, Callable, Dict, Optional

from .daemon_thread import run_in_daemon_thread
from .output_hooks import emit_activity


def ask_llm_sync(
    app: Any, logger: Logger, context: str, tag: str = ""
) -> Dict[str, Any]:
    """Single LLM call with timing logs (synchronous)."""
    logger.info(f"JARVIS [{tag}]: Calling LLM (mode={app.llm.mode})...")
    logger.debug(f"JARVIS [{tag}]: LLM context:\n{context}")

    t0 = time.perf_counter()
    response = app.llm.ask(context)
    elapsed = time.perf_counter() - t0

    logger.info(f"JARVIS [{tag}]: LLM responded in {elapsed:.2f}s")
    logger.debug(f"JARVIS [{tag}]: LLM raw response: {response}")
    return response


async def ask_llm(
    app: Any,
    logger: Logger,
    context: str,
    tag: str = "",
    mode: str | None = None,
    skip_if: Optional[Callable[[], bool]] = None,
) -> Optional[Dict[str, Any]]:
    """Single LLM call with timing logs (non-blocking for UI).

    Serialized via app.llm_lock: LLM.ask()/switch_mode() share mutable
    per-mode history state that two concurrently-running goals must never
    touch at once (#154). `mode`, when given, is asserted atomically with
    the call itself -- inside the same lock acquisition -- so a goal's turn
    can never accidentally run under a sibling goal's mode no matter how the
    event loop happens to interleave them. Callers that already guarantee
    their mode (none do currently) may omit it.

    `skip_if` is checked once the lock is held, and a true answer returns
    None without calling the model. A turn can wait a long time for the lock,
    and what it was about may be over by then (a stale reminder).
    """
    async with app.llm_lock:
        if skip_if is not None and skip_if():
            return None
        if mode is not None:
            app.llm.switch_mode(mode)
        emit_activity(app, f"LLM ({app.llm.mode}) is thinking…", kind="llm")
        t0 = time.perf_counter()
        response = await run_in_daemon_thread(ask_llm_sync, app, logger, context, tag)
        elapsed = time.perf_counter() - t0
        emit_activity(app, f"LLM responded in {elapsed:.1f}s.", kind="llm")
        return response
