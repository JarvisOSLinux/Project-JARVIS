"""Synchronous and threaded LLM calls with timing and activity lines."""

from __future__ import annotations

import asyncio
import time
from logging import Logger
from typing import Any, Dict


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
    app: Any, logger: Logger, context: str, tag: str = ""
) -> Dict[str, Any]:
    """Single LLM call with timing logs (non-blocking for UI)."""
    app._activity(f"LLM ({app.llm.mode}) is thinking…", kind="llm")
    t0 = time.perf_counter()
    response = await asyncio.to_thread(ask_llm_sync, app, logger, context, tag)
    elapsed = time.perf_counter() - t0
    app._activity(f"LLM responded in {elapsed:.1f}s.", kind="llm")
    return response
