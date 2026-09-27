"""TUI confirmations panel (#235) and the engine-less TUI (#236).

The panel is driven through Textual's ``run_test`` pilot rather than by calling
its methods, because the things worth pinning are keyboard behaviours: that a
row expands, that space ticks one task and not the batch, and that approving a
deselected subset sends the per-task message rather than a blanket approve.

The engine-less path is exercised with ``app.jarvis`` left as None, which is
exactly the state ``enter_offline_mode`` leaves the app in.
"""

import json

import pytest

from jarvis.core.confirmation_manager import (
    DECISION_QUEUE_FILENAME,
    HISTORY_FILENAME,
    STORE_FILENAME,
)
from jarvis.tui.confirmations_panel import (
    SCOPE_ALL,
    SCOPE_SESSION,
    ConfirmationsPanel,
    filter_by_scope,
    format_age,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Scratch confirmation store/queue/history, and no real engine boot.

    ``on_mount`` would otherwise construct a genuine ``Jarvis`` — spawning
    contextor and reaching for Ollama — which is both slow and the wrong
    subject: these tests are about the panel, not about engine startup.
    """
    from jarvis.core import confirmation_manager as cm
    from jarvis.tui import lifecycle as tui_lifecycle

    # Patch the Config object *this module* bound, not whatever
    # ``jarvis.config.Config`` currently names: other test modules call
    # ``importlib.reload(jarvis.config)``, which rebinds the name to a brand
    # new class while modules that did ``from ..config import Config`` keep
    # the original. Patching the wrong one is silently ineffective.
    monkeypatch.setattr(cm.Config, "JARVIS_DATA_DIR", str(tmp_path), raising=False)

    async def _no_engine(app, logger):
        await tui_lifecycle.enter_offline_mode(app, logger, "no engine (test)")

    monkeypatch.setattr(tui_lifecycle, "start_jarvis", _no_engine)
    return tmp_path


def _write_store(data_dir, entries):
    (data_dir / STORE_FILENAME).write_text(json.dumps(entries), encoding="utf-8")


def _entry(request_id, lines, chat_session_id=None, created_at=1000.0):
    return {
        "request_id": request_id,
        "tasks": [{"server": "srv", "tool": f"t{i}"} for i in range(len(lines))],
        "confirm_details": [
            {"tool_name": f"srv.t{i}", "task": {"server": "srv", "tool": f"t{i}"}}
            for i in range(len(lines))
        ],
        "tool_names": [f"srv.t{i}" for i in range(len(lines))],
        "tool_lines": lines,
        "created_at": created_at,
        "chat_session_id": chat_session_id,
    }


def _queued(data_dir):
    path = data_dir / DECISION_QUEUE_FILENAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


async def _open_panel(app, pilot):
    """Reproduce the F3 path: show the panel and focus its list."""
    app.action_confirmations()
    await pilot.pause()
    return app.query_one("#confirmations-panel", ConfirmationsPanel)


def _rows(panel):
    from textual.widgets import ListView

    return list(panel.query_one("#confirmations-list", ListView).children)


def _row_text(row):
    from textual.widgets import Label

    return str(row.query_one(Label).content)


@pytest.mark.unit
class TestPurehelpers:
    async def test_format_age_units(self):
        now = 100000.0
        assert format_age(now - 5, now) == "5s"
        assert format_age(now - 300, now) == "5m"
        assert format_age(now - 7200, now) == "2h"
        assert format_age(now - 172800, now) == "2d"

    async def test_scope_all_keeps_everything(self):
        entries = [{"chat_session_id": "a"}, {"chat_session_id": "b"}]
        assert filter_by_scope(entries, SCOPE_ALL, "a") == entries

    async def test_scope_session_filters(self):
        entries = [{"chat_session_id": "a"}, {"chat_session_id": "b"}]
        assert filter_by_scope(entries, SCOPE_SESSION, "a") == [
            {"chat_session_id": "a"}
        ]

    async def test_unattributed_entries_survive_the_session_filter(self):
        """Pre-#235 entries carry None; hiding them would lose them entirely."""
        entries = [{"chat_session_id": None}, {"chat_session_id": "b"}]
        assert filter_by_scope(entries, SCOPE_SESSION, "a") == [
            {"chat_session_id": None}
        ]

    async def test_scope_session_without_a_session_keeps_everything(self):
        entries = [{"chat_session_id": "a"}, {"chat_session_id": "b"}]
        assert filter_by_scope(entries, SCOPE_SESSION, None) == entries


@pytest.mark.unit
class TestPanelWithoutAnEngine:
    """#236: no engine, so everything comes off disk and decisions queue."""

    async def test_pending_from_the_store_is_listed(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        _write_store(data_dir, [_entry("abc123", ["srv.t0: rm -rf /tmp/x"])])
        app = JarvisTUI()
        async with app.run_test() as pilot:
            panel = await _open_panel(app, pilot)
            text = "\n".join(_row_text(r) for r in _rows(panel))
            assert "abc123" in text
            assert "rm -rf /tmp/x" in text
            assert "Pending (1)" in text

    async def test_approve_queues_the_socket_protocol_message(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        _write_store(data_dir, [_entry("abc123", ["srv.t0: run"])])
        app = JarvisTUI()
        async with app.run_test() as pilot:
            panel = await _open_panel(app, pilot)
            await pilot.press("a")
            await pilot.pause()

        assert _queued(data_dir) == [{"type": "approve_confirmation", "id": "abc123"}]

    async def test_deny_queues_a_deny(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        _write_store(data_dir, [_entry("abc123", ["srv.t0: run"])])
        app = JarvisTUI()
        async with app.run_test() as pilot:
            panel = await _open_panel(app, pilot)
            await pilot.press("d")
            await pilot.pause()

        assert _queued(data_dir) == [{"type": "deny_confirmation", "id": "abc123"}]

    async def test_expand_reveals_one_row_per_task(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        _write_store(data_dir, [_entry("abc123", ["srv.t0: alpha", "srv.t1: beta"])])
        app = JarvisTUI()
        async with app.run_test() as pilot:
            panel = await _open_panel(app, pilot)
            await pilot.press("enter")
            await pilot.pause()

            text = "\n".join(_row_text(r) for r in _rows(panel))
            assert "alpha" in text and "beta" in text
            # Both ticked by default — expanding is not a deselect-all.
            assert text.count("\\[x]") == 2

    async def test_space_unticks_one_task_and_approve_sends_a_partial(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        _write_store(data_dir, [_entry("abc123", ["srv.t0: alpha", "srv.t1: beta"])])
        app = JarvisTUI()
        async with app.run_test() as pilot:
            panel = await _open_panel(app, pilot)
            await pilot.press("enter")  # expand
            await pilot.press("down")  # first task row
            await pilot.press("space")  # untick task 0
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()

        assert _queued(data_dir) == [
            {
                "type": "partial_approve_confirmation",
                "id": "abc123",
                "approved_indices": [1],
            }
        ]

    async def test_approving_an_expanded_batch_untouched_is_a_plain_approve(
        self, data_dir
    ):
        """Expanding to look is not the same as deselecting something."""
        from jarvis.tui.app import JarvisTUI

        _write_store(data_dir, [_entry("abc123", ["srv.t0: alpha", "srv.t1: beta"])])
        app = JarvisTUI()
        async with app.run_test() as pilot:
            panel = await _open_panel(app, pilot)
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()

        assert _queued(data_dir) == [{"type": "approve_confirmation", "id": "abc123"}]

    async def test_changing_your_mind_replaces_the_queued_decision(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        _write_store(data_dir, [_entry("abc123", ["srv.t0: run"])])
        app = JarvisTUI()
        async with app.run_test() as pilot:
            panel = await _open_panel(app, pilot)
            await pilot.press("a")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()

        assert _queued(data_dir) == [{"type": "deny_confirmation", "id": "abc123"}]

    async def test_scope_toggle_filters_to_the_browsed_session(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        _write_store(
            data_dir,
            [
                _entry("mine01", ["srv.t0: mine"], chat_session_id="sess-1"),
                _entry("other1", ["srv.t0: theirs"], chat_session_id="sess-2"),
            ],
        )
        app = JarvisTUI()
        async with app.run_test() as pilot:
            app._offline_session_id = "sess-1"
            panel = await _open_panel(app, pilot)

            text = "\n".join(_row_text(r) for r in _rows(panel))
            assert "mine01" in text and "other1" in text

            await pilot.press("s")
            await pilot.pause()
            text = "\n".join(_row_text(r) for r in _rows(panel))
            assert "mine01" in text
            assert "other1" not in text
            assert panel.scope == SCOPE_SESSION

    async def test_history_section_renders_resolved_entries(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        (data_dir / HISTORY_FILENAME).write_text(
            json.dumps(
                [
                    {
                        "id": "old111",
                        "tool_names": ["srv.wipe"],
                        "outcome": "denied",
                        "resolved_at": 1000.0,
                        "chat_session_id": None,
                    }
                ]
            ),
            encoding="utf-8",
        )
        app = JarvisTUI()
        async with app.run_test() as pilot:
            panel = await _open_panel(app, pilot)
            text = "\n".join(_row_text(r) for r in _rows(panel))
            assert "History" in text
            assert "old111" in text
            assert "srv.wipe" in text

    async def test_empty_store_says_so(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        app = JarvisTUI()
        async with app.run_test() as pilot:
            panel = await _open_panel(app, pilot)
            text = "\n".join(_row_text(r) for r in _rows(panel))
            assert "nothing waiting" in text
            assert "nothing resolved yet" in text


@pytest.mark.unit
class TestPanelVisibility:
    async def test_f3_toggles_and_focuses(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        app = JarvisTUI()
        async with app.run_test() as pilot:
            panel = app.query_one("#confirmations-panel", ConfirmationsPanel)
            assert panel.display is False

            app.action_confirmations()
            await pilot.pause()
            assert panel.display is True
            assert app.focused is not None
            assert app.focused.id == "confirmations-list"

            app.action_confirmations()
            await pilot.pause()
            assert panel.display is False


@pytest.mark.unit
class TestOfflineStatusLine:
    async def test_status_names_the_offline_state_and_the_count(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        _write_store(data_dir, [_entry("abc123", ["srv.t0: run"])])
        app = JarvisTUI()
        async with app.run_test():
            app.engine_error = "contextor binary not found"
            app._update_status()
            assert "engine offline" in app.status_text
            assert "1 pending" in app.status_text

    async def test_sidebar_says_sessions_are_hidden_rather_than_absent(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        app = JarvisTUI()
        async with app.run_test() as pilot:
            await app._refresh_sidebar()
            await pilot.pause()

            from textual.widgets import ListView

            rows = list(app.query_one("#session-list", ListView).children)
            assert any("no engine" in _row_text(r) for r in rows)

    async def test_typing_while_offline_explains_instead_of_stalling(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        app = JarvisTUI()
        async with app.run_test() as pilot:
            app.query_one("#input").value = "hello"
            await pilot.press("enter")
            await pilot.pause()

            transcript = "\n".join(app._export_lines)
            assert "chat is unavailable" in transcript.lower()
            assert "no engine (test)" in transcript
            assert "still starting up" not in transcript


@pytest.mark.unit
class TestPanelWithAnEngine:
    """With an engine the panel reads the in-process manager, no socket."""

    async def test_decision_goes_through_apply_confirmation_decision(self, data_dir):
        from jarvis.tui.app import JarvisTUI
        from jarvis.tui.confirmations_panel import submit_decision

        applied = []

        class _Events:
            def inject_confirmation_response(self, msg):
                applied.append(msg)

        class _Manager:
            def list_pending(self):
                return [
                    {
                        "id": "abc123",
                        "tool_names": ["srv.t0"],
                        "tool_lines": ["srv.t0: run"],
                        "created_at": 1000.0,
                        "session_id": None,
                        "chat_session_id": None,
                    }
                ]

            def list_history(self):
                return []

        class _Engine:
            confirmation = _Manager()
            events = _Events()

        app = JarvisTUI()
        app.jarvis = _Engine()

        ack = submit_decision(app, {"type": "approve_confirmation", "id": "abc123"})

        assert applied == [
            {"type": "confirmation_response", "id": "abc123", "approved": True}
        ]
        assert "abc123" in ack
        # Nothing was queued: the engine applied it directly.
        assert not (data_dir / DECISION_QUEUE_FILENAME).exists()

    async def test_panel_reads_the_in_process_manager(self, data_dir):
        from jarvis.tui.app import JarvisTUI

        # A store file that must NOT be the source when an engine is present.
        _write_store(data_dir, [_entry("fromdisk", ["srv.t0: stale"])])

        class _Manager:
            def list_pending(self):
                return [
                    {
                        "id": "live01",
                        "tool_names": ["srv.t0"],
                        "tool_lines": ["srv.t0: live"],
                        "created_at": 1000.0,
                        "session_id": None,
                        "chat_session_id": None,
                    }
                ]

            def list_history(self):
                return []

        class _Sessions:
            current_id = None
            current = None

        class _Engine:
            confirmation = _Manager()
            sessions = _Sessions()

        app = JarvisTUI()
        async with app.run_test() as pilot:
            app.jarvis = _Engine()
            panel = await _open_panel(app, pilot)
            text = "\n".join(_row_text(r) for r in _rows(panel))
            assert "live01" in text
            assert "fromdisk" not in text
