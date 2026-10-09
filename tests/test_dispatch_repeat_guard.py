"""#205 — per-goal dispatch repeat/progress guard.

EXIT-driven ROOT turns re-enter _act_on_root_response at depth 0, so the
synchronous MAX_CHAIN_DEPTH cap never bounds a tool that keeps returning no
usable content across dispatch cycles (observed: navigate -> about:blank looped
~8x until a provider read-timeout). This guard counts identical
(server, tool, params) dispatches within one goal and short-circuits to an
informative respond before re-sending a no-progress tool.

Also covers the secondary task_pids dedup fix (loop_body.md bottom section).
"""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from jarvis.config import Config
from jarvis.dispatch.goal_manager import GoalManager
from jarvis.runtime import dispatch_flow

_LOG = logging.getLogger("test")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _FakeDispatch:
    is_connected = True


class _OutputManager:
    def __init__(self):
        self.responses = []

    def handle_response(self, payload):
        self.responses.append(payload)

    def emit_activity(self, *a, **k):
        pass


class _App:
    def __init__(self, goals):
        self.goals = goals
        self.dispatch = _FakeDispatch()
        self.contextor = None  # persist_assistant_turn no-ops
        self.mcp_dispatch_docs = {}
        self.output_manager = _OutputManager()
        self.acted = []

    async def _act_on_root_response(self, response, depth=0):
        self.acted.append((response, depth))


def _sender(state):
    """A fake dispatch_send that hands back an incrementing INIT PID."""

    async def _fake_send(app, logger, tasks, session_id=None, fingerprint=None):
        state["pid"] += 1
        pid = state["pid"]
        state["sends"] += 1
        state["last_fingerprint"] = fingerprint
        return {
            "output": (
                f"Signal window (last 1):\n" f"[10:00:00] PID {pid} INIT s/t {{}}\n"
            )
        }

    return _fake_send


def _patch(monkeypatch, state, limit=3):
    monkeypatch.setattr(Config, "DISPATCH_REPEAT_LIMIT", limit)
    monkeypatch.setattr(dispatch_flow, "dispatch_send", _sender(state))
    monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)


def _nav():
    return [
        {
            "server": "playwright",
            "tool": "browser_navigate",
            "params": {"url": "https://github.com/YakupAtahanov"},
        }
    ]


def _snap():
    return [{"server": "playwright", "tool": "browser_snapshot", "params": {}}]


def _exit(pid, output=""):
    return {"type": "EXIT", "pid": pid, "data": output}


def _wrapped_exit(pid, nonce, status, body):
    """An EXIT shaped like dispatch's: status stamped ahead of the wrapper."""
    return {
        "type": "EXIT",
        "pid": pid,
        "nonce": nonce,
        "data": f"[hash={nonce}] {status} <{nonce}>{body}</{nonce}>",
    }


def _run_job():
    return [
        {
            "server": "jarvis-shell-system",
            "tool": "run_job",
            "params": {"command": "pacman -Syu", "job": "upg"},
        }
    ]


def _answer():
    return [
        {
            "server": "jarvis-shell-system",
            "tool": "send_input",
            "params": {"job": "upg", "text": "y\n"},
        }
    ]


# jarvis-shell's send_input reply is deterministic for a given (job, text):
# every answer to every prompt comes back byte-identical.
_RECEIPT = (
    '{"success": true, "job": "upg", "delivered_bytes": 2, '
    '"result": "Input delivered verbatim to the job\'s terminal"}'
)


# ---------------------------------------------------------------------------
# GoalManager.record_dispatch — the window-count primitive
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_record_dispatch_counts_within_window(tmp_path):
    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("x")
    assert gm.record_dispatch(goal.id, "A") == 1
    assert gm.record_dispatch(goal.id, "A") == 2
    assert gm.record_dispatch(goal.id, "B") == 1
    assert gm.record_dispatch(goal.id, "A") == 3  # not consecutive, still counts


@pytest.mark.unit
def test_record_dispatch_catches_interleaved_cycle(tmp_path):
    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("x")
    # A,B,A,B,A — A must reach 3 even though no two A's are adjacent.
    counts = [gm.record_dispatch(goal.id, fp) for fp in ("A", "B", "A", "B", "A")]
    assert counts[-1] == 3


