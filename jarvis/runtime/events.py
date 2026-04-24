"""Event-loop routing and async event source helpers."""

from __future__ import annotations

import asyncio
from logging import Logger
from typing import Any, Optional

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


async def await_dispatch_signal(app: Any, logger: Logger) -> Optional[dict[str, Any]]:
    """Poll dispatch for new signals and return the latest one."""
    if not app.dispatch.is_connected:
        await asyncio.sleep(1)
        return None

    signals = await app.dispatch.get_signal_window()
    if signals:
        latest = signals[-1]
        logger.debug(
            f"JARVIS: Received {len(signals)} signal(s), forwarding latest: "
            f"type={latest.get('type')}, pid={latest.get('pid')}",
        )
        return latest

    await asyncio.sleep(0.5)
    return None
