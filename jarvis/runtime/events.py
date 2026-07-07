"""Event-loop routing and async event source helpers."""

from __future__ import annotations

import asyncio
from logging import Logger
from typing import Any, Set

from ..core.logger import get_logger
from ..dispatch.event_merger import Event, EventType
from .root_handlers import (
    on_confirmation_response,
    on_dispatch_signal,
    on_user_input,
)

logger = get_logger(__name__)


async def handle_event(app: Any, event: Event) -> None:
    """Route merged events to the appropriate app handler."""
    if event.type == EventType.USER_INPUT:
        await on_user_input(app, logger, event.data)
    elif event.type == EventType.DISPATCH_SIGNAL:
        await on_dispatch_signal(app, logger, event.data)
    elif event.type == EventType.CONFIRMATION_RESPONSE:
        await on_confirmation_response(app, logger, event.data)


def track_event_task(
    event_tasks: Set[asyncio.Task], task: asyncio.Task, task_logger: Logger
) -> None:
    """Track a spawned per-event task and log any unhandled exception once it
    finishes, instead of letting it disappear silently (#154). Each merged
    event now runs as its own task rather than being awaited inline, so a
    goal that raises must not be allowed to take the whole daemon down --
    but it also must not vanish from the logs either.
    """
    event_tasks.add(task)

    def _on_done(finished: asyncio.Task) -> None:
        event_tasks.discard(finished)
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            task_logger.error(
                f"JARVIS: Unhandled error in event handler: {exc}", exc_info=exc
            )

    task.add_done_callback(_on_done)


async def await_user_input() -> str:
    """Read user input from stdin without blocking the event loop."""
    return await asyncio.get_event_loop().run_in_executor(
        None,
        input,
        "",
    )
