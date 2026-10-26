"""CONFIG_HINT must reach the LLM from the EXIT-signal path.

#195 made the dispatch path go silent (EXIT signals drive the next ROOT turn),
which silently took the old dispatch_flow CONFIG_HINT builder out of the loop.
Re-homed into root_handlers' two signal handlers: when an EXIT carries an
auth/token error, the context must name the failing server's exact config keys
so the LLM can call configure_server instead of re-running the same bad call
until the #205 repeat guard kills the goal.
"""

import asyncio
import logging

from jarvis.core.confirmation_manager import ConfirmationManager, PendingConfirmation
from jarvis.dispatch.goal_manager import GoalManager
from jarvis.dispatch.transport import _normalize_pushed_signal
from jarvis.runtime import root_handlers
from jarvis.runtime.dispatch_flow import _batch_fingerprint

_LOG = logging.getLogger("test")

_MANIFEST = {
    "configurableProperties": [
        {"key": "BRAVE_API_KEY", "label": "Brave API key"},
        {"key": "BRAVE_ENDPOINT", "label": "Endpoint"},
    ]
}

_BRAVE = "com.brave.search"
_SQLITE = "io.github.eduresser.sqlite-mcp"


class _FakeDispatch:
    def __init__(self, manifest=None):
        self.manifest = _MANIFEST if manifest is None else manifest
        self.asked_for = []
        self.sent = None
        self.session_id = "UNSET"
        self.init_pids = [7]

    async def get_server_manifest(self, server_id):
        self.asked_for.append(server_id)
        return self.manifest

    async def send_tasks(self, tasks, session_id=None):
        self.sent = list(tasks)
        self.session_id = session_id
        lines = "".join(
            f"[10:00:00] PID {pid} INIT "
            f"{task.get('server')}/{task.get('tool')} {{}}\n"
            for pid, task in zip(self.init_pids, tasks)
        )
        return {"output": f"Signal window (last {len(tasks)}):\n{lines}"}


class _FakeSessions:
    current_id = "s1"

    def load_summary(self):
        return ""


class _FakeApp:
    def __init__(self, goals, manifest=None, confirmation=None):
        self.goals = goals
        self.dispatch = _FakeDispatch(manifest)
        self.sessions = _FakeSessions()
        self.confirmation = confirmation
        self._gui_clients = None
        self.llm = object()  # not None
        self.acted = None

    async def _act_on_root_response(self, response, depth=0):
        self.acted = response


def _exit_signal(pid, output):
    return {"type": "EXIT", "pid": pid, "data": {"output": output}}


def _wire_signal(pid, kind, message):
    """A signal built the way the daemon actually produces one.

    dispatch's wire struct is ``{timestamp, pid, kind, message, payload?,
    nonce?}``; _normalize_pushed_signal maps it to the canonical
    ``{type, pid, data, timestamp}``. Building through it keeps these tests
    from drifting to a shape production cannot emit — which is exactly how the
    old ``sig["server"]`` fallback test came to green-light unreachable code.
    """
    return _normalize_pushed_signal(
        {"timestamp": "10:00:00", "pid": pid, "kind": kind, "message": message}
    )


def _wire(monkeypatch):
    """Stub context building + the LLM call; capture the context handed to it."""
    seen = {}

    monkeypatch.setattr(root_handlers, "build_root_context", lambda a, l, **k: "")
    monkeypatch.setattr(root_handlers, "emit_activity", lambda *a, **k: None)

    async def _fake_ask(a, l, context, **k):
        seen["context"] = context
        return {"action": "respond", "output": "ok"}

    monkeypatch.setattr(root_handlers, "ask_llm", _fake_ask)
    return seen


def _goal_with_dispatch(tmp_path, server=_BRAVE, pid=7):
    goals = GoalManager(archive_dir=str(tmp_path))
    goal = goals.add_goal("search the web")
    tasks = [{"server": server, "tool": "brave_search", "params": {}}]
    goals.link_tasks(goal.id, [pid])
    # Fingerprint format is server<US>tool<US>params, batches joined by <RS>.
    goals.link_dispatch_fingerprint(goal.id, _batch_fingerprint(tasks), [pid])
    goals.link_dispatch_servers(goal.id, [pid], tasks)
    return goals, goal


