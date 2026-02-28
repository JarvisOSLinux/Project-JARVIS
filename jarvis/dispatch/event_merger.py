"""
EventMerger — async dual-input listener.

Merges two input sources into a single event stream:
1. User input (text typed or voice transcribed)
2. Dispatch signals (task completions, reminders, etc.)

Whichever fires first wakes Jarvis. This is what allows the user
to keep talking while dispatch tasks are running.
"""

import asyncio
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List, Callable, Awaitable
from ..core.logger import get_logger

logger = get_logger(__name__)


class EventType(Enum):
    USER_INPUT = "user_input"
    DISPATCH_SIGNAL = "dispatch_signal"
    SHUTDOWN = "shutdown"


@dataclass
class Event:
    """A single event from either input source."""

    type: EventType
    data: Any = None
    timestamp: float = field(default_factory=lambda: __import__('time').time())

    @staticmethod
    def user_input(text: str) -> 'Event':
        return Event(type=EventType.USER_INPUT, data=text)

    @staticmethod
    def dispatch_signal(signal: Dict[str, Any]) -> 'Event':
        return Event(type=EventType.DISPATCH_SIGNAL, data=signal)

    @staticmethod
    def shutdown() -> 'Event':
        return Event(type=EventType.SHUTDOWN)


class EventMerger:
    """
    Merges user input and dispatch signals into a single async event queue.

    Usage:
        merger = EventMerger()
        merger.start(user_source, signal_source)

        async for event in merger:
            if event.type == EventType.USER_INPUT:
                ...
            elif event.type == EventType.DISPATCH_SIGNAL:
                ...
    """

    def __init__(self, max_queue_size: int = 100):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._running = False
        self._tasks: List[asyncio.Task] = []

    def start(
        self,
        user_source: Callable[[], Awaitable[str]],
        signal_source: Callable[[], Awaitable[Optional[Dict[str, Any]]]],
    ):
        """
        Start listening to both input sources.

        Args:
            user_source: Async callable that awaits and returns user text input.
                         Called in a loop — each call blocks until user provides input.
            signal_source: Async callable that awaits and returns the next dispatch signal.
                           Returns None if no signal (timeout). Called in a loop.
        """
        self._running = True
        self._tasks = [
            asyncio.create_task(self._listen_user(user_source)),
            asyncio.create_task(self._listen_signals(signal_source)),
        ]
        logger.info("EventMerger: Started listening")

    async def stop(self):
        """Stop listening and clean up."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        # Drain any cancelled tasks
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("EventMerger: Stopped")

    async def get_next_event(self) -> Event:
        """Block until the next event arrives from either source."""
        return await self._queue.get()

    async def push_event(self, event: Event):
        """Manually push an event (e.g., for shutdown)."""
        await self._queue.put(event)

    async def _listen_user(self, source: Callable[[], Awaitable[str]]):
        """Loop: await user input, push as event."""
        while self._running:
            try:
                text = await source()
                if text and text.strip():
                    await self._queue.put(Event.user_input(text.strip()))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"EventMerger: User input error: {e}")

    async def _listen_signals(self, source: Callable[[], Awaitable[Optional[Dict[str, Any]]]]):
        """Loop: await dispatch signals, push as events."""
        while self._running:
            try:
                signal = await source()
                if signal is not None:
                    await self._queue.put(Event.dispatch_signal(signal))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"EventMerger: Signal source error: {e}")

    def __aiter__(self):
        return self

    async def __anext__(self) -> Event:
        if not self._running and self._queue.empty():
            raise StopAsyncIteration
        event = await self.get_next_event()
        if event.type == EventType.SHUTDOWN:
            raise StopAsyncIteration
        return event