@pytest.mark.unit
def test_record_dispatch_trims_to_window(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "DISPATCH_REPEAT_WINDOW", 4)
    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("x")
    for fp in ("A", "B", "C", "D", "E"):  # A ages out of a 4-wide window
        gm.record_dispatch(goal.id, fp)
    assert goal.recent_dispatches == ["B", "C", "D", "E"]
    assert gm.record_dispatch(goal.id, "A") == 1


@pytest.mark.unit
def test_record_dispatch_unknown_goal_is_zero(tmp_path):
    gm = GoalManager(archive_dir=str(tmp_path))
    assert gm.record_dispatch("nope", "A") == 0


# ---------------------------------------------------------------------------
# Guard at the dispatch choke point
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pure_repeat_trips_and_stays_silent(tmp_path, monkeypatch):
    state = {"pid": 1, "sends": 0}
    _patch(monkeypatch, state, limit=3)
    monkeypatch.setattr(dispatch_flow, "ask_llm", _boom("guard must not call the LLM"))

    gm = GoalManager(archive_dir=str(tmp_path))
    gm.add_goal("fetch page")
    app = _App(gm)

    for _ in range(5):
        if app.output_manager.responses:
            break
        asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, _nav(), depth=0))

    # Trips on the 3rd send attempt: 2 sends, 3rd short-circuited.
    assert state["sends"] == 2
    assert len(app.output_manager.responses) == 1
    assert "browser_navigate" in app.output_manager.responses[0]["output"]
    # No LLM turn was driven.
    assert app.acted == []
    # Goal is failed + dismissed so it stops re-driving.
    assert gm.get_all_goals() == []


@pytest.mark.unit
def test_interleaved_navigate_snapshot_loop_terminates(tmp_path, monkeypatch):
    """Regression: replay navigate -> about:blank -> snapshot('') -> ... and
    prove it terminates in <= limit x cycle-length dispatches with an
    informative respond, instead of looping until a provider timeout."""
    state = {"pid": 1, "sends": 0}
    _patch(monkeypatch, state, limit=3)
    monkeypatch.setattr(dispatch_flow, "ask_llm", _boom("guard must not call the LLM"))

    gm = GoalManager(archive_dir=str(tmp_path))
    gm.add_goal("fetch github page")
    app = _App(gm)

    cycle = [_nav, _snap]
    for i in range(20):
        if app.output_manager.responses:
            break
        asyncio.run(
            dispatch_flow.dispatch_execute_tasks(app, _LOG, cycle[i % 2](), depth=0)
        )
        # Every EXIT comes back empty — no progress.
        gm.update_from_signal(_exit(state["pid"], output=""))

    assert app.output_manager.responses, "loop never short-circuited"
    # limit(3) x cycle length(2) = 6 upper bound on real sends.
    assert state["sends"] <= 6
    assert "browser_navigate" in app.output_manager.responses[0]["output"]
    assert gm.get_all_goals() == []


@pytest.mark.unit
def test_new_params_each_time_does_not_trip(tmp_path, monkeypatch):
    state = {"pid": 1, "sends": 0}
    _patch(monkeypatch, state, limit=3)

    gm = GoalManager(archive_dir=str(tmp_path))
    gm.add_goal("browse several pages")
    app = _App(gm)

    for i in range(6):
        task = [
            {
                "server": "playwright",
                "tool": "browser_navigate",
                "params": {"url": f"https://example.com/{i}"},
            }
        ]
        asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, task, depth=0))

    assert app.output_manager.responses == []
    assert state["sends"] == 6


@pytest.mark.unit
def test_poll_until_ready_with_changing_exits_does_not_trip(tmp_path, monkeypatch):
    """Same tool + same params each poll, but each EXIT returns new content —
    the progress reset must age the window out so it never trips."""
    state = {"pid": 1, "sends": 0}
    _patch(monkeypatch, state, limit=3)

    gm = GoalManager(archive_dir=str(tmp_path))
    gm.add_goal("wait for job")
    app = _App(gm)

    poll = [{"server": "ci", "tool": "check_status", "params": {"job": 7}}]
    progress = ["10%", "25%", "50%", "75%", "90%", "done"]
    for text in progress:
        asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, poll, depth=0))
        gm.update_from_signal(_exit(state["pid"], output=text))

    assert app.output_manager.responses == []
    assert state["sends"] == len(progress)


