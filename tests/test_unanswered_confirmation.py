"""A request nobody answered is not one the user refused.

Session 43940ebc (2026-11-20): Yakup's jarvis.conf, seeded from .env.example
before #224, still held that template's CONFIRMATION_TIMEOUT=30, which the
header-less reading took for his choice. Two requests went unanswered for 30 s
and were denied; the block read "✗ denied" (one in a mixed batch read
"✓ approved"), and the model said "I was denied access".
"""

import asyncio
import logging
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jarvis.config import SAVED_SETTINGS_HEADER, chosen_before_header
from jarvis.core.confirmation_manager import ConfirmationManager, PendingConfirmation
from jarvis.core.output_manager import OutputManager
from jarvis.runtime import action_events, root_handlers

_LOG = logging.getLogger("test")

_SAFE = {"server": "m365", "tool": "list-calendars", "params": {}}
_ASKED = {"server": "m365", "tool": "get-mailbox-settings", "params": {}}


def _pending(response):
    """A batch of one tool that needed no approval and one that was asked about,
    resolved by ``response``."""
    mgr = ConfirmationManager()
    mgr._pending["r1"] = PendingConfirmation(
        request_id="r1",
        tasks=[_SAFE, _ASKED],
        approved_tasks=[_SAFE],
        confirm_details=[{"tool_name": "m365.get-mailbox-settings", "task": _ASKED}],
    )
    return mgr.resolve({"id": "r1", **response})


@pytest.mark.unit
class TestTheTimeout:
    # Its auto-deny carries the wait: test_confirmation_pending.py.

    def test_resolve_keeps_it(self):
        assert (
            _pending({"approved": False, "unanswered_after": 30}).unanswered_after == 30
        )

    def test_a_refusal_is_not_a_timeout(self):
        assert _pending({"approved": False}).unanswered_after is None

    def test_an_approval_is_never_unanswered(self):
        assert (
            _pending({"approved": True, "unanswered_after": 30}).unanswered_after
            is None
        )


@pytest.mark.unit
class TestTheBlock:
    def test_a_timeout_reads_no_answer(self):
        status, detail = root_handlers._confirm_outcome(
            _pending({"approved": False, "unanswered_after": 30})
        )
        assert status == "unanswered"
        assert detail == "No answer within 30 s: m365.get-mailbox-settings"

    def test_a_refusal_beside_an_unasked_tool_reads_denied(self):
        # The first get-calendar-view in 43940ebc showed "✓ approved".
        assert root_handlers._confirm_outcome(_pending({"approved": False})) == (
            "denied",
            "",
        )

    def test_an_approval_reads_approved(self):
        assert root_handlers._confirm_outcome(_pending({"approved": True})) == (
            "approved",
            "",
        )

    def test_a_partial_approval_names_what_was_denied(self):
        other = {"server": "m365", "tool": "list-mail-messages", "params": {}}
        mgr = ConfirmationManager()
        mgr._pending["r2"] = PendingConfirmation(
            request_id="r2",
            tasks=[_ASKED, other],
            approved_tasks=[],
            confirm_details=[
                {"tool_name": "m365.get-mailbox-settings", "task": _ASKED},
                {"tool_name": "m365.list-mail-messages", "task": other},
            ],
        )
        pending = mgr.resolve({"id": "r2", "approved_indices": [1]})
        assert root_handlers._confirm_outcome(pending) == (
            "approved",
            "Denied: m365.get-mailbox-settings",
        )

    def test_the_tui_draws_it(self):
        pytest.importorskip("textual")
        from jarvis.tui.chat_view import _MARKS

        assert "no answer" in _MARKS["unanswered"]


@pytest.mark.unit
class TestWhatTheModelIsTold:
    def _context(self, response):
        pending = _pending(response)
        pending.approved_tasks = []
        manager = OutputManager(suppress_stdout=True)
        events = []
        manager.add_action_callback(events.append)
        app = SimpleNamespace(
            output_manager=manager,
            llm=Mock(),
            _act_on_root_response=AsyncMock(),
        )
        action_events.start(app, "confirm", "Confirm", action_id="confirm:r1")
        ask = AsyncMock(return_value={})
        with (
            patch.object(root_handlers, "build_root_context", return_value="CTX"),
            patch.object(root_handlers, "ask_llm", ask),
        ):
            asyncio.run(root_handlers._resume_after_confirmation(app, _LOG, pending))
        return ask.call_args.args[2], events[-1]

    def test_a_timeout_is_no_answer(self):
        context, block = self._context({"approved": False, "unanswered_after": 30})
        assert "NO_ANSWER:" in context and "USER_DENIAL" not in context
        assert "did not refuse it" in context and "30 s" in context
        assert block["status"] == "unanswered"

    def test_a_refusal_is_still_a_denial(self):
        context, block = self._context({"approved": False})
        assert "USER_DENIAL: Action m365.get-mailbox-settings was denied" in context
        assert block["status"] == "denied"


@pytest.mark.unit
class TestTheOldTemplateDefault:
    def test_an_earlier_default_is_nobodys_choice(self):
        template = {"CONFIRMATION_TIMEOUT": "0"}
        assert not chosen_before_header("CONFIRMATION_TIMEOUT", "30", template)
        assert not chosen_before_header("CONFIRMATION_TIMEOUT", "0", template)
        assert chosen_before_header("CONFIRMATION_TIMEOUT", "120", template)
        assert not chosen_before_header("RESET_HISTORY_AFTER_RESPONSE", "true", {})

    def test_a_rewrite_drops_it(self, tmp_path, monkeypatch):
        import jarvis.cli as cli
        from jarvis.config import Config

        conf = tmp_path / "jarvis.conf"
        conf.write_text("CONFIRMATION_TIMEOUT=30\nWAKE_CHIME_PATH=/x/chime.wav\n")
        monkeypatch.setattr(cli, "ENV_FILE", conf)
        monkeypatch.setattr(cli, "_jarvis_config_dir", None)
        monkeypatch.setattr(Config, "OUTPUT_MODE", Config.OUTPUT_MODE)
        monkeypatch.setenv("OUTPUT_MODE", Config.OUTPUT_MODE)
        cli._update_env_setting("OUTPUT_MODE", "text")
        assert conf.read_text() == (
            f"{SAVED_SETTINGS_HEADER}\nWAKE_CHIME_PATH=/x/chime.wav\nOUTPUT_MODE=text\n"
        )

    @pytest.mark.parametrize(
        "header, expected", [(False, "0"), (True, "30")], ids=["seeded", "saved"]
    )
    def test_read_at_start(self, tmp_path, header, expected):
        """Read as JARVIS starts: the old copy's 30 gives way to jarvis/.env's
        or the default; a 30 saved under the header is a choice and holds."""
        conf = tmp_path / "jarvis" / "jarvis.conf"
        conf.parent.mkdir()
        conf.write_text(
            (f"{SAVED_SETTINGS_HEADER}\n" if header else "")
            + "CONFIRMATION_TIMEOUT=30\n"
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("JARVIS_CONFIG_DIR", "CONFIRMATION_TIMEOUT")
        }
        env["XDG_CONFIG_HOME"] = str(tmp_path)
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os, jarvis.config;"
                "print(os.environ.get('CONFIRMATION_TIMEOUT', '0'))",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert out.stdout.strip() == expected
