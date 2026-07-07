"""Tests for GoalManager.archive_all() (Project-JARVIS#146).

Real file I/O via tmp_path -- archive_all() must actually persist
in-flight goals to disk, not just claim to, since the whole point is
that a daemon shutdown doesn't silently lose them.
"""

import json

import pytest

from jarvis.dispatch.goal_manager import GoalManager, GoalStatus


@pytest.mark.unit
class TestArchiveAll:
    def test_archives_goals_of_every_status(self, tmp_path):
        gm = GoalManager(archive_dir=str(tmp_path))
        pending = gm.add_goal("pending work")
        active = gm.add_goal("active work")
        gm.link_tasks(active.id, [123])
        deferred = gm.add_goal("deferred work")
        gm.defer_goal(deferred.id, timer_pid=456)
        completed = gm.add_goal("done work")
        gm.complete_goal(completed.id, output="finished")

        archived = gm.archive_all()

        assert {g.id for g in archived} == {
            pending.id,
            active.id,
            deferred.id,
            completed.id,
        }
        assert gm.get_all_goals() == []  # in-memory state cleared

        archive_file = tmp_path / "goal_archive.jsonl"
        lines = archive_file.read_text().splitlines()
        assert len(lines) == 4
        statuses = {json.loads(line)["status"] for line in lines}
        assert statuses == {"pending", "active", "deferred", "completed"}

    def test_empty_goal_manager_writes_nothing(self, tmp_path):
        gm = GoalManager(archive_dir=str(tmp_path))
        assert gm.archive_all() == []
        assert not (tmp_path / "goal_archive.jsonl").exists()

    def test_archived_goal_preserves_description_and_status(self, tmp_path):
        gm = GoalManager(archive_dir=str(tmp_path))
        goal = gm.add_goal("do the thing")

        gm.archive_all()

        entries = gm.load_archive()
        assert len(entries) == 1
        assert entries[0]["id"] == goal.id
        assert entries[0]["description"] == "do the thing"
        assert entries[0]["status"] == GoalStatus.PENDING.value
