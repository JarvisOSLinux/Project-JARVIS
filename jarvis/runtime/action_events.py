"""What JARVIS does, as events a front end can draw as blocks (#271).

Each action starts and ends; a long tool run may report output in between.
Events are plain dicts, meant for the TUI and for socket clients alike (#267):

    {"type": "action", "phase": "start", "id": "tool:4211", "kind": "tool",
     "title": "execute_command (shell)", "detail": "pacman -Syu", "at": …}
    {"type": "action", "phase": "update", "id": "tool:4211", "output": "…", "at": …}
    {"type": "action", "phase": "end", "id": "tool:4211", "status": "ok",
     "output": "…", "at": …}

Statuses at the end: ok, failed, stopped, waiting (a setup the user has to
fill in), approved, denied. Only an action that started can update or end, so
a timer's signal, or a confirmation restored after a restart, shows nothing.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

# A block shows the end of a long output: a package upgrade's last lines say
# whether it worked.
MAX_OUTPUT_CHARS = 20_000
MAX_DETAIL_CHARS = 2_000

_STATUS_OF_SIGNAL = {"KILL": "stopped", "TIMEOUT": "failed"}
_CODE = re.compile(r"^\s*(\d{3})\b\s*")
_HASH = re.compile(r"\[hash=([0-9a-fA-F]+)\]\s*")


def _open(app: Any) -> Dict[str, Dict[str, Any]]:
    actions = getattr(app, "_open_actions", None)
    if actions is None:
        actions = app._open_actions = {}
    return actions


def _emit(app: Any, event: Dict[str, Any]) -> None:
    manager = getattr(app, "output_manager", None)
    emit = getattr(manager, "emit_action", None)
    if emit is not None:
        emit(event)


def clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """The end of `text`, saying how much was left out."""
    if len(text) <= limit:
        return text
    return f"… ({len(text) - limit:,} earlier characters not shown)\n" + text[-limit:]


def start(
    app: Any,
    kind: str,
    title: str,
    detail: str = "",
    action_id: Optional[str] = None,
) -> str:
    """Announce an action; returns its id for `update` and `end`."""
    action_id = action_id or f"{kind}:{uuid.uuid4().hex[:8]}"
    record = {
        "id": action_id,
        "kind": kind,
        "title": title,
        "detail": clip(detail, MAX_DETAIL_CHARS),
        "at": time.time(),
    }
    _open(app)[action_id] = record
    _emit(app, {"type": "action", "phase": "start", **record})
    return action_id


def update(app: Any, action_id: str, output: str) -> None:
    """Output so far from an action that is still running."""
    if action_id not in _open(app) or not output:
        return
    _emit(
        app,
        {
            "type": "action",
            "phase": "update",
            "id": action_id,
            "output": clip(output),
            "at": time.time(),
        },
    )


def end(app: Any, action_id: str, status: str = "ok", output: str = "") -> None:
    """Close an action with its outcome."""
    record = _open(app).pop(action_id, None)
    if record is None:
        return
    _emit(
        app,
        {
            "type": "action",
            "phase": "end",
            "id": action_id,
            "kind": record["kind"],
            "status": status,
            "output": clip(output),
            "at": time.time(),
            "started_at": record["at"],
        },
    )


def running(app: Any) -> List[Dict[str, Any]]:
    """Actions that have started and not ended, oldest first."""
    return sorted(_open(app).values(), key=lambda r: r["at"])


def short_server(server_id: Any) -> str:
    """The readable end of a server id: com.github.x.jarvis-shell-mcp -> jarvis-shell-mcp."""
    return str(server_id or "?").rsplit(".", 1)[-1]


def tool_title(task: Any) -> str:
    """`execute_command (jarvis-shell-mcp)` for a task dict."""
    if not isinstance(task, dict):
        return "?"
    return f"{task.get('tool', '?')} ({short_server(task.get('server'))})"


def readable(text: str) -> tuple:
    """(text, failed) for a tool's output: a shell result as what it printed.

    Shell servers answer with JSON ({"stdout", "stderr", "exit_code"}), so a
    package upgrade read as one line of escaped newlines. A non-zero exit or
    `success: false` is a failure even though the call itself succeeded.
    """
    stripped = text.strip()
    # A failed call arrives as "Error: dmcp invocation failed: {…}".
    start = stripped.find("{")
    if start == -1:
        return text, False
    try:
        data, _ = json.JSONDecoder().raw_decode(stripped[start:])
    except ValueError:
        return text, False
    if not isinstance(data, dict) or not {"stdout", "stderr"} & data.keys():
        return text, False
    code = data.get("exit_code")
    parts = [
        stripped[:start].strip(),
        str(data.get("stdout") or "").rstrip(),
        str(data.get("stderr") or "").rstrip(),
        f"(exit code {code})" if code not in (None, 0) else "",
    ]
    failed = data.get("success") is False or code not in (None, 0)
    return "\n".join(p for p in parts if p), failed


def tool_detail(params: Any) -> str:
    """A tool's parameters as one readable line: a shell command or script as typed."""
    if not isinstance(params, dict) or not params:
        return ""
    options = []
    if params.get("cwd"):
        options.append(f"in {params['cwd']}")
    if params.get("timeout"):
        options.append(f"timeout {params['timeout']}s")
    suffix = f"  ({', '.join(options)})" if options else ""
    command = params.get("command")
    if isinstance(command, str) and set(params) <= {
        "command",
        "args",
        "cwd",
        "timeout",
    }:
        args = params.get("args") or []
        if isinstance(args, list):
            return " ".join([command, *(str(a) for a in args)]) + suffix
    script = params.get("script")
    if isinstance(script, str) and set(params) <= {"script", "cwd", "timeout"}:
        return script.strip() + suffix
    try:
        return json.dumps(params, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(params)


def tools_started(app: Any, pids: list, tasks: list) -> None:
    """One tool block per dispatched task.

    Paired like dispatch_flow.note_dispatched_tools: this batch's INITs are
    the newest, so the trailing len(tasks) PIDs belong to these tasks in order.
    """
    if not pids or not tasks or len(pids) < len(tasks):
        return
    for pid, task in zip(pids[-len(tasks) :], tasks):
        if not isinstance(task, dict):
            continue
        start(
            app,
            "tool",
            tool_title(task),
            tool_detail(task.get("params")),
            action_id=f"tool:{pid}",
        )


def signal_output(signal: Dict[str, Any]) -> tuple:
    """(status code or None, readable text) from a dispatch signal's body.

    dispatch writes "[hash=H] 200 <H>output</H>" on EXIT and "Running for 30s
    [hash=H] <H>tail</H>" on REMIND; the wrapper is for the model, not for the
    reader. Output whose wrapper failed verification keeps its warning mark.
    """
    body = signal.get("data")
    if not isinstance(body, str):
        body = signal.get("message")
    if not isinstance(body, str):
        return None, ""
    if signal.get("_boundary_verified") is False:
        return None, body.strip()
    found = _HASH.search(body)
    code = None
    if found:
        nonce = found.group(1)
        rest = body[found.end() :]
        coded = _CODE.match(rest)
        if coded:
            code, rest = coded.group(1), rest[coded.end() :]
        opened, closed = rest.find(f"<{nonce}>"), rest.rfind(f"</{nonce}>")
        if opened != -1 and closed > opened:
            return code, rest[opened + len(nonce) + 2 : closed].strip()
        return code, rest.strip()
    coded = _CODE.match(body)
    if coded:
        return coded.group(1), body[coded.end() :].strip()
    return None, body.strip()


def on_signal(app: Any, signal: Dict[str, Any], exit_data: Any = None) -> None:
    """Close or update the tool block a dispatch signal belongs to."""
    if isinstance(exit_data, dict):
        signal = exit_data
    kind = str(signal.get("type") or "").upper()
    action_id = f"tool:{signal.get('pid')}"
    if kind == "REMIND":
        update(app, action_id, signal_output(signal)[1])
        return
    if kind not in ("EXIT", "KILL", "TIMEOUT"):
        return
    code, text = signal_output(signal)
    text, failed = readable(text)
    status = _STATUS_OF_SIGNAL.get(kind) or (
        "ok" if code == "200" and not failed else "failed"
    )
    end(app, action_id, status, text)
