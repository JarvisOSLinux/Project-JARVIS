"""Resolved-confirmation history ring (#235).

``resolve()`` is the single point an entry leaves ``_pending``, so it is also
the single point a history record is written. These tests pin that every
resolution shape lands, that the ring stays bounded, and that a memory-only
manager still touches no filesystem at all.
"""

import json

import pytest

from jarvis.core.confirmation_manager import (
    HISTORY_FILENAME,
    HISTORY_LIMIT,
    ConfirmationManager,
    load_history,
)


def _manager(tmp_path):
    return ConfirmationManager(store_path=str(tmp_path / "confirmations.json"))


def _add(manager, request_id, count=2, chat_session_id=None):
    """Seed a pending entry directly — request_confirmation() would also fire
    notification channels, which is not what these tests are about."""
    from jarvis.core.confirmation_manager import PendingConfirmation

    details = [
        {"tool_name": f"srv.tool{i}", "task": {"server": "srv", "tool": f"tool{i}"}}
        for i in range(count)
    ]
    manager._pending[request_id] = PendingConfirmation(
        request_id=request_id,
        tasks=[d["task"] for d in details],
        confirm_details=details,
        tool_names=[d["tool_name"] for d in details],
        tool_lines=[f"{d['tool_name']}: run" for d in details],
        chat_session_id=chat_session_id,
    )
    manager._persist()


@pytest.mark.unit
class TestHistoryRing:
    def test_approve_is_recorded(self, tmp_path):
        m = _manager(tmp_path)
        _add(m, "aaa")
        m.resolve({"id": "aaa", "approved": True})

        history = m.list_history()
        assert [r["id"] for r in history] == ["aaa"]
        assert history[0]["outcome"] == "approved"
        assert history[0]["resolved_at"] > 0
        assert history[0]["tool_names"] == ["srv.tool0", "srv.tool1"]

    def test_deny_is_recorded(self, tmp_path):
        m = _manager(tmp_path)
        _add(m, "bbb")
        m.resolve({"id": "bbb", "approved": False})
        assert m.list_history()[0]["outcome"] == "denied"

    def test_partial_records_the_subset(self, tmp_path):
        m = _manager(tmp_path)
        _add(m, "ccc", count=3)
        m.resolve({"id": "ccc", "approved_indices": [0, 2]})

        record = m.list_history()[0]
        assert record["outcome"] == "partial"
        assert record["approved_indices"] == [0, 2]

    def test_approved_indices_covering_everything_is_not_partial(self, tmp_path):
        """A subset that happens to be the whole set reads as a plain approve."""
        m = _manager(tmp_path)
        _add(m, "ddd", count=2)
        m.resolve({"id": "ddd", "approved_indices": [0, 1]})
        assert m.list_history()[0]["outcome"] == "approved"

    def test_empty_approved_indices_is_denied(self, tmp_path):
        m = _manager(tmp_path)
        _add(m, "eee", count=2)
        m.resolve({"id": "eee", "approved_indices": []})
        assert m.list_history()[0]["outcome"] == "denied"

    def test_newest_first(self, tmp_path):
        m = _manager(tmp_path)
        for rid in ("one", "two", "three"):
            _add(m, rid)
            m.resolve({"id": rid, "approved": True})
        assert [r["id"] for r in m.list_history()] == ["three", "two", "one"]

    def test_ring_is_bounded(self, tmp_path):
        m = _manager(tmp_path)
        for i in range(HISTORY_LIMIT + 25):
            _add(m, f"id{i}", count=1)
            m.resolve({"id": f"id{i}", "approved": True})

        history = m.list_history()
        assert len(history) == HISTORY_LIMIT
        # The oldest fell off the end, the newest is on top.
        assert history[0]["id"] == f"id{HISTORY_LIMIT + 24}"
        assert not any(r["id"] == "id0" for r in history)

    def test_unknown_id_writes_nothing(self, tmp_path):
        m = _manager(tmp_path)
        assert m.resolve({"id": "nope", "approved": True}) is None
        assert m.list_history() == []
        assert not (tmp_path / HISTORY_FILENAME).exists()

    def test_history_sits_beside_the_store(self, tmp_path):
        m = _manager(tmp_path)
        _add(m, "fff")
        m.resolve({"id": "fff", "approved": True})
        assert (tmp_path / HISTORY_FILENAME).exists()

    def test_chat_session_id_survives_into_history(self, tmp_path):
        m = _manager(tmp_path)
        _add(m, "ggg", chat_session_id="sess-1")
        m.resolve({"id": "ggg", "approved": True})
        assert m.list_history()[0]["chat_session_id"] == "sess-1"


@pytest.mark.unit
class TestMemoryOnlyStaysSilent:
    def test_no_history_file_and_no_records(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        m = ConfirmationManager()
        _add(m, "hhh")
        m.resolve({"id": "hhh", "approved": True})

        assert m.list_history() == []
        assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
class TestHistoryReadResilience:
    def test_corrupt_history_reads_empty_and_does_not_raise(self, tmp_path):
        path = tmp_path / HISTORY_FILENAME
        path.write_text("{ not json", encoding="utf-8")
        assert load_history(path) == []

    def test_non_list_history_reads_empty(self, tmp_path):
        path = tmp_path / HISTORY_FILENAME
        path.write_text(json.dumps({"id": "x"}), encoding="utf-8")
        assert load_history(path) == []

    def test_absent_history_reads_empty(self, tmp_path):
        assert load_history(tmp_path / "nothing.json") == []

    def test_a_corrupt_history_does_not_block_the_resolve(self, tmp_path):
        """A resolve whose history write fails must still apply the decision."""
        m = _manager(tmp_path)
        (tmp_path / HISTORY_FILENAME).write_text("garbage", encoding="utf-8")
        _add(m, "iii")

        pending = m.resolve({"id": "iii", "approved": True})
        assert pending is not None
        assert pending.approved_tasks  # the approval really was applied
        # The unreadable file is treated as an empty ring and replaced.
        assert [r["id"] for r in m.list_history()] == ["iii"]


@pytest.mark.unit
class TestRestoreCompatibility:
    def test_store_written_before_chat_session_id_still_loads(self, tmp_path):
        """#224 stores predate the #235 field; they must restore, not crash."""
        store = tmp_path / "confirmations.json"
        store.write_text(
            json.dumps(
                [
                    {
                        "request_id": "old",
                        "tasks": [],
                        "tool_names": ["srv.tool"],
                        "tool_lines": ["srv.tool: run"],
                        "created_at": 1.0,
                        "session_id": "goal-9",
                    }
                ]
            ),
            encoding="utf-8",
        )
        m = ConfirmationManager(store_path=str(store))
        pending = m.list_pending()
        assert len(pending) == 1
        assert pending[0]["chat_session_id"] is None
