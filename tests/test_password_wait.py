import pytest

from jarvis.runtime import password_wait
from jarvis.runtime.password_wait import GRANTED, WAITING, awaiting_password
from jarvis.runtime.root_actions import _split_task_tails


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