@pytest.mark.unit
def test_poll_until_ready_empty_first_exit_does_not_trip(tmp_path, monkeypatch):
    """The first poll returns empty (the normal case — the job/page isn't ready
    yet), then content starts changing. The empty EXIT must seed a baseline so
    the next non-empty EXIT registers as progress; otherwise the window never
    resets and the guard trips while the tool is really advancing (#205)."""
    state = {"pid": 1, "sends": 0}
    _patch(monkeypatch, state, limit=3)

    gm = GoalManager(archive_dir=str(tmp_path))
    gm.add_goal("wait for job")
    app = _App(gm)

    poll = [{"server": "ci", "tool": "check_status", "params": {"job": 7}}]
    progress = ["", "10%", "25%", "50%"]
    for text in progress:
        asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, poll, depth=0))
        gm.update_from_signal(_exit(state["pid"], output=text))

    assert app.output_manager.responses == []
    assert state["sends"] == len(progress)


@pytest.mark.unit
def test_guard_respond_resets_history_when_configured(tmp_path, monkeypatch):
    """With RESET_HISTORY_AFTER_RESPONSE on, a normal respond clears the LLM
    conversation, so the guard's short-circuit respond must too — otherwise the
    bloated loop context that tripped the guard survives into the next turn,
    the opposite of what the config asks (#205)."""
    state = {"pid": 1, "sends": 0}
    _patch(monkeypatch, state, limit=3)
    monkeypatch.setattr(dispatch_flow, "ask_llm", _boom("guard must not call the LLM"))
    monkeypatch.setattr(Config, "RESET_HISTORY_AFTER_RESPONSE", True)

    gm = GoalManager(archive_dir=str(tmp_path))
    gm.add_goal("fetch page")
    app = _App(gm)

    resets = {"n": 0}

    class _LLM:
        def reset_history(self):
            resets["n"] += 1

    app.llm = _LLM()

    for _ in range(5):
        if app.output_manager.responses:
            break
        asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, _nav(), depth=0))

    assert app.output_manager.responses, "guard never tripped"
    assert resets["n"] == 1


# ---------------------------------------------------------------------------
# Progress detection is nonce-independent (boundary-wrapped output)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_identical_inner_content_under_different_nonces_is_not_progress(tmp_path):
    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("x")
    gm.record_dispatch(goal.id, "fp")
    gm.record_dispatch(goal.id, "fp")
    gm.link_tasks(goal.id, [1, 2])
    gm.link_dispatch_fingerprint(goal.id, "fp", [1, 2])

    # Same inner content "X", different per-task nonces — the wrapper bytes
    # differ but the tool made no progress, so the window must survive.
    gm.update_from_signal(
        {
            "type": "EXIT",
            "pid": 1,
            "data": "[hash=aaa] 200 <aaa>X</aaa>",
            "nonce": "aaa",
        }
    )
    gm.update_from_signal(
        {
            "type": "EXIT",
            "pid": 2,
            "data": "[hash=bbb] 200 <bbb>X</bbb>",
            "nonce": "bbb",
        }
    )
    assert goal.recent_dispatches == ["fp", "fp"]


@pytest.mark.unit
def test_changed_inner_content_resets_window(tmp_path):
    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("x")
    gm.record_dispatch(goal.id, "fp")
    gm.record_dispatch(goal.id, "fp")
    gm.link_tasks(goal.id, [1, 2])
    gm.link_dispatch_fingerprint(goal.id, "fp", [1, 2])

    gm.update_from_signal(
        {
            "type": "EXIT",
            "pid": 1,
            "data": "[hash=aaa] 200 <aaa>X</aaa>",
            "nonce": "aaa",
        }
    )
    gm.update_from_signal(
        {
            "type": "EXIT",
            "pid": 2,
            "data": "[hash=bbb] 200 <bbb>Y</bbb>",
            "nonce": "bbb",
        }
    )
    assert goal.recent_dispatches == []


