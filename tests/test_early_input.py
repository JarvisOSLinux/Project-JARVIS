"""Input that arrives before the engine starts is kept, not dropped.

The TUI says "Ready." while the engine is still syncing its tool index (seven
seconds on the reference machine); a message typed then was dropped with only
a log line ("inject_user_input called before start, dropping input"), so
JARVIS seemed to ignore it. Found driving the real TUI headless (#271).
"""

import asyncio

import pytest

from jarvis.dispatch.event_merger import EventMerger, EventType


async def _no_signals():
    return []


def _drain(merger):
    async def run():
        merger.start(_no_signals)
        got = []
        while len(got) < 3:
            got.append(await asyncio.wait_for(merger.get_next_event(), 1))
        await merger.stop()
        return got

    return asyncio.run(run())


@pytest.mark.unit
def test_what_came_early_is_delivered_at_start_in_order():
    merger = EventMerger()
    merger.inject_user_input("  what python do I have  ")
    merger.inject_user_input("   ")  # still dropped: nothing to say
    merger.inject_confirmation_response({"id": "r1", "approved": True})
    merger.inject_setup_result({"type": "setup_completed", "id": "s1"})
    events = _drain(merger)
    assert [(e.type, e.data) for e in events] == [
        (EventType.USER_INPUT, "what python do I have"),
        (EventType.CONFIRMATION_RESPONSE, {"id": "r1", "approved": True}),
        (EventType.SETUP_RESULT, {"type": "setup_completed", "id": "s1"}),
    ]


@pytest.mark.unit
def test_holding_is_bounded_like_the_queue():
    merger = EventMerger(max_queue_size=2)
    for text in ("one", "two", "three"):
        merger.inject_user_input(text)
    assert [e.data for e in merger._early] == ["one", "two"]
