"""
EventMerger — async dual-input listener.

Merges multiple input sources into a single event stream:
1. User input (voice, stdin, socket, or injected)
2. Dispatch signals (task completions, reminders, etc.)

Dispatch signals arrive by two paths that share one ingest (_ingest_one):
- PUSH (primary, #26): dispatch emits a notifications/message the moment a
  task completes or a reminder fires; the MCP session's logging handler calls
  enqueue_pushed_signal, so ROOT is woken on completion without polling.
- POLL (fallback): a background asyncio Task polls the dispatch signal window
  as a catch-up net. Each signal is fingerprinted by (pid, type, timestamp)
  and tracked in a seen-set; duplicates are silently dropped, so a signal
  observed by both push and poll is delivered exactly once.

On the first poll, all already-present signals are marked seen without being
enqueued — this prevents stale signals from a previous JARVIS session being
replayed on startup.

Signal type filtering: INIT and WAIT are never delivered via the async queue.
INIT is always captured inline by the send_tasks result; delivering it again
via EventMerger would cause redundant ROOT processing. WAIT is an internal
acknowledgement signal emitted by the dispatch 'wait' tool.

REMIND+EXIT merging: if a REMIND for PID X is first-seen and an EXIT for the
same PID is already present in the same window snapshot, the REMIND is
enriched with _remind_completed=True / _exit=<exit signal> so ROOT gets the
full output in a single LLM turn instead of two.

Double-delivery prevention: when the dispatch sub-chain calls wait_task()
and receives EXIT signals inline, it marks those signals via
mark_signals_seen() so the background poller does not re-enqueue them.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..core.logger import get_logger
from .boundary import verify_and_mark

logger = get_logger(__name__)

# Signal types that the sub-chain always handles inline; never enqueue these.
_INLINE_SIGNAL_TYPES = frozenset({"INIT", "WAIT"})


class EventType(Enum):
    USER_INPUT = "user_input"
    DISPATCH_SIGNAL = "dispatch_signal"
    # A merged fire_wake=false group delivered by dispatch as one batch, handled
    # as a single ROOT turn (#189). data is a list of signal dicts.
    DISPATCH_SIGNAL_BATCH = "dispatch_signal_batch"
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
    def dispatch_signals(signals: List[Dict[str, Any]]) -> "Event":
        return Event(type=EventType.DISPATCH_SIGNAL_BATCH, data=signals)

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

    def call_soon_threadsafe(self, callback: Callable[[], None]) -> None:
        """Schedule a plain callback on the event loop from any thread.

        For voice/socket threads that need to trigger async work (e.g.
        broadcasting a GUI state change) without going through the merged
        event queue.
        """
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(callback)

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

    def mark_signals_seen(self, signals: List[Dict[str, Any]]) -> None:
        """
        Mark a list of signals as already-processed.

        Call this after receiving EXIT/TIMEOUT signals inline (e.g. from
        wait_task) to prevent the background polling loop from re-enqueueing
        the same signals as new events.
        """
        for sig in signals:
            self._seen.add(self._signal_key(sig))
        if signals:
            logger.debug(
                f"EventMerger: Marked {len(signals)} signal(s) as seen (inline handling)"
            )

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

        INIT and WAIT signals are never delivered — INIT is always captured
        inline by the send_tasks result; WAIT is an internal acknowledgement.

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
                        # REMIND+EXIT merging needs the batch snapshot, so it
                        # happens here (the push path sees signals one at a time
                        # and can't merge). Only enrich a first-seen REMIND whose
                        # task has already exited, so ROOT gets the full picture
                        # in one LLM turn.
                        if (
                            sig.get("type") == "REMIND"
                            and sig.get("pid") in exits_by_pid
                            and self._signal_key(sig) not in self._seen
                        ):
                            pid = sig.get("pid")
                            verify_and_mark(exits_by_pid[pid])
                            sig = {
                                **sig,
                                "_remind_completed": True,
                                "_exit": exits_by_pid[pid],
                            }
                            logger.info(
                                f"EventMerger: REMIND+EXIT merged for pid={pid}"
                            )

                        self._ingest_one(sig)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"EventMerger: Signal reader error: {e}")

            await asyncio.sleep(self._poll_interval)

    def _prepare_signal(self, sig: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Dedup, INLINE-filter, and boundary-verify one signal.

        Returns the signal to deliver, or None if it should be dropped. Signals
        are keyed by (pid, type, timestamp); one already in the seen-set is
        dropped, so a signal observed by both push and poll is delivered exactly
        once. INIT/WAIT are handled inline elsewhere and never enqueued.
        EXIT/TIMEOUT bodies are checked against dispatch's output-provenance
        boundary (#165) and flagged in-band if unverified.
        """
        key = self._signal_key(sig)
        if key in self._seen:
            return None
        self._seen.add(key)

        if sig.get("type") in _INLINE_SIGNAL_TYPES:
            return None

        if sig.get("type") in ("EXIT", "TIMEOUT") and verify_and_mark(sig):
            logger.warning(
                "EventMerger: output-provenance boundary FAILED for "
                f"pid={sig.get('pid')} ({sig.get('_boundary_reason')}) "
                "— marked UNVERIFIED"
            )
        return sig

    def _ingest_one(self, sig: Dict[str, Any]) -> bool:
        """Prepare and enqueue a single signal as one event. Shared by the poll
        loop and the single-signal push path. Returns True if enqueued.
        """
        prepared = self._prepare_signal(sig)
        if prepared is None:
            return False
        try:
            self._queue.put_nowait(Event.dispatch_signal(prepared))
            logger.debug(
                f"EventMerger: Queued signal "
                f"type={prepared.get('type')}, pid={prepared.get('pid')}"
            )
            return True
        except asyncio.QueueFull:
            logger.warning(
                f"EventMerger: Queue full, dropping signal "
                f"type={prepared.get('type')}, pid={prepared.get('pid')}"
            )
            return False

    def enqueue_pushed(self, payload: Any) -> None:
        """Sink for signals PUSHED by dispatch (notifications/message).

        A dict is a single ``fire_wake=true`` signal → one event → one ROOT turn.
        A list is a merged ``fire_wake=false`` batch dispatch already grouped →
        one event → one ROOT turn (#189). ``fire_wake`` is the only thing that
        decides merging; the daemon never coalesces on its own.
        """
        if isinstance(payload, list):
            self.enqueue_pushed_batch(payload)
        elif isinstance(payload, dict) and payload:
            self.enqueue_pushed_signal(payload)

    def enqueue_pushed_signal(self, sig: Dict[str, Any]) -> None:
        """Ingest a single pushed dispatch signal (#26).

        Runs on this event loop (called from the MCP session's logging handler),
        so it goes through the same dedup/boundary/enqueue path as the poll loop.
        Push and poll share the seen-set, so whichever observes a given
        (pid, type, timestamp) first wins and the other is a no-op.
        """
        if not sig:
            return
        self._ingest_one(sig)

    def enqueue_pushed_batch(self, signals: List[Dict[str, Any]]) -> None:
        """Ingest a merged ``fire_wake=false`` group as ONE event (#189).

        Each signal is still deduped and boundary-verified individually, but the
        survivors ride as a single ``DISPATCH_SIGNAL_BATCH`` event so ROOT sees
        the whole group's outcomes and answers once, instead of one turn per
        signal.
        """
        prepared: List[Dict[str, Any]] = []
        for sig in signals:
            if not sig:
                continue
            ready = self._prepare_signal(sig)
            if ready is not None:
                prepared.append(ready)
        if not prepared:
            return
        try:
            self._queue.put_nowait(Event.dispatch_signals(prepared))
            logger.debug(
                f"EventMerger: Queued batch of {len(prepared)} signal(s) "
                f"pids={[s.get('pid') for s in prepared]}"
            )
        except asyncio.QueueFull:
            logger.warning(
                f"EventMerger: Queue full, dropping batch of {len(prepared)} signal(s)"
            )

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
