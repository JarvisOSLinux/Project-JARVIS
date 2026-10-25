"""Reading a running job: once per reminder, then wait (2026-10-23).

"Can you run the system update without the noconfirm?" worked: pacman's
"[Y/n]" reached the model in a REMIND and it answered with send_input. But the
model also kept calling read_output on the running job; each read's EXIT woke
it, so it read again, about fifteen times, and each read of the root shell
server started it again under pkexec, one password prompt per read. Yakup:
after reading, it should go idle or say "the updates are running, please wait".

dispatch already holds a running job's output (run_job streams it; REMIND
carries it), so a read of a running job is answered from there, once per
reminder; a read with no news since the last one makes JARVIS tell the user
the job is running (once) and wait, without asking the model again.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.runtime import dispatch_flow

SERVER = "com.x.jarvis-shell-system-mcp"
N = "ab12"


def _app():
    app = SimpleNamespace(
        dispatch=SimpleNamespace(
            is_connected=True,
            get_task_status=AsyncMock(
                return_value=[
                    {
                        "pid": 7,
                        "state": "running",
                        "tail": f"[hash={N}] <{N}>(2/41) upgrading libuv</{N}>",
                        "tail_hash": N,
                    }
                ]
            ),
            send_tasks=AsyncMock(),
        ),
        goals=SimpleNamespace(get_active_goals=lambda: [], record_dispatch=Mock()),
        output_manager=Mock(),
        _act_on_root_response=AsyncMock(),
        _signal_ledger=(set(), {}),
    )
    dispatch_flow.note_dispatched_tools(
        app,
        [7],
        [
            {
                "server": SERVER,
                "tool": "run_job",
                "params": {"command": "pacman -Syu", "job": "system-update"},
            }
        ],
    )
    return app


READ = {"server": SERVER, "tool": "read_output", "params": {"job": "system-update"}}


def _read(app, tasks=None):
    ask = AsyncMock(return_value={"action": "respond", "output": "Running."})
    with (
        patch.object(dispatch_flow, "ask_llm", ask),
        patch.object(dispatch_flow, "build_root_context", return_value=""),
        patch.object(dispatch_flow, "emit_activity") as activity,
        patch.object(dispatch_flow, "persist_assistant_turn"),
        patch.object(
            dispatch_flow, "dispatch_send", AsyncMock(return_value={})
        ) as send,
    ):
        asyncio.run(
            dispatch_flow.dispatch_execute_tasks(
                app, logging.getLogger("t"), tasks or [dict(READ)], 0
            )
        )
    return ask, send, activity


def _told(app):
    return [
        c.args[0]["output"] for c in app.output_manager.handle_response.call_args_list
    ]


@pytest.mark.unit
class TestRunningJobReads:
    def test_the_first_read_comes_from_dispatch_not_the_server(self):
        app = _app()
        ask, send, _ = _read(app)
        send.assert_not_awaited()
        context = ask.await_args.args[2]
        assert "JOB_OUTPUT: system-update still running" in context
        assert "(2/41) upgrading libuv" in context
        assert "do not read or start them again now" in context

    def test_reading_again_before_any_news_waits_and_says_so_once(self):
        app = _app()
        _read(app)
        ask, send, _ = _read(app)
        ask.assert_not_awaited()
        send.assert_not_awaited()
        assert _told(app) == [
            "system-update is still running. I'll tell you when it needs you or "
            "when it's done."
        ]
        ask, _, activity = _read(app)
        ask.assert_not_awaited()
        assert len(_told(app)) == 1
        activity.assert_called_once()

    def test_a_reminder_makes_the_next_read_worth_answering(self):
        app = _app()
        _read(app)
        app._signal_ledger[1][7] = object()  # a REMIND arrived
        ask, _, _ = _read(app)
        ask.assert_awaited_once()

    def test_a_finished_job_is_read_from_the_server(self):
        app = _app()
        app._signal_ledger[0].add(7)
        ask, send, _ = _read(app)
        send.assert_awaited_once()
        ask.assert_not_awaited()

    def test_an_unknown_job_or_another_server_is_read_from_the_server(self):
        for task in (
            {**READ, "params": {"job": "other"}},
            {**READ, "server": "elsewhere"},
        ):
            _, send, _ = _read(_app(), [task])
            send.assert_awaited_once()

    def test_starting_a_running_job_again_reads_it_instead(self):
        app = _app()
        again = {
            "server": SERVER,
            "tool": "run_job",
            "params": {"command": "pacman -Syu", "job": "system-update"},
        }
        ask, send, _ = _read(app, [again])
        send.assert_not_awaited()
        assert "do not read or start them again now" in ask.await_args.args[2]

    def test_a_finished_job_can_be_started_again(self):
        app = _app()
        app._signal_ledger[0].add(7)
        again = {
            "server": SERVER,
            "tool": "run_job",
            "params": {"command": "pacman -Syu", "job": "system-update"},
        }
        _, send, _ = _read(app, [again])
        send.assert_awaited_once()

    def test_a_read_beside_the_jobs_own_start_is_dropped(self):
        app = _app()
        start = {
            "server": SERVER,
            "tool": "run_job",
            "params": {"command": "pacman -Syu", "job": "new"},
        }
        tasks = [start, {**READ, "params": {"job": "new"}}]
        _, send, _ = _read(app, tasks)
        (sent,) = send.await_args.args[2]
        assert sent is start


@pytest.mark.unit
def test_status_polls_are_not_logged_at_info(caplog):
    from jarvis.dispatch import transport

    adapter = SimpleNamespace(
        timeout=5, session=SimpleNamespace(call_tool=AsyncMock(return_value={}))
    )
    caplog.set_level(logging.DEBUG, logger="t")

    async def run():
        for op in ("get_task_status", "send_tasks"):
            await transport.call_tool(
                adapter,
                logging.getLogger("t"),
                tool_name="x",
                params={},
                op_name=op,
                timeout_error="",
                failure_prefix="",
                extractor=lambda r: {},
            )

    asyncio.run(run())
    levels = {r.getMessage().split()[1]: r.levelname for r in caplog.records}
    assert levels == {"get_task_status": "DEBUG", "send_tasks": "INFO"}