# ---------------------------------------------------------------------------
# Confirmation-gated dispatches must reset the window across the approval cycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_confirmation_gated_poll_until_ready_does_not_trip(tmp_path, monkeypatch):
    """A confirmation-gated tool dispatched with identical params, approved on
    every cycle, each approved run returning changing non-empty content, must
    NOT trip the guard.

    The gate makes dispatch_send return awaiting_confirmation before the direct
    path links the fingerprint, and the approved batch is sent later from
    on_confirmation_response. Unless the fingerprint survives that round-trip and
    is re-linked to the approved PIDs, the EXIT can never reset the window and a
    genuinely progressing gated tool short-circuits (#205)."""
    from jarvis.core.confirmation_manager import ConfirmationManager
    from jarvis.runtime import root_handlers

    monkeypatch.setattr(Config, "DISPATCH_REPEAT_LIMIT", 3)
    monkeypatch.setattr(Config, "CONFIRMATION_MODE", "ask_all")
    monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)
    monkeypatch.setattr(root_handlers, "emit_activity", lambda *a, **k: None)

    conf = ConfirmationManager()

    async def _no_notify(*a, **k):
        # No live channel in the test — leave the request pending; the test
        # drives the approval explicitly.
        pass

    monkeypatch.setattr(conf, "_send_notification", _no_notify)

    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("run migrations step by step")

    state = {"pid": 1}

    class _Dispatch:
        is_connected = True

        async def list_server_tools(self, server_id):
            return {"tools": [{"name": "run_migration"}]}

        async def send_tasks(self, tasks, session_id=None):
            state["pid"] += 1
            return {
                "output": (
                    f"Signal window (last 1):\n"
                    f"[10:00:00] PID {state['pid']} INIT db/run_migration {{}}\n"
                )
            }

    class _ConfApp:
        def __init__(self):
            self.goals = gm
            self.dispatch = _Dispatch()
            self.confirmation = conf
            self.contextor = None
            self.llm = object()  # not None; happy path never calls it
            self._gui_clients = None
            self.sessions = SimpleNamespace(current_id=None)
            self.output_manager = _OutputManager()
            self.acted = []

        async def _act_on_root_response(self, response, depth=0):
            self.acted.append((response, depth))

    app = _ConfApp()

    task = [{"server": "db", "tool": "run_migration", "params": {"step": "next"}}]
    outputs = ["applied 0001", "applied 0002", "applied 0003", "applied 0004"]

    for text in outputs:
        asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, task, depth=0))
        if app.output_manager.responses:
            break  # guard tripped — the bug; assertion below fails cleanly
        # Gated: nothing has run yet, exactly one confirmation is pending.
        req_id = conf.list_pending()[0]["id"]
        asyncio.run(
            root_handlers.on_confirmation_response(
                app, _LOG, {"id": req_id, "approved": True}
            )
        )
        gm.update_from_signal(_exit(state["pid"], output=text))

    # Every approved run made real progress, so the guard must not have tripped.
    assert app.output_manager.responses == []
    assert app.acted == []
    assert gm.get_all_goals() == [goal]