def _goal_with_batch(tmp_path):
    """A two-server batch linked exactly as dispatch_flow links a real dispatch."""
    goals = GoalManager(archive_dir=str(tmp_path))
    goal = goals.add_goal("search the web, then query the db")
    tasks = [
        {"server": _BRAVE, "tool": "brave_search", "params": {}},
        {"server": _SQLITE, "tool": "query", "params": {}},
    ]
    pids = [1, 2]
    goals.link_tasks(goal.id, pids)
    goals.link_dispatch_fingerprint(goal.id, _batch_fingerprint(tasks), pids)
    goals.link_dispatch_servers(goal.id, pids, tasks)
    return goals, goal


class TestSingleSignal:
    def test_auth_error_exit_adds_config_hint_with_manifest_keys(
        self, tmp_path, monkeypatch
    ):
        goals, _goal = _goal_with_dispatch(tmp_path)
        app = _FakeApp(goals)
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signal(
                app,
                _LOG,
                _exit_signal(7, "error: SUBSCRIPTION_TOKEN_INVALID (422)"),
            )
        )

        ctx = seen["context"]
        assert "CONFIG_HINT" in ctx
        assert "com.brave.search" in ctx
        # The exact key names from the manifest, not guessed from error wording.
        assert "BRAVE_API_KEY" in ctx
        assert "BRAVE_ENDPOINT" in ctx
        assert '"action": "configure_server"' in ctx
        assert app.dispatch.asked_for == ["com.brave.search"]

    def test_non_auth_error_exit_adds_no_hint(self, tmp_path, monkeypatch):
        goals, _goal = _goal_with_dispatch(tmp_path)
        app = _FakeApp(goals)
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signal(
                app, _LOG, _exit_signal(7, "error: connection timed out")
            )
        )

        assert "CONFIG_HINT" not in seen["context"]
        # No manifest lookup at all when nothing looks like an auth failure.
        assert app.dispatch.asked_for == []

    def test_server_with_no_configurable_properties_adds_no_hint(
        self, tmp_path, monkeypatch
    ):
        goals, _goal = _goal_with_dispatch(tmp_path)
        app = _FakeApp(goals, manifest={"configurableProperties": []})
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signal(app, _LOG, _exit_signal(7, "unauthorized"))
        )

        assert "CONFIG_HINT" not in seen["context"]

    def test_manifest_lookup_failure_does_not_break_the_turn(
        self, tmp_path, monkeypatch
    ):
        goals, _goal = _goal_with_dispatch(tmp_path)
        app = _FakeApp(goals)

        async def _boom(server_id):
            raise RuntimeError("registry unreachable")

        app.dispatch.get_server_manifest = _boom
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signal(
                app, _LOG, _exit_signal(7, "invalid_api_key")
            )
        )

        assert "CONFIG_HINT" not in seen["context"]
        assert app.acted == {"action": "respond", "output": "ok"}


class TestBatchedSignals:
    def test_auth_error_in_batch_adds_config_hint(self, tmp_path, monkeypatch):
        goals, _goal = _goal_with_dispatch(tmp_path)
        app = _FakeApp(goals)
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signals(
                app,
                _LOG,
                [_exit_signal(7, "error: Unauthorized — token_invalid")],
            )
        )

        ctx = seen["context"]
        assert "CONFIG_HINT" in ctx
        assert "BRAVE_API_KEY" in ctx

    def test_clean_batch_adds_no_hint(self, tmp_path, monkeypatch):
        goals, _goal = _goal_with_dispatch(tmp_path)
        app = _FakeApp(goals)
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signals(
                app, _LOG, [_exit_signal(7, "Python 3.11.15")]
            )
        )

        assert "CONFIG_HINT" not in seen["context"]

    def test_unattributable_pid_names_no_server(self, tmp_path, monkeypatch):
        """An EXIT for a PID no goal owns must not guess a server.

        This replaces a test that set ``sig["server"]`` by hand and asserted the
        hint used it. Dispatch's wire format has no such field
        (``SignalEntry{timestamp,pid,kind,message,payload?,nonce?}``), so that
        branch could never fire in production; building the signal through
        _normalize_pushed_signal keeps this pinned to the real shape.
        """
        goals = GoalManager(archive_dir=str(tmp_path))
        app = _FakeApp(goals)
        seen = _wire(monkeypatch)

        sig = _wire_signal(99, "EXIT", "invalid_token")
        assert "server" not in sig

        asyncio.run(root_handlers.on_dispatch_signals(app, _LOG, [sig]))

        assert "CONFIG_HINT" not in seen["context"]
        assert app.dispatch.asked_for == []


