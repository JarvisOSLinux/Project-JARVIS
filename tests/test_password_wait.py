import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.runtime import llm_bridge, password_wait, root_handlers
from jarvis.runtime.password_wait import GRANTED, WAITING, awaiting_password
from jarvis.runtime.root_actions import _split_task_tails
from jarvis.runtime.work import collect_work


def test_waiting_line_alone_means_waiting():
    assert awaiting_password(f"dmcp: {WAITING} on the computer (polkit prompt)\n")


def test_accepted_line_after_waiting_ends_the_wait():
    tail = f"dmcp: {WAITING}\ndmcp: {GRANTED}, running as root\n:: Synchronizing\n"
    assert not awaiting_password(tail)


def test_a_second_wait_after_an_accept_counts_again():
    tail = f"dmcp: {WAITING}\ndmcp: {GRANTED}\nworking\ndmcp: {WAITING}\n"
    assert awaiting_password(tail)


@pytest.mark.parametrize("tail", [None, "", "downloading packages\n", 7])
def test_no_password_line_is_not_waiting(tail):
    assert not awaiting_password(tail)


def test_notice_wording_matches_dmcp():
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "dmcp" / "src" / "elevation.rs"
    if not src.exists():
        pytest.skip("dmcp checkout not next to Project-JARVIS")
    text = src.read_text()
    assert password_wait.WAITING in text
    assert password_wait.GRANTED in text


def test_status_split_keeps_the_tail_for_the_waiting_check():
    rows, tails = _split_task_tails(
        [{"pid": 3, "state": "running", "tail": f"dmcp: {WAITING} here"}]
    )
    assert rows == [{"pid": 3, "state": "running"}]
    assert awaiting_password(tails[0][1])


# -- a sudo inside a shell task waits on JARVIS's popup, printing nothing ------


def _relay_waiting(*pids):
    return SimpleNamespace(waiting_tasks=lambda: set(pids))


def test_a_task_the_popup_waits_on_gets_one_note():
    app = SimpleNamespace(askpass=_relay_waiting(7))
    note = password_wait.popup_notes(app, [7, 8, 7])
    assert note.count("WAITING_FOR_PASSWORD") == 1
    assert "(pid 7)" in note and password_wait.WAITING_NOTE_POPUP in note
    assert password_wait.popup_notes(SimpleNamespace(), [7]) == ""
    assert password_wait.popup_notes(app, [8]) == ""


def test_the_work_list_shows_a_task_the_popup_waits_on():
    app = SimpleNamespace(
        askpass=_relay_waiting(4),
        goals=SimpleNamespace(get_active_goals=lambda: []),
        dispatch=SimpleNamespace(
            is_connected=True,
            get_task_status=AsyncMock(
                return_value=[
                    {
                        "pid": 4,
                        "type": "mcp",
                        "server": "shell",
                        "tool": "run_job",
                        "state": "running",
                        "tail": "",
                    },
                    {
                        "pid": 5,
                        "type": "mcp",
                        "server": "shell",
                        "tool": "run_job",
                        "state": "running",
                        "tail": "",
                    },
                ]
            ),
        ),
        confirmation=None,
        setups=None,
        _event_tasks=[],
    )
    work = asyncio.run(collect_work(app))
    waiting = {t["pid"]: t["awaiting_password"] for t in work["tasks"]}
    assert waiting == {4: True, 5: False}


def test_a_reminder_for_a_job_whose_sudo_waits_says_so():
    """Its output is silent while the popup asks; the model is told to point
    the user at the popup instead of reading, killing or answering the job."""
    app = SimpleNamespace(
        askpass=_relay_waiting(7),
        llm=Mock(mode="root", ask=Mock(return_value={"action": "respond"})),
        llm_lock=asyncio.Lock(),
        goals=SimpleNamespace(
            update_from_signal=Mock(), find_goal_by_task_pid=Mock(return_value=None)
        ),
        _act_on_root_response=AsyncMock(),
    )
    remind = {"type": "REMIND", "pid": 7, "data": "[hash=h] <h>==> Making package</h>"}
    with (
        patch.object(root_handlers, "build_root_context", return_value=""),
        patch.object(
            root_handlers, "_config_hint_for_signals", AsyncMock(return_value="")
        ),
        patch.object(root_handlers, "emit_activity"),
        patch.object(llm_bridge, "emit_activity"),
    ):
        asyncio.run(root_handlers.on_dispatch_signal(app, Mock(), remind))
    told = app.llm.ask.call_args.args[0]
    assert "WAITING_FOR_PASSWORD (pid 7)" in told
    assert "Never ask for the password in the chat" in told
