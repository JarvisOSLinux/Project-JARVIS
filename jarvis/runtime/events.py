"""Event-loop routing and async event source helpers."""

from __future__ import annotations

import asyncio
from typing import Any

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


async def await_user_input() -> str:
    """Read user input from stdin without blocking the event loop."""
    return await asyncio.get_event_loop().run_in_executor(
        None,
        input,
        "",
    )