@pytest.mark.unit
def test_gated_wizard_answers_survive_the_approval_cycle(tmp_path, monkeypatch):
    """End to end in smart mode: send_input sits at the host floor, so every
    answer is confirmed, and each approved delivery must age the repeat window
    out. Both halves matter — an ungated answer would reach a root PTY with no
    informed consent, and a counted one kills the goal at the third prompt."""
    from jarvis.core.confirmation_manager import ConfirmationManager
    from jarvis.runtime import root_handlers

    monkeypatch.setattr(Config, "DISPATCH_REPEAT_LIMIT", 3)
    monkeypatch.setattr(Config, "CONFIRMATION_MODE", "smart")
    monkeypatch.setattr(dispatch_flow, "emit_activity", lambda *a, **k: None)
    monkeypatch.setattr(root_handlers, "emit_activity", lambda *a, **k: None)

    conf = ConfirmationManager()

    async def _no_notify(*a, **k):
        pass

    monkeypatch.setattr(conf, "_send_notification", _no_notify)

    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("update my system")

    state = {"pid": 1}

    class _Dispatch:
        is_connected = True

        async def list_server_tools(self, server_id):
            # The shell manifests declare no threat_level on any job tool.
            return {"tools": [{"name": "send_input"}]}

        async def send_tasks(self, tasks, session_id=None):
            state["pid"] += 1
            return {
                "output": (
                    f"Signal window (last 1):\n"
                    f"[10:00:00] PID {state['pid']} INIT sh/send_input {{}}\n"
                )
            }

    class _ConfApp:
        def __init__(self):
            self.goals = gm
            self.dispatch = _Dispatch()
            self.confirmation = conf
            self.contextor = None
            self.llm = object()
            self._gui_clients = None
            self.sessions = SimpleNamespace(current_id=None)
            self.output_manager = _OutputManager()
            self.acted = []

        async def _act_on_root_response(self, response, depth=0):
            self.acted.append((response, depth))

    app = _ConfApp()

    for i in range(5):
        asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, _answer(), depth=0))
        if app.output_manager.responses:
            break  # guard tripped — the bug; assertions below fail cleanly
        pending = conf.list_pending()
        assert pending, "send_input reached a root PTY without confirmation"
        asyncio.run(
            root_handlers.on_confirmation_response(
                app, _LOG, {"id": pending[0]["id"], "approved": True}
            )
        )
        gm.update_from_signal(_wrapped_exit(state["pid"], f"a{i}f", 200, _RECEIPT))

    assert app.output_manager.responses == []
    assert gm.get_all_goals() == [goal]


# ---------------------------------------------------------------------------
# Answering an interactive job (#211) — deliveries are judged on delivery
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_delivers_live_input_only_for_a_pure_delivery_batch():
    assert dispatch_flow._delivers_live_input(_answer()) is True
    assert dispatch_flow._delivers_live_input(_run_job()) is False
    assert dispatch_flow._delivers_live_input([]) is False
    # A batch that also runs something is a normal dispatch: the guard must
    # keep judging it, or a loop could hide behind one send_input.
    assert dispatch_flow._delivers_live_input(_answer() + _run_job()) is False


@pytest.mark.unit
def test_successful_delivery_exit_resets_the_window(tmp_path):
    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("update my system")
    gm.record_dispatch(goal.id, "fp", delivers_input=True)
    gm.record_dispatch(goal.id, "fp", delivers_input=True)
    gm.link_tasks(goal.id, [2])
    gm.link_dispatch_fingerprint(goal.id, "fp", [2])
    assert goal.dispatch_input_fps == ["fp"]

    gm.update_from_signal(_wrapped_exit(2, "aaa", 200, _RECEIPT))

    assert goal.recent_dispatches == []


@pytest.mark.unit
def test_failed_delivery_exit_leaves_the_window_intact(tmp_path):
    """A delivery into a job that is gone is exactly the no-progress case the
    guard exists for, so its 500 must not buy the loop another cycle."""
    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("update my system")
    gm.record_dispatch(goal.id, "fp", delivers_input=True)
    gm.record_dispatch(goal.id, "fp", delivers_input=True)
    gm.link_tasks(goal.id, [2])
    gm.link_dispatch_fingerprint(goal.id, "fp", [2])

    gm.update_from_signal(_wrapped_exit(2, "aaa", 500, '{"error": "unknown job"}'))

    assert goal.recent_dispatches == ["fp", "fp"]


@pytest.mark.unit
def test_unverified_delivery_exit_is_not_progress(tmp_path):
    """Output that failed the provenance boundary carries the UNVERIFIED marker
    ahead of the status, and untrusted output is evidence of nothing."""
    from jarvis.dispatch.boundary import UNVERIFIED_MARK

    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("update my system")
    gm.record_dispatch(goal.id, "fp", delivers_input=True)
    gm.record_dispatch(goal.id, "fp", delivers_input=True)
    gm.link_tasks(goal.id, [2])
    gm.link_dispatch_fingerprint(goal.id, "fp", [2])

    signal = _wrapped_exit(2, "aaa", 200, _RECEIPT)
    signal["data"] = UNVERIFIED_MARK + signal["data"]
    gm.update_from_signal(signal)

    assert goal.recent_dispatches == ["fp", "fp"]