class TestServerAttribution:
    """One failing task must name its own server — not every server in the batch."""

    def test_batch_exit_names_only_the_failing_task_server(self, tmp_path, monkeypatch):
        goals, _goal = _goal_with_batch(tmp_path)
        app = _FakeApp(goals)
        seen = _wire(monkeypatch)

        # Only pid 1 (the Brave task) failed; the sqlite task never reported.
        asyncio.run(
            root_handlers.on_dispatch_signals(
                app, _LOG, [_wire_signal(1, "EXIT", "error: invalid_api_key")]
            )
        )

        ctx = seen["context"]
        assert "CONFIG_HINT" in ctx
        assert _BRAVE in ctx.split("CONFIG_HINT", 1)[1]
        # The healthy server is neither named in the hint nor even looked up.
        assert app.dispatch.asked_for == [_BRAVE]
        assert f"CONFIG_HINT: {_SQLITE}" not in ctx

    def test_unmapped_pid_with_batch_fingerprint_names_nothing(
        self, tmp_path, monkeypatch
    ):
        """A stale batch-wide fingerprint must yield no hint, not a wrong one."""
        goals, goal = _goal_with_batch(tmp_path)
        # Simulate a PID known to the fingerprint map but not the server map
        # (e.g. an entry that predates per-PID attribution).
        goal.dispatch_pid_server.clear()
        app = _FakeApp(goals)
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signals(
                app, _LOG, [_wire_signal(1, "EXIT", "error: invalid_api_key")]
            )
        )

        assert "CONFIG_HINT" not in seen["context"]
        assert app.dispatch.asked_for == []

    def test_unmapped_pid_with_single_task_fingerprint_still_hints(
        self, tmp_path, monkeypatch
    ):
        """A single-task fingerprint is unambiguous, so it stays a valid source."""
        goals, goal = _goal_with_dispatch(tmp_path)
        goal.dispatch_pid_server.clear()
        app = _FakeApp(goals)
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signals(
                app, _LOG, [_wire_signal(7, "EXIT", "error: invalid_api_key")]
            )
        )

        assert "CONFIG_HINT" in seen["context"]
        assert app.dispatch.asked_for == [_BRAVE]

    def test_denied_server_is_never_named_after_partial_approval(
        self, tmp_path, monkeypatch
    ):
        """#187 partial approval: the resume path re-links the FULL batch
        fingerprint (the repeat guard counts batches), so server attribution has
        to come from the approved subset — otherwise a tool the user explicitly
        denied gets its config keys read out to the LLM."""
        goals = GoalManager(archive_dir=str(tmp_path))
        goal = goals.add_goal("search the web, then query the db")
        brave_task = {"server": _BRAVE, "tool": "brave_search", "params": {}}
        sqlite_task = {"server": _SQLITE, "tool": "query", "params": {}}

        conf = ConfirmationManager()
        conf._pending["r1"] = PendingConfirmation(
            request_id="r1",
            tasks=[brave_task, sqlite_task],
            approved_tasks=[],
            confirm_details=[
                {"tool_name": f"{_BRAVE}.brave_search", "task": brave_task},
                {"tool_name": f"{_SQLITE}.query", "task": sqlite_task},
            ],
            session_id=goal.id,
            fingerprint=_batch_fingerprint([brave_task, sqlite_task]),
        )

        app = _FakeApp(goals, confirmation=conf)
        seen = _wire(monkeypatch)

        # Approve only the Brave task; deny the sqlite one.
        asyncio.run(
            root_handlers.on_confirmation_response(
                app, _LOG, {"id": "r1", "approved_indices": [0]}
            )
        )
        assert app.dispatch.sent == [brave_task]
        # The full batch fingerprint is still what the repeat guard sees (#205).
        assert goal.dispatch_pid_fps[7] == _batch_fingerprint([brave_task, sqlite_task])

        # Now the approved task fails authentication.
        asyncio.run(
            root_handlers.on_dispatch_signal(
                app, _LOG, _wire_signal(7, "EXIT", "error: invalid_api_key")
            )
        )

        ctx = seen["context"]
        assert "CONFIG_HINT" in ctx
        assert _SQLITE not in ctx
        assert app.dispatch.asked_for == [_BRAVE]


