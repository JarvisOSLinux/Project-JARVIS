"""A finished task's result reaches ROOT once.

On the reference machine (2026-10-21), "run the updates" got the same summary
twice, 16 seconds apart. The long pacman task had fired a reminder; when the
signal poll saw that reminder for the first time beside the task's EXIT, it
queued a merged REMIND+EXIT event, but left the EXIT unseen, so the same
snapshot queued the EXIT again as its own event: two ROOT turns, one result.
"""

import asyncio

import pytest

from jarvis.dispatch.event_merger import EventMerger

REMIND = {"pid": 4, "type": "REMIND", "timestamp": "t1", "data": "still running"}
EXIT = {"pid": 4, "type": "EXIT", "timestamp": "t2", "data": "[hash=h] 200 <h>done</h>"}


def _events(windows, before=None):
    """Run the poll loop over successive window snapshots; return queued events."""

    async def run():
        merger = EventMerger(poll_interval=0.001)
        merger._running = True
        snapshots = [[]] + windows  # the first poll only seeds the seen-set
        calls = {"n": 0}

        async def window():
            i = min(calls["n"], len(snapshots) - 1)
            calls["n"] += 1
            if calls["n"] > len(snapshots) + 2:
                merger._running = False
            return snapshots[i]

        if before:
            before(merger)
        await merger._listen_signals(window)
        out = []
        while not merger._queue.empty():
            out.append(merger._queue.get_nowait())
        return out

    return asyncio.run(run())


def _signals(events):
    return [e.data for e in events]


@pytest.mark.unit
class TestMergedReminder:
    def test_a_reminder_beside_its_exit_is_one_event_carrying_the_exit(self):
        (only,) = _signals(_events([[REMIND, EXIT]]))
        assert only["type"] == "REMIND" and only["_remind_completed"] is True
        assert only["_exit"]["type"] == "EXIT"

    def test_the_exit_listed_first_still_makes_one_event(self):
        assert len(_events([[EXIT, REMIND]])) == 1

    def test_a_reminder_after_its_exit_was_delivered_adds_nothing(self):
        # The EXIT was pushed and delivered before this poll saw the reminder.
        def pushed(merger):
            merger._signals_initialized = True
            merger.enqueue_pushed_signal(dict(EXIT))

        events = _events([[REMIND, EXIT]], before=pushed)
        assert [s["type"] for s in _signals(events)] == ["EXIT"]

    def test_later_polls_of_the_same_window_add_nothing(self):
        assert len(_events([[REMIND, EXIT], [REMIND, EXIT], [REMIND, EXIT]])) == 1

    def test_a_plain_exit_is_still_delivered(self):
        assert [s["type"] for s in _signals(_events([[EXIT]]))] == ["EXIT"]
