"""The poll fallback reads dispatch's signal window (#26).

Signals arrive by push. The poll catches up on what no push delivered: a KILL,
which dispatch never pushes, and a push that was lost. It read nothing until
2026-10-25: dispatch's `log` tool put its JSON in a key outside the MCP
tool-result schema, the SDK dropped it, and the text left could not be used.
dispatch now returns the entries in `structuredContent`.
"""

import asyncio
import logging

import pytest
from mcp import types

from jarvis.dispatch.adapter import DispatchAdapter
from jarvis.dispatch.event_merger import EventMerger, EventType
from jarvis.dispatch.transport import _normalize_pushed_signal, get_signal_window

LOG = logging.getLogger("t")

_TEXT = {"type": "text", "text": "Signal window (last 3):\n[05:02:59] PID 1 INIT …"}


def _raw(pid, kind, ts, message="", nonce=None, held=False):
    """A SignalEntry as dispatch serializes it, in a push and in the log alike."""
    raw = {"timestamp": ts, "pid": pid, "kind": kind, "message": message}
    if nonce:
        raw["nonce"] = nonce
    if held:
        raw["held"] = True
    return raw


_EXIT = _raw(
    1,
    "EXIT",
    "2026-10-25T05:03:04.512730918+03:00",
    "[hash=a3f2] 200 <a3f2>done</a3f2>",
    nonce="a3f2",
)
_KILL = _raw(2, "KILL", "2026-10-25T05:03:09.001+03:00", "Terminated by LLM")
_HELD = _raw(
    3, "EXIT", "2026-10-25T05:03:10.2+03:00", "[hash=b4] 200 <b4>x</b4>", "b4", True
)


class _Session:
    def __init__(self, wire):
        # Validated from the wire JSON, as the SDK does with dispatch's reply.
        self.result = types.CallToolResult.model_validate(wire)

    async def call_tool(self, name, params):
        assert name == "log"
        return self.result


def _adapter(wire):
    adapter = DispatchAdapter()
    adapter._connected = True
    adapter.session = _Session(wire)
    return adapter


def _read(adapter):
    return asyncio.run(get_signal_window(adapter, LOG))


@pytest.mark.unit
class TestReadingTheWindow:
    def test_entries_read_as_pushed_signals(self):
        wire = {
            "content": [_TEXT],
            "structuredContent": {"entries": [_EXIT, _KILL, _HELD]},
        }
        exit_, kill, held = _read(_adapter(wire))
        assert exit_ == _normalize_pushed_signal(_EXIT)
        assert exit_["nonce"] == "a3f2"
        assert kill["type"] == "KILL" and kill["data"] == "Terminated by LLM"
        assert held["held"] is True and "held" not in exit_

    def test_a_signal_has_one_key_pushed_or_polled(self):
        wire = {"content": [_TEXT], "structuredContent": {"entries": [_EXIT]}}
        (polled,) = _read(_adapter(wire))
        pushed = _normalize_pushed_signal(dict(_EXIT))
        assert EventMerger._signal_key(polled) == EventMerger._signal_key(pushed)

    def test_the_old_reply_reads_nothing_and_says_so_once(self, caplog):
        # dispatch before the fix: the JSON beside content, dropped by the SDK.
        adapter = _adapter({"content": [_TEXT], "entries": [_EXIT]})
        with caplog.at_level(logging.WARNING):
            assert _read(adapter) == []
            assert _read(adapter) == []
        warned = [r for r in caplog.records if "no structured entries" in r.message]
        assert len(warned) == 1


def _polled(raw):
    """What get_signal_window hands the merger for one entry."""
    sig = _normalize_pushed_signal(raw)
    if raw.get("held"):
        sig["held"] = True
    return sig


def _merger(first=()):
    merger = EventMerger()
    merger._catch_up([_polled(r) for r in first])
    return merger


def _poll(merger, *raws):
    merger._catch_up([_polled(r) for r in raws])


def _queued(merger):
    out = []
    while not merger._queue.empty():
        out.append(merger._queue.get_nowait())
    return out


@pytest.mark.unit
class TestCatchingUp:
    def test_a_kill_is_delivered_once_the_second_poll_still_finds_it(self):
        merger = _merger()
        _poll(merger, _KILL)
        assert _queued(merger) == []
        _poll(merger, _KILL)
        (event,) = _queued(merger)
        assert event.data["type"] == "KILL" and event.data["pid"] == 2
        _poll(merger, _KILL)
        assert _queued(merger) == []

    def test_a_push_between_two_polls_is_not_delivered_again(self):
        merger = _merger()
        _poll(merger, _EXIT)
        merger.enqueue_pushed_signal(_normalize_pushed_signal(dict(_EXIT)))
        _poll(merger, _EXIT)
        _poll(merger, _EXIT)
        assert [e.data["type"] for e in _queued(merger)] == ["EXIT"]

    def test_a_lost_push_is_caught_up(self):
        merger = _merger()
        _poll(merger, _EXIT)
        _poll(merger, _EXIT)
        (event,) = _queued(merger)
        assert event.data["pid"] == 1
        assert event.data["_boundary_verified"] is True

    def test_signals_there_at_the_first_poll_are_not_replayed(self):
        merger = _merger(first=[_EXIT, _KILL])
        _poll(merger, _EXIT, _KILL)
        _poll(merger, _EXIT, _KILL)
        assert _queued(merger) == []


_HELD_2 = _raw(
    4, "EXIT", "2026-10-25T05:03:12+03:00", "[hash=c5] 200 <c5>y</c5>", "c5", True
)


def _released(raw):
    return {k: v for k, v in raw.items() if k != "held"}


@pytest.mark.unit
class TestHeldForABatch:
    """A fire_wake=false EXIT is in the window before its batch is pushed."""

    def test_it_waits_for_the_batch_and_comes_with_it(self):
        merger = _merger()
        for _ in range(3):
            _poll(merger, _HELD)
        assert _queued(merger) == []
        merger.enqueue_pushed_batch(
            [_normalize_pushed_signal(_released(r)) for r in (_HELD, _HELD_2)]
        )
        (event,) = _queued(merger)
        assert event.type == EventType.DISPATCH_SIGNAL_BATCH
        assert [s["pid"] for s in event.data] == [3, 4]

    def test_held_at_the_first_poll_still_comes_with_its_batch(self):
        merger = _merger(first=[_HELD])
        merger.enqueue_pushed_batch([_normalize_pushed_signal(_released(_HELD))])
        (event,) = _queued(merger)
        assert event.type == EventType.DISPATCH_SIGNAL_BATCH

    def test_a_lost_batch_push_is_caught_up(self):
        merger = _merger()
        _poll(merger, _HELD, _HELD_2)
        _poll(merger, _released(_HELD), _released(_HELD_2))
        assert _queued(merger) == []
        _poll(merger, _released(_HELD), _released(_HELD_2))
        assert sorted(e.data["pid"] for e in _queued(merger)) == [3, 4]

    def test_a_reminder_does_not_carry_its_held_exit(self):
        remind = _raw(3, "REMIND", "2026-10-25T05:03:01+03:00", "Running for 30s")
        merger = _merger()
        _poll(merger, remind, _HELD)
        _poll(merger, remind, _HELD)
        (event,) = _queued(merger)
        assert event.data["type"] == "REMIND"
        assert "_exit" not in event.data


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
