"""What the confirmations panel shows and what its keys send (#235)."""

import pytest

from jarvis.tui.confirmations_view import (
    SCOPE_ALL,
    SCOPE_SESSION,
    build_rows,
    decision_for,
    deny_message,
    format_age,
    in_scope,
    selection_for,
)

NOW = 10_000.0


def entry(
    request_id="r1", lines=("shell: ls", "shell: rm x"), chat="chat-1", created=NOW - 90
):
    return {
        "id": request_id,
        "tool_lines": list(lines),
        "created_at": created,
        "chat_session_id": chat,
        "goal_description": "tidy up",
    }


def done(request_id="h1", outcome="approved", chat="chat-1", **kw):
    return {
        "id": request_id,
        "tool_lines": ["shell: ls"],
        "resolved_at": NOW - 30,
        "outcome": outcome,
        "approved": 1,
        "total": 1,
        "chat_session_id": chat,
        **kw,
    }


def rows(
    pending=(), history=(), scope=SCOPE_ALL, session="chat-1", sel=None, queued=()
):
    return build_rows(
        pending,
        history,
        scope=scope,
        current_session_id=session,
        selections=sel or {},
        queued=queued,
        now=NOW,
    )


@pytest.mark.unit
class TestAge:
    @pytest.mark.parametrize(
        "seconds,text",
        [
            (5, "5s ago"),
            (59, "59s ago"),
            (120, "2m ago"),
            (7300, "2h ago"),
            (200000, "2d ago"),
        ],
    )
    def test_format(self, seconds, text):
        assert format_age(NOW - seconds, NOW) == text


@pytest.mark.unit
class TestScope:
    def test_all_keeps_everything(self):
        assert in_scope(entry(chat=None), SCOPE_ALL, "chat-1")

    def test_this_session_keeps_only_matching(self):
        assert in_scope(entry(chat="chat-1"), SCOPE_SESSION, "chat-1")
        assert not in_scope(entry(chat="chat-2"), SCOPE_SESSION, "chat-1")

    def test_an_entry_with_no_session_is_never_guessed_into_one(self):
        assert not in_scope(entry(chat=None), SCOPE_SESSION, "chat-1")

    def test_no_current_session_shows_nothing_under_this_session(self):
        assert not in_scope(entry(), SCOPE_SESSION, None)


@pytest.mark.unit
class TestRows:
    def test_pending_shows_a_header_and_every_task_checked(self):
        r = rows([entry()])
        kinds = [x.kind for x in r]
        assert kinds == ["note", "pending", "task", "task", "note"]
        assert "r1  1m ago  tidy up" in r[1].text
        assert r[2].text == "  [x] shell: ls" and r[2].task_index == 0
        assert r[3].text == "  [x] shell: rm x" and r[3].task_index == 1

    def test_an_unchecked_task_shows_empty(self):
        r = rows([entry()], sel={"r1": {0}})
        assert r[2].text.startswith("  [x]")
        assert r[3].text.startswith("  [ ]")

    def test_empty_states(self):
        r = rows()
        assert [x.text for x in r] == [
            "PENDING (0)",
            "  nothing waiting for you",
            "HISTORY (0)",
        ]

    def test_history_rows_show_outcome_age_and_command(self):
        r = rows(history=[done(outcome="denied", tool_lines=["a", "b", "c"])])
        text = r[-1].text
        assert "denied" in text and "30s ago" in text and "a (+2)" in text
        assert r[-1].kind == "history"

    def test_partial_history_shows_the_ratio(self):
        r = rows(history=[done(outcome="partial", approved=2, total=3)])
        assert "partial 2/3" in r[-1].text

    def test_scope_filters_both_sections(self):
        r = rows(
            [entry("a", chat="chat-1"), entry("b", chat="chat-2")],
            [done("h1", chat="chat-1"), done("h2", chat="chat-2")],
            scope=SCOPE_SESSION,
        )
        ids = [x.request_id for x in r if x.kind in ("pending", "history")]
        assert ids == ["a", "h1"]
        assert r[0].text == "PENDING (1)"

    def test_a_queued_offline_decision_is_labelled(self):
        r = rows([entry()], queued=[{"type": "deny_confirmation", "id": "r1"}])
        assert "[queued: deny]" in r[1].text
        r = rows(
            [entry()],
            queued=[
                {
                    "type": "partial_approve_confirmation",
                    "id": "r1",
                    "approved_indices": [0],
                }
            ],
        )
        assert "[queued: approve 1]" in r[1].text

    def test_markup_characters_in_a_command_survive_as_text(self):
        r = rows([entry(lines=("echo [bold]hi[/bold]",))])
        assert "[bold]hi[/bold]" in r[2].text


@pytest.mark.unit
class TestDecisions:
    def test_everything_checked_approves_all(self):
        assert decision_for(entry(), None) == {
            "type": "approve_confirmation",
            "id": "r1",
        }
        assert decision_for(entry(), {0, 1}) == {
            "type": "approve_confirmation",
            "id": "r1",
        }

    def test_a_subset_is_a_partial_approval_by_index(self):
        assert decision_for(entry(), {1}) == {
            "type": "partial_approve_confirmation",
            "id": "r1",
            "approved_indices": [1],
        }

    def test_nothing_checked_denies(self):
        assert decision_for(entry(), set()) == {"type": "deny_confirmation", "id": "r1"}

    def test_deny_ignores_the_selection(self):
        assert deny_message(entry()) == {"type": "deny_confirmation", "id": "r1"}

    def test_stale_indices_are_dropped(self):
        assert selection_for(entry(), {0, 5, -1}) == {0}

    def test_an_entry_with_no_task_lines_approves_whole(self):
        assert decision_for(entry(lines=()), None)["type"] == "approve_confirmation"