class TestSignalTypeGate:
    """Only EXIT/TIMEOUT can report a tool failure; nothing else may trigger a hint."""

    def test_successful_exit_merely_mentioning_auth_adds_no_hint(
        self, tmp_path, monkeypatch
    ):
        goals, _goal = _goal_with_dispatch(tmp_path)
        app = _FakeApp(goals)
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signal(
                app,
                _LOG,
                _wire_signal(
                    7, "EXIT", "Result: 'A guide to OAuth authentication' - example.com"
                ),
            )
        )

        assert "CONFIG_HINT" not in seen["context"]
        assert app.dispatch.asked_for == []

    def test_kill_signal_mentioning_unauthorized_adds_no_hint(
        self, tmp_path, monkeypatch
    ):
        goals, _goal = _goal_with_dispatch(tmp_path)
        app = _FakeApp(goals)
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signal(
                app, _LOG, _wire_signal(7, "KILL", "killed: unauthorized by operator")
            )
        )

        # A KILL never reaches the model at all, so it cannot carry a hint.
        assert "context" not in seen
        assert app.dispatch.asked_for == []

    def test_merged_remind_exit_still_hints_off_the_exit(self, tmp_path, monkeypatch):
        """EventMerger delivers a REMIND carrying the real EXIT (_exit); the hint
        must follow the outcome, not the wrapper's type."""
        goals, _goal = _goal_with_dispatch(tmp_path)
        app = _FakeApp(goals)
        seen = _wire(monkeypatch)

        remind = _wire_signal(7, "REMIND", "still running")
        remind["_remind_completed"] = True
        remind["_exit"] = _wire_signal(7, "EXIT", "error: invalid_api_key")

        asyncio.run(root_handlers.on_dispatch_signal(app, _LOG, remind))

        assert "CONFIG_HINT" in seen["context"]
        assert app.dispatch.asked_for == [_BRAVE]


class TestRequiredFlagFiltering:
    """The hint names keys the server actually requires, not every tuning knob."""

    def test_optional_properties_are_left_out(self, tmp_path, monkeypatch):
        goals, _goal = _goal_with_dispatch(tmp_path, server=_SQLITE)
        app = _FakeApp(
            goals,
            manifest={
                "configurableProperties": [
                    {"key": "SQLITE_DB_PATH", "required": True},
                    {"key": "SQLITE_READ_ONLY", "required": False, "default": "false"},
                    {"key": "SQLITE_TIMEOUT", "required": False, "default": "30"},
                ]
            },
        )
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signal(
                app, _LOG, _wire_signal(7, "EXIT", "error: invalid_token")
            )
        )

        ctx = seen["context"]
        assert "CONFIG_HINT" in ctx
        assert "SQLITE_DB_PATH" in ctx
        # Optional tuning knobs must not be presented as values to invent.
        assert "SQLITE_READ_ONLY" not in ctx
        assert "SQLITE_TIMEOUT" not in ctx

    def test_missing_required_flag_keeps_the_key(self, tmp_path, monkeypatch):
        """Real manifests omit `required`; omission must mean "include", or the
        filter would silence the hint entirely."""
        goals, _goal = _goal_with_dispatch(tmp_path)
        app = _FakeApp(goals)  # _MANIFEST has no `required` on either property
        seen = _wire(monkeypatch)

        asyncio.run(
            root_handlers.on_dispatch_signal(
                app, _LOG, _wire_signal(7, "EXIT", "error: invalid_api_key")
            )
        )

        ctx = seen["context"]
        assert "BRAVE_API_KEY" in ctx
        assert "BRAVE_ENDPOINT" in ctx
