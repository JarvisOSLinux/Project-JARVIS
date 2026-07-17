"""Tests for pushed dispatch-signal delivery (#26).

Covers the daemon side of the push architecture: normalizing dispatch's raw
SignalEntry into the daemon's canonical shape, and EventMerger.enqueue_pushed_signal
sharing one dedup/ingest path with the poll loop so a signal seen by both is
delivered exactly once.
"""

import asyncio

import pytest

from jarvis.dispatch.event_merger import EventMerger, EventType
from jarvis.dispatch.transport import _normalize_pushed_signal


def _raw_exit(pid=1, nonce="abc123", ts="2026-07-13T12:00:00-07:00"):
    """A raw dispatch EXIT SignalEntry as it arrives in a pushed notification."""
    return {
        "timestamp": ts,
        "pid": pid,
        "kind": "EXIT",
        "message": f"[hash={nonce}] 200 <{nonce}>done</{nonce}>",
        "nonce": nonce,
    }


def test_normalize_maps_kind_and_message():
    raw = _raw_exit()
    norm = _normalize_pushed_signal(raw)
    assert norm["type"] == "EXIT"
    assert norm["pid"] == 1
    assert norm["data"] == raw["message"]
    assert norm["timestamp"] == raw["timestamp"]
    assert norm["nonce"] == "abc123"


def test_normalize_omits_absent_optional_fields():
    raw = {"timestamp": "t", "pid": 5, "kind": "REMIND", "message": "Running for 30s"}
    norm = _normalize_pushed_signal(raw)
    assert norm["type"] == "REMIND"
    assert "nonce" not in norm
    assert "payload" not in norm


def test_pushed_exit_is_enqueued():
    async def run():
        merger = EventMerger()
        merger.enqueue_pushed_signal(_normalize_pushed_signal(_raw_exit()))
        assert merger._queue.qsize() == 1
        event = await merger._queue.get()
        assert event.type == EventType.DISPATCH_SIGNAL
        assert event.data["type"] == "EXIT"
        assert event.data["pid"] == 1

    asyncio.run(run())


def test_pushed_signal_deduplicated_on_repeat():
    async def run():
        merger = EventMerger()
        sig = _normalize_pushed_signal(_raw_exit())
        merger.enqueue_pushed_signal(sig)
        merger.enqueue_pushed_signal(sig)  # same (pid, type, timestamp)
        assert merger._queue.qsize() == 1

    asyncio.run(run())


def test_push_and_poll_share_dedup():
    async def run():
        merger = EventMerger()
        sig = _normalize_pushed_signal(_raw_exit())
        # Push observes it first...
        merger.enqueue_pushed_signal(sig)
        # ...then the poll fallback sees the same (pid, type, timestamp): no-op.
        assert merger._ingest_one(dict(sig)) is False
        assert merger._queue.qsize() == 1

    asyncio.run(run())


def test_inline_types_not_pushed():
    async def run():
        merger = EventMerger()
        for kind in ("INIT", "WAIT"):
            raw = {"timestamp": "t", "pid": 9, "kind": kind, "message": "x"}
            merger.enqueue_pushed_signal(_normalize_pushed_signal(raw))
        assert merger._queue.qsize() == 0

    asyncio.run(run())


def test_empty_push_is_ignored():
    async def run():
        merger = EventMerger()
        merger.enqueue_pushed_signal({})
        merger.enqueue_pushed_signal(None)  # type: ignore[arg-type]
        assert merger._queue.qsize() == 0

    asyncio.run(run())


def test_unverified_exit_is_marked_in_band():
    async def run():
        merger = EventMerger()
        # Body lacks the nonce wrapper dispatch's boundary requires: the ingest
        # path must flag it untrusted rather than pass it through clean (#165).
        raw = {
            "timestamp": "t",
            "pid": 2,
            "kind": "EXIT",
            "message": "[hash=deadbeef] 200 unwrapped output",
            "nonce": "deadbeef",
        }
        merger.enqueue_pushed_signal(_normalize_pushed_signal(raw))
        event = await merger._queue.get()
        assert event.data.get("_boundary_verified") is False
        assert event.data["data"].startswith("[⚠ UNVERIFIED")

    asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
