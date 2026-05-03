"""
EventMerger — async dual-input listener.

Merges multiple input sources into a single event stream:
1. User input (voice, stdin, socket, or injected)
2. Dispatch signals (task completions, reminders, etc.)

The signal reader runs as a background asyncio Task, polling the dispatch
signal window every 0.5 s. Each signal is fingerprinted by
(pid, type, timestamp) and tracked in a seen-set; duplicates are silently
dropped so the same signal is never delivered twice even if it stays in the
Rust window for multiple polls.

On the first poll, all already-present signals are marked seen without being
enqueued — this prevents stale signals from a previous JARVIS session being
replayed on startup.

REMIND+EXIT merging: if a REMIND for PID X is first-seen and an EXIT for the
same PID is already present in the same window snapshot, the REMIND is
enriched with _remind_completed=True / _exit=<exit signal> so ROOT gets the
full output in a single LLM turn instead of two.
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

    The signal reader runs as a background asyncio Task that polls the
    dispatch signal window. Each signal is fingerprinted by
    (pid, type, timestamp); duplicates are silently dropped so the same
    signal is never delivered twice even if it stays in the window for
    multiple polls.

    Usage:
        merger = EventMerger()
        merger.start(signal_window_source=adapter.get_signal_window, ...)
        merger.inject_user_input("hello")  # thread-safe, from any thread

        async for event in merger:
            if event.type == EventType.USER_INPUT:
                ...
    """

    def __init__(self, max_queue_size: int = 100, poll_interval: float = 0.5):
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._poll_interval = poll_interval
        self._seen: set = set()
        self._signals_initialized = False

    # ------------------------------------------------------------------
    # Thread-safe injection helpers
    # ------------------------------------------------------------------

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
        """Thread-safe."""
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

    # ------------------------------------------------------------------
    # Startup / teardown
    # ------------------------------------------------------------------

    def start(
        self,
        signal_window_source: Callable[[], Awaitable[List[Dict[str, Any]]]],
        user_source: Optional[Callable[[], Awaitable[str]]] = None,
    ) -> None:
        """
        Start listening to input sources.

        Args:
            signal_window_source: Async callable that returns the full current
                dispatch signal window (list of signal dicts). Called in a
                polling loop; deduplication and REMIND+EXIT merging happen here.
            user_source: Optional. Async callable that awaits and returns user
                text input (e.g. stdin). When None (daemon/TUI mode), only
                inject_user_input() and socket listeners feed user input.
        """
        self._running = True
        self._loop = asyncio.get_running_loop()
        tasks = [asyncio.create_task(self._listen_signals(signal_window_source))]
        if user_source is not None:
            tasks.append(asyncio.create_task(self._listen_user_source(user_source)))
        self._tasks = tasks
        logger.info("EventMerger: Started (deduplicating signal queue active)")

    async def stop(self):
        """Stop listening and clean up."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("EventMerger: Stopped")

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------

    async def get_next_event(self) -> Event:
        """Block until the next event arrives from either source."""
        return await self._queue.get()

    async def push_event(self, event: Event):
        """Manually push an event (e.g., for shutdown)."""
        await self._queue.put(event)

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

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
        self,
        window_source: Callable[[], Awaitable[List[Dict[str, Any]]]],
    ) -> None:
        """
        Poll the dispatch signal window, deduplicate, and enqueue new signals.

        On the first poll, all present signals are marked seen without enqueueing
        to avoid replaying stale signals from a previous JARVIS session.

        REMIND+EXIT merging: if a REMIND for PID X is seen for the first time
        and an EXIT for the same PID is already in the same window snapshot, the
        signal is enriched with _remind_completed=True and _exit=<exit sig> so
        ROOT can respond with the actual task output in a single LLM turn.
        """
        while self._running:
            try:
                signals = await window_source()

                if not self._signals_initialized:
                    # Mark pre-existing signals as seen on the first poll.
                    # This prevents replaying signals from a previous session.
                    for sig in signals:
                        self._seen.add(self._signal_key(sig))
                    self._signals_initialized = True
                    logger.debug(
                        f"EventMerger: Signal seen-set initialized "
                        f"({len(self._seen)} pre-existing signal(s) skipped)"
                    )
                    await asyncio.sleep(self._poll_interval)
                    continue

                if signals:
                    exits_by_pid: Dict[Any, Dict[str, Any]] = {
                        s["pid"]: s
                        for s in signals
                        if s.get("type") in ("EXIT", "TIMEOUT") and "pid" in s
                    }
                    for sig in signals:
                        key = self._signal_key(sig)
                        if key in self._seen:
                            continue
                        self._seen.add(key)

                        if sig.get("type") == "REMIND":
                            pid = sig.get("pid")
                            if pid in exits_by_pid:
                                # Task already finished — merge exit info so
                                # ROOT gets the full picture in one LLM turn.
                                sig = {
                                    **sig,
                                    "_remind_completed": True,
                                    "_exit": exits_by_pid[pid],
                                }
                                logger.info(
                                    f"EventMerger: REMIND+EXIT merged for pid={pid}"
                                )

                        logger.debug(
                            f"EventMerger: Queued signal "
                            f"type={sig.get('type')}, pid={sig.get('pid')}"
                        )
                        try:
                            self._queue.put_nowait(Event.dispatch_signal(sig))
                        except asyncio.QueueFull:
                            logger.warning(
                                f"EventMerger: Queue full, dropping signal "
                                f"type={sig.get('type')}, pid={sig.get('pid')}"
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"EventMerger: Signal reader error: {e}")

            await asyncio.sleep(self._poll_interval)

    @staticmethod
    def _signal_key(sig: Dict[str, Any]) -> str:
        """Fingerprint a signal for deduplication."""
        return f"{sig.get('pid')}:{sig.get('type')}:{sig.get('timestamp', '')}"

    # ------------------------------------------------------------------
    # Async iteration
    # ------------------------------------------------------------------

    def __aiter__(self):
        return self

    async def __anext__(self) -> Event:
        if not self._running and self._queue.empty():
            raise StopAsyncIteration
        event = await self.get_next_event()
        if event.type == EventType.SHUTDOWN:
            raise StopAsyncIteration
        return event
