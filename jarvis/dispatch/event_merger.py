"""
EventMerger — async dual-input listener.

Merges multiple input sources into a single event stream:
1. User input (voice, stdin, socket, or injected)
2. Dispatch signals (task completions, reminders, etc.)

Supports inject_user_input() for thread-safe injection from voice,
socket listeners, or CLI "jarvis send" — enabling dual input
(voice + app/CLI) in daemon or interactive mode.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..core.logger import get_logger

logger = get_logger(__name__)


class EventType(Enum):
    USER_INPUT = "user_input"
    DISPATCH_SIGNAL = "dispatch_signal"
    CONFIRMATION_RESPONSE = "confirmation_response"
    SHUTDOWN = "shutdown"


@dataclass
class Event:
    """A single event from either input source."""

    type: EventType
    data: Any = None
    timestamp: float = field(default_factory=lambda: __import__("time").time())

    @staticmethod
    def user_input(text: str) -> "Event":
        return Event(type=EventType.USER_INPUT, data=text)

    @staticmethod
    def dispatch_signal(signal: Dict[str, Any]) -> "Event":
        return Event(type=EventType.DISPATCH_SIGNAL, data=signal)

    @staticmethod
    def confirmation_response(data: Dict[str, Any]) -> "Event":
        return Event(type=EventType.CONFIRMATION_RESPONSE, data=data)

    @staticmethod
    def shutdown() -> "Event":
        return Event(type=EventType.SHUTDOWN)


class EventMerger:
    """
    Merges user input and dispatch signals into a single async event queue.

    Supports dual input: voice, stdin, socket, or inject_user_input() all feed
    the same pipeline. Use inject_user_input() for thread-safe injection from
    voice callbacks, socket listeners, or "jarvis send".

    Usage:
        merger = EventMerger()
        merger.start(user_source=..., signal_source=...)
        merger.inject_user_input("hello")  # thread-safe, from any thread

        async for event in merger:
            if event.type == EventType.USER_INPUT:
                ...
    """

    def __init__(self, max_queue_size: int = 100):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def inject_user_input(self, text: str) -> None:
        """
        Inject user input from any thread (voice, socket, CLI).
        Thread-safe. Drops empty or whitespace-only input.
        """
        if not text or not str(text).strip():
            return
        t = str(text).strip()
        if self._loop is None:
            logger.warning(
                "EventMerger: inject_user_input called before start, dropping input"
            )
            return

        def _put() -> None:
            try:
                self._queue.put_nowait(Event.user_input(t))
                logger.debug(f"EventMerger: Injected user input ({len(t)} chars)")
            except asyncio.QueueFull:
                logger.warning("EventMerger: User input queue full, dropping")

        self._loop.call_soon_threadsafe(_put)

    def inject_confirmation_response(self, data: Dict[str, Any]) -> None:
        """
        Inject a confirmation response from any thread (socket, notification).
        Thread-safe.
        """
        if self._loop is None:
            logger.warning(
                "EventMerger: inject_confirmation_response called before start"
            )
            return

        def _put() -> None:
            try:
                self._queue.put_nowait(Event.confirmation_response(data))
                logger.debug(
                    f"EventMerger: Injected confirmation response id={data.get('id')}"
                )
            except asyncio.QueueFull:
                logger.warning(
                    "EventMerger: Queue full, dropping confirmation response"
                )

        self._loop.call_soon_threadsafe(_put)

    def request_shutdown(self) -> None:
        """Thread-safe request to shut down the event loop (e.g. from signal handler)."""
        if self._loop is None:
            return

        def _push() -> None:
            asyncio.create_task(self._push_shutdown())

        self._loop.call_soon_threadsafe(_push)

    async def _push_shutdown(self) -> None:
        """Push SHUTDOWN event to end the async iteration."""
        await self._queue.put(Event.shutdown())

    def start(
        self,
        signal_source: Callable[[], Awaitable[Optional[Dict[str, Any]]]],
        user_source: Optional[Callable[[], Awaitable[str]]] = None,
    ):
        """
        Start listening to input sources.

        Args:
            signal_source: Async callable that awaits and returns the next dispatch signal.
                           Returns None if no signal (timeout). Called in a loop.
            user_source: Optional. Async callable that awaits and returns user text input.
                         When provided (e.g. stdin for chat mode), runs in parallel.
                         When None (daemon mode), only inject_user_input and socket feed input.
        """
        self._running = True
        self._loop = asyncio.get_running_loop()
        tasks = [asyncio.create_task(self._listen_signals(signal_source))]
        if user_source is not None:
            tasks.append(asyncio.create_task(self._listen_user_source(user_source)))
        self._tasks = tasks
        logger.info("EventMerger: Started listening (dual input enabled)")

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

    async def _listen_user_source(self, source: Callable[[], Awaitable[str]]):
        """Loop: await user input from stdin/chat, push as event."""
        while self._running:
            try:
                text = await source()
                if text and str(text).strip():
                    logger.debug(
                        f"EventMerger: Queued user input ({len(str(text).strip())} chars)"
                    )
                    await self._queue.put(Event.user_input(str(text).strip()))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"EventMerger: User input error: {e}")

    async def _listen_signals(
        self, source: Callable[[], Awaitable[Optional[Dict[str, Any]]]]
    ):
        """Loop: await dispatch signals, push as events."""
        while self._running:
            try:
                signal = await source()
                if signal is not None:
                    logger.debug(
                        f"EventMerger: Queued dispatch signal type={signal.get('type')}, pid={signal.get('pid')}"
                    )
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