@pytest.mark.unit
def test_answering_a_wizard_does_not_trip_the_guard(tmp_path, monkeypatch):
    """The taught loop (#211): pacman -Syu as a job, then one send_input per
    "[Y/n]" it asks. The answers are byte-identical — same job, same "y\\n", and
    a receipt that never varies — so counting them as repeats killed the goal on
    the third prompt and orphaned a root-owned transaction parked at a prompt."""
    state = {"pid": 1, "sends": 0}
    _patch(monkeypatch, state, limit=3)
    monkeypatch.setattr(dispatch_flow, "ask_llm", _boom("guard must not call the LLM"))

    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("update my system")
    app = _App(gm)

    asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, _run_job(), depth=0))

    for i in range(5):
        asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, _answer(), depth=0))
        if app.output_manager.responses:
            break  # guard tripped — the bug; assertions below fail cleanly
        # The run_job task is still parked on its next prompt; only the
        # delivery exits, with the same receipt every time.
        gm.update_from_signal(_wrapped_exit(state["pid"], f"a{i}f", 200, _RECEIPT))

    assert app.output_manager.responses == []
    assert state["sends"] == 6  # the job plus all five answers
    assert gm.get_all_goals() == [goal]


@pytest.mark.unit
def test_delivery_into_a_dead_job_still_trips(tmp_path, monkeypatch):
    """Same identical send_input, but the job is gone: every delivery fails, so
    the exemption must not apply and the loop still ends at the limit."""
    state = {"pid": 1, "sends": 0}
    _patch(monkeypatch, state, limit=3)
    monkeypatch.setattr(dispatch_flow, "ask_llm", _boom("guard must not call the LLM"))

    gm = GoalManager(archive_dir=str(tmp_path))
    gm.add_goal("update my system")
    app = _App(gm)

    for i in range(6):
        if app.output_manager.responses:
            break
        asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, _answer(), depth=0))
        gm.update_from_signal(
            _wrapped_exit(state["pid"], f"a{i}f", 500, '{"error": "unknown job upg"}')
        )

    assert app.output_manager.responses, "loop never short-circuited"
    assert state["sends"] == 2
    assert "send_input" in app.output_manager.responses[0]["output"]
    assert gm.get_all_goals() == []


@pytest.mark.unit
def test_successful_exit_alone_is_not_progress_for_other_tools(tmp_path, monkeypatch):
    """The exemption is scoped to deliveries: a tool call that keeps succeeding
    while returning the same nothing (the #205 about:blank snapshot) must still
    trip, or reading the EXIT status would have gutted the guard."""
    state = {"pid": 1, "sends": 0}
    _patch(monkeypatch, state, limit=3)
    monkeypatch.setattr(dispatch_flow, "ask_llm", _boom("guard must not call the LLM"))

    gm = GoalManager(archive_dir=str(tmp_path))
    gm.add_goal("fetch page")
    app = _App(gm)

    for i in range(6):
        if app.output_manager.responses:
            break
        asyncio.run(dispatch_flow.dispatch_execute_tasks(app, _LOG, _snap(), depth=0))
        gm.update_from_signal(_wrapped_exit(state["pid"], f"a{i}f", 200, ""))

    assert app.output_manager.responses, "loop never short-circuited"
    assert state["sends"] == 2
    assert gm.get_all_goals() == []


# ---------------------------------------------------------------------------
# Secondary: task_pids dedup (loop_body.md bottom section)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_link_tasks_dedups_replayed_pids(tmp_path):
    """_extract_pids_from_result re-offers earlier INITs from the rolling
    signal window, so link_tasks must not grow [2] -> [2,2,3] -> [2,2,3,2,3,4]."""
    gm = GoalManager(archive_dir=str(tmp_path))
    goal = gm.add_goal("x")
    gm.link_tasks(goal.id, [2])
    gm.link_tasks(goal.id, [2, 3])  # window still holds PID 2's INIT
    gm.link_tasks(goal.id, [2, 3, 4])
    assert goal.task_pids == [2, 3, 4]


def _boom(msg):
    async def _fail(*a, **k):
        raise AssertionError(msg)

    return _fail
