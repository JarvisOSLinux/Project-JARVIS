"""Resolved confirmations are kept for review (#235), and each request records
the chat session it was made in.
"""

import json
from unittest.mock import Mock

import pytest

from jarvis.core import confirmation_manager as cm
from jarvis.core.confirmation_manager import (
    HISTORY_FILENAME,
    HISTORY_LIMIT,
    ConfirmationManager,
    load_history,
)


def _details(n):
    return [
        {"tool_name": f"shell_{i}", "params": {"command": f"cmd{i}"}, "task": {"id": i}}
        for i in range(n)
    ]


async def _request(mgr, request_id="r1", n=2, chat_session_id="chat-1"):
    details = _details(n)
    await mgr.request_confirmation(
        request_id=request_id,
        tasks=[d["task"] for d in details],
        tools_needing_confirmation=details,
        approved_tasks=[],
        denied_tools=[],
        chat_session_id=chat_session_id,
    )


@pytest.mark.unit
class TestOutcomes:
    @pytest.mark.asyncio
    async def test_approve_records_approved(self):
        mgr = ConfirmationManager()
        await _request(mgr, n=2)
        mgr.resolve({"id": "r1", "approved": True})
        (entry,) = mgr.list_history()
        assert entry["outcome"] == "approved"
        assert (entry["approved"], entry["total"]) == (2, 2)
        assert entry["chat_session_id"] == "chat-1"
        assert len(entry["tool_lines"]) == 2
        assert entry["resolved_at"] >= entry["created_at"]

    @pytest.mark.asyncio
    async def test_deny_records_denied(self):
        mgr = ConfirmationManager()
        await _request(mgr, n=2)
        mgr.resolve({"id": "r1", "approved": False})
        (entry,) = mgr.list_history()
        assert (entry["outcome"], entry["approved"]) == ("denied", 0)

    @pytest.mark.asyncio
    async def test_a_subset_records_partial(self):
        mgr = ConfirmationManager()
        await _request(mgr, n=3)
        mgr.resolve({"id": "r1", "approved_indices": [0, 2]})
        (entry,) = mgr.list_history()
        assert (entry["outcome"], entry["approved"], entry["total"]) == (
            "partial",
            2,
            3,
        )

    @pytest.mark.asyncio
    async def test_an_empty_or_full_subset_is_denied_or_approved(self):
        mgr = ConfirmationManager()
        await _request(mgr, "a", n=2)
        await _request(mgr, "b", n=2)
        mgr.resolve({"id": "a", "approved_indices": []})
        mgr.resolve({"id": "b", "approved_indices": [0, 1]})
        outcomes = {e["id"]: e["outcome"] for e in mgr.list_history()}
        assert outcomes == {"a": "denied", "b": "approved"}

    @pytest.mark.asyncio
    async def test_an_unknown_id_records_nothing(self):
        mgr = ConfirmationManager()
        mgr.resolve({"id": "ghost", "approved": True})
        assert mgr.list_history() == []

    @pytest.mark.asyncio
    async def test_newest_first(self):
        mgr = ConfirmationManager()
        await _request(mgr, "a")
        await _request(mgr, "b")
        mgr.resolve({"id": "a", "approved": True})
        mgr.resolve({"id": "b", "approved": False})
        assert [e["id"] for e in mgr.list_history()] == ["b", "a"]

    @pytest.mark.asyncio
    async def test_the_pending_summary_carries_the_chat_session(self):
        mgr = ConfirmationManager()
        await _request(mgr, chat_session_id="chat-9")
        assert mgr.list_pending()[0]["chat_session_id"] == "chat-9"


@pytest.mark.unit
class TestPersistence:
    @pytest.mark.asyncio
    async def test_history_survives_a_restart(self, tmp_path):
        store = tmp_path / "confirmations.json"
        mgr = ConfirmationManager(store_path=str(store))
        await _request(mgr, n=1)
        mgr.resolve({"id": "r1", "approved": True})

        again = ConfirmationManager(store_path=str(store))
        assert [e["id"] for e in again.list_history()] == ["r1"]
        assert (tmp_path / HISTORY_FILENAME).exists()

    @pytest.mark.asyncio
    async def test_the_chat_session_survives_a_restart_of_a_pending_entry(
        self, tmp_path
    ):
        store = tmp_path / "confirmations.json"
        await _request(
            ConfirmationManager(store_path=str(store)), chat_session_id="chat-3"
        )
        again = ConfirmationManager(store_path=str(store))
        assert again.list_pending()[0]["chat_session_id"] == "chat-3"

    @pytest.mark.asyncio
    async def test_an_old_store_without_the_field_still_loads(self, tmp_path):
        store = tmp_path / "confirmations.json"
        mgr = ConfirmationManager(store_path=str(store))
        await _request(mgr)
        records = json.loads(store.read_text())
        for r in records:
            r.pop("chat_session_id")
        store.write_text(json.dumps(records))
        again = ConfirmationManager(store_path=str(store))
        assert again.list_pending()[0]["chat_session_id"] is None

    @pytest.mark.asyncio
    async def test_history_is_capped(self, tmp_path):
        mgr = ConfirmationManager(store_path=str(tmp_path / "c.json"))
        for i in range(HISTORY_LIMIT + 5):
            await _request(mgr, f"r{i}", n=1)
            mgr.resolve({"id": f"r{i}", "approved": True})
        history = mgr.list_history()
        assert len(history) == HISTORY_LIMIT
        assert history[0]["id"] == f"r{HISTORY_LIMIT + 4}"
        assert len(load_history(tmp_path / HISTORY_FILENAME)) == HISTORY_LIMIT

    @pytest.mark.asyncio
    async def test_a_memory_only_manager_touches_no_disk(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = ConfirmationManager()
        await _request(mgr, n=1)
        mgr.resolve({"id": "r1", "approved": True})
        assert len(mgr.list_history()) == 1
        assert list(tmp_path.iterdir()) == []

    def test_a_missing_or_corrupt_history_file_is_empty_not_an_error(self, tmp_path):
        assert load_history(tmp_path / "nope.json") == []
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert load_history(bad) == []
        bad.write_text(json.dumps({"a": 1}))
        assert load_history(bad) == []

    @pytest.mark.asyncio
    async def test_a_history_write_failure_never_blocks_resolving(
        self, tmp_path, monkeypatch
    ):
        mgr = ConfirmationManager(store_path=str(tmp_path / "c.json"))
        await _request(mgr, n=1)

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(cm, "_atomic_write_json", boom)
        pending = mgr.resolve({"id": "r1", "approved": True})
        assert pending is not None
        assert len(mgr.list_history()) == 1
