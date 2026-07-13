"""TLA confirmation-gate tests (#185, #186, #187).

Covers the human-in-the-loop boundary hardening found during testing:
- #186 the prompt must show the actual command/params, not just tool names
- #187 per-task allow/deny for batched confirmations
- #185 no auto-deny when the user simply hasn't answered yet
"""

import asyncio

import pytest

import jarvis.platform as platform_mod
from jarvis.core.confirmation_manager import (
    DEFAULT_TIMEOUT,
    ConfirmationManager,
    PendingConfirmation,
    confirmation_line,
    describe_tool_call,
)


def _cmd_detail(tool_name, command, args=None, **params):
    p = {"command": command}
    if args is not None:
        p["args"] = args
    p.update(params)
    return {
        "tool_name": tool_name,
        "task": {
            "server": tool_name.split(".")[0],
            "tool": tool_name.split(".")[-1],
            "params": p,
        },
        "params": p,
    }


# --- #186: command/params are rendered ---------------------------------------


def test_describe_execute_command_shows_full_command():
    d = _cmd_detail("sys.execute_command", "pacman", ["-Syu", "--noconfirm"])
    assert describe_tool_call(d) == "pacman -Syu --noconfirm"


def test_describe_includes_cwd_and_timeout():
    d = _cmd_detail("sys.execute_command", "git", ["status"], cwd="/tmp", timeout=30)
    rendered = describe_tool_call(d)
    assert "git status" in rendered
    assert "cwd=/tmp" in rendered
    assert "timeout=30s" in rendered


def test_describe_script_is_flattened():
    d = {"tool_name": "sh.execute_script", "params": {"script": "echo hi\nls -la"}}
    assert describe_tool_call(d) == "echo hi ; ls -la"


def test_describe_generic_params_as_key_value():
    d = {"tool_name": "x.write_file", "params": {"path": "/etc/hosts", "content": "x"}}
    rendered = describe_tool_call(d)
    assert "path=/etc/hosts" in rendered
    assert "content=x" in rendered


def test_describe_long_command_is_truncated():
    d = _cmd_detail("sys.execute_command", "echo", ["a" * 500])
    rendered = describe_tool_call(d)
    assert len(rendered) <= 201
    assert rendered.endswith("…")


def test_confirmation_line_prefixes_tool_name():
    d = _cmd_detail("sys.execute_command", "pacman", ["-Syu"])
    assert confirmation_line(d) == "sys.execute_command: pacman -Syu"


def test_request_stores_rendered_lines_for_list_pending():
    import asyncio

    mgr = ConfirmationManager()
    detail = _cmd_detail("sys.execute_command", "pacman", ["-Syu", "--noconfirm"])
    task = detail["task"]

    async def run():
        # No channels registered -> stays pending, reviewable.
        await mgr.request_confirmation(
            request_id="abc",
            tasks=[task],
            tools_needing_confirmation=[detail],
            approved_tasks=[],
            denied_tools=[],
            timeout=0,
        )

    asyncio.run(run())
    pend = mgr.list_pending()
    assert len(pend) == 1
    assert pend[0]["tool_lines"] == ["sys.execute_command: pacman -Syu --noconfirm"]


# --- #185: no auto-deny; dismiss != deny -------------------------------------


def test_default_timeout_is_no_auto_deny():
    assert DEFAULT_TIMEOUT == 0


def test_request_with_zero_timeout_creates_no_auto_deny_task():
    mgr = ConfirmationManager()
    detail = _cmd_detail("sys.execute_command", "pacman", ["-Syu"])

    async def run():
        await mgr.request_confirmation(
            request_id="t0",
            tasks=[detail["task"]],
            tools_needing_confirmation=[detail],
            approved_tasks=[],
            denied_tools=[],
            timeout=0,
        )

    asyncio.run(run())
    # Still pending, and no timeout task scheduled to auto-deny it.
    assert mgr.has_pending("t0")
    assert "t0" not in mgr._timeout_tasks


def _desktop_notify(mgr, action, tool_lines=("sys.execute_command: pacman -Syu",)):
    """Drive _notify_desktop with a stubbed platform action, capturing injects."""
    injected = []
    mgr.set_event_injector(injected.append)

    async def fake_send(title, body, timeout_ms):
        return action

    orig = platform_mod.current.send_desktop_notification
    platform_mod.current.send_desktop_notification = fake_send
    try:
        asyncio.run(mgr._notify_desktop("d1", list(tool_lines), 0))
    finally:
        platform_mod.current.send_desktop_notification = orig
    return injected


def test_desktop_dismiss_leaves_pending_not_denied():
    # action=None means dismissed/expired — must NOT inject a deny (#185).
    injected = _desktop_notify(ConfirmationManager(), None)
    assert injected == []


def test_desktop_explicit_deny_injects_deny():
    injected = _desktop_notify(ConfirmationManager(), "deny")
    assert injected == [
        {"type": "confirmation_response", "id": "d1", "approved": False}
    ]


def test_desktop_allow_injects_approve():
    injected = _desktop_notify(ConfirmationManager(), "allow")
    assert injected == [{"type": "confirmation_response", "id": "d1", "approved": True}]
