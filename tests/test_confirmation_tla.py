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


# --- #187: per-task allow/deny -----------------------------------------------


def _setup_batch(mgr, req_id="b1", n=2, pre_approved=None):
    """Seed a pending confirmation directly (no channel dispatch)."""
    details = [_cmd_detail(f"s.tool{i}", "cmd", [str(i)]) for i in range(n)]
    tasks = [d["task"] for d in details]
    pre = list(pre_approved or [])
    mgr._pending[req_id] = PendingConfirmation(
        request_id=req_id,
        tasks=pre + tasks,
        approved_tasks=list(pre),
        denied_tools=[],
        confirm_details=details,
        tool_names=[d["tool_name"] for d in details],
    )
    return details, tasks


def test_resolve_per_task_approves_subset_denies_rest():
    mgr = ConfirmationManager()
    _, tasks = _setup_batch(mgr, "b1", n=2)
    pending = mgr.resolve({"id": "b1", "approved_indices": [0]})
    assert pending.approved_tasks == [tasks[0]]
    assert pending.denied_tools == ["s.tool1"]


def test_resolve_per_task_empty_denies_all():
    mgr = ConfirmationManager()
    _setup_batch(mgr, "b2", n=2)
    pending = mgr.resolve({"id": "b2", "approved_indices": []})
    assert pending.approved_tasks == []
    assert set(pending.denied_tools) == {"s.tool0", "s.tool1"}


def test_resolve_per_task_preserves_preapproved():
    mgr = ConfirmationManager()
    pre = [{"server": "safe", "tool": "noop", "params": {}}]
    _, tasks = _setup_batch(mgr, "b3", n=2, pre_approved=pre)
    pending = mgr.resolve({"id": "b3", "approved_indices": [1]})
    assert pending.approved_tasks == pre + [tasks[1]]
    assert pending.denied_tools == ["s.tool0"]


def test_resolve_fallback_all_or_nothing_without_indices():
    mgr = ConfirmationManager()
    _, tasks = _setup_batch(mgr, "b4", n=2)
    pending = mgr.resolve({"id": "b4", "approved": True})
    assert pending.approved_tasks == tasks
    assert pending.denied_tools == []


def _desktop_batch(mgr, actions, req_id="d2", n=2):
    injected = []
    mgr.set_event_injector(injected.append)
    it = iter(actions)

    async def fake_send(title, body, timeout_ms):
        return next(it)

    lines = [f"s.tool{i}: cmd {i}" for i in range(n)]
    orig = platform_mod.current.send_desktop_notification
    platform_mod.current.send_desktop_notification = fake_send
    try:
        asyncio.run(mgr._notify_desktop(req_id, lines, 0))
    finally:
        platform_mod.current.send_desktop_notification = orig
    return injected


def test_desktop_per_task_aggregates_indices():
    injected = _desktop_batch(ConfirmationManager(), ["allow", "deny"])
    assert injected == [
        {"type": "confirmation_response", "id": "d2", "approved_indices": [0]}
    ]


def test_desktop_per_task_dismiss_midbatch_leaves_pending():
    # First allowed, second dismissed -> don't resolve the batch on a partial
    # answer; leave it pending (#185 + #187).
    injected = _desktop_batch(ConfirmationManager(), ["allow", None])
    assert injected == []
