"""One model turn at a time per request (#284).

Every dispatch signal was its own turn, and turns only queued for the model,
each with the context it built on arrival. Two tasks of one request that ended
together got two answers, the first saying the other was still running; and a
reminder that came in while JARVIS was answering the request answered again,
nine seconds later ("System update is in progress…", then "System update
progressing…"). A request now has one lane:

- a turn for the request (the user's message, a confirmation answered, a
  setup finished, its tasks' signals) holds the lane, and another waits;
- signals that come in while the lane is busy are answered together, in one
  turn, once it is free; while other tasks of the request still run, a
  signal waits a moment first, so tasks that end together answer once;
- a plain reminder that comes in while the request's turn runs is dropped:
  dispatch repeats reminders while the task runs, so the next one carries
  anything new, a question such as pacman's "[Y/n]" included;
- a task's result the model already read during a turn (a status check of
  the request shows finished tasks' output) is not answered again when its
  own signal's turn comes. In the app, "The system update has completed
  successfully!" came twice: the status check read pacman's result while
  its EXIT waited for the turn, and the EXIT's turn said it again.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from logging import Logger
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

# How long a signal waits for the request's other tasks before its turn.
COALESCE_SECONDS = 1.0


@dataclass
class _Lane:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: List[Any] = field(default_factory=list)
    draining: bool = False


def _lanes(app: Any) -> Dict[str, _Lane]:
    lanes = getattr(app, "_turn_lanes", None)
    if lanes is None:
        lanes = app._turn_lanes = {}
    return lanes


def _lane(app: Any, goal_id: str) -> _Lane:
    return _lanes(app).setdefault(goal_id, _Lane())


def _release(app: Any, goal_id: str, lane: _Lane) -> None:
    """Forget an idle lane, so finished requests leave nothing behind."""
    if lane.draining or lane.pending or lane.lock.locked():
        return
    if _lanes(app).get(goal_id) is lane:
        del _lanes(app)[goal_id]


def busy(app: Any, goal_id: Optional[str]) -> bool:
    """True while a turn for the request runs."""
    lane = _lanes(app).get(goal_id) if goal_id else None
    return lane is not None and lane.lock.locked()


def active(app: Any, goal_id: Optional[str]) -> bool:
    """True while the request has a turn running or signals waiting for one."""
    return bool(goal_id) and goal_id in _lanes(app)


def _seen(app: Any) -> set:
    seen = getattr(app, "_results_seen", None)
    if seen is None:
        seen = app._results_seen = set()
    return seen


def saw_results(app: Any, pids: Any) -> None:
    """The model read these tasks' results in a turn for their request."""
    _seen(app).update(p for p in pids or () if p is not None)


def result_seen(app: Any, pid: Any) -> bool:
    return pid in _seen(app)


def forget(app: Any, pids: Any) -> None:
    """A new task took these PIDs: dispatch counts from 1 again after a restart."""
    _seen(app).difference_update(pids or ())


@asynccontextmanager
async def turn(app: Any, goal_id: Optional[str]) -> AsyncIterator[None]:
    """Hold the request's lane for one turn."""
    if not goal_id:
        yield
        return
    lane = _lane(app, goal_id)
    try:
        async with lane.lock:
            yield
    finally:
        _release(app, goal_id, lane)


async def deliver(
    app: Any,
    logger: Logger,
    goal_id: str,
    items: List[Any],
    answer: Callable[[List[Any]], Awaitable[None]],
    *,
    wait: bool = False,
) -> None:
    """Answer `items` in the request's next turn.

    The caller that finds nobody draining the lane drains it: it waits
    COALESCE_SECONDS when `wait` (other tasks of the request still run), then
    answers everything pending in one turn, and again for whatever came in
    meanwhile, until nothing is left. Other callers return at once. A stop
    cancels the drain, and what was pending goes with it.
    """
    from . import stop

    lane = _lane(app, goal_id)
    lane.pending.extend(items)
    if lane.draining:
        return
    lane.draining = True
    stop.claim_turn(app, goal_id)
    try:
        if wait:
            await asyncio.sleep(COALESCE_SECONDS)
        while lane.pending:
            async with lane.lock:
                batch, lane.pending = lane.pending, []
                try:
                    await answer(batch)
                except Exception as e:  # one failed turn must not strand the rest
                    logger.error(f"JARVIS: a signal turn failed: {e}", exc_info=e)
    finally:
        lane.draining = False
        lane.pending = []
        _release(app, goal_id, lane)
