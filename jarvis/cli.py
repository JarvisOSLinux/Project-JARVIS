"""
CLI interface for JARVIS AI Assistant

Usage:
    jarvis                    # Start dual-input mode (voice + socket, or chat)
    jarvis run                # Same as jarvis — event loop with voice + socket
    jarvis tui                # Interactive TUI (OpenClaw-style, needs [tui] extra)
    jarvis chat               # Interactive text chat (stdin only)
    jarvis send "<message>"   # Send message to running jarvis (via socket)
    jarvis ask "<message>"    # Ask a single question (one-shot, no daemon)
    jarvis text               # Set text output mode
    jarvis voice              # Set voice output mode
    jarvis output-type        # Show current output mode
"""

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

from .config import Config
from .core.logger import JarvisLogger, get_logger
from .core.providers import (
    add_provider,
    edit_provider,
    list_providers,
    move_provider,
    parse_flags,
    remove_provider,
)

# Initialise logging from config before any get_logger() call so that the
# file handler (LOG_FILE) and level (LOG_LEVEL) are applied from the start.
# Without this, the first get_logger() call would call setup() with defaults
# (no log_file), set _initialized = True, and all subsequent setup() calls
# would return early — leaving LOG_FILE silently ignored.
JarvisLogger.setup(
    log_level=Config.LOG_LEVEL,
    log_file=Config.LOG_FILE or None,
    enable_colors=Config.LOG_COLORS,
)

logger = get_logger(__name__)

_jarvis_config_dir = os.environ.get("JARVIS_CONFIG_DIR")
if _jarvis_config_dir:
    ENV_FILE = Path(_jarvis_config_dir) / "jarvis.conf"
else:
    from .platform import current as _platform

    ENV_FILE = _platform.config_dir() / "jarvis.conf"


def _find_ipc_endpoint(quiet: bool = False) -> "str | None":
    """Locate and ownership-verify a running JARVIS instance's input socket.

    Prints its own error and returns None on failure so callers can decide
    whether to exit. ``quiet`` suppresses only the not-found message, for
    callers with a working offline fallback for a stopped daemon; a failed
    ownership check stays loud either way, because that is a security signal
    rather than an absent daemon.
    """
    from .platform import current as platform

    candidates = [Config.JARVIS_INPUT_SOCKET, *platform.system_ipc_candidates()]
    path = None
    for p in candidates:
        if p and _ipc_endpoint_exists(p):
            path = p
            break
    if not path:
        if not quiet:
            print("Error: JARVIS IPC endpoint not found.")
            print("  Tried:", ", ".join(p for p in candidates if p))
            print("  Is JARVIS running? Start with 'jarvis' or 'jarvis run'.")
        return None
    if not platform.ipc_verify_owner(path):
        print("Error: IPC endpoint ownership check failed.")
        return None
    return path


def _cmd_send() -> None:
    """Send a message to a running JARVIS instance via IPC."""
    from .platform import current as platform

    if len(sys.argv) < 3:
        print("Usage: jarvis send <message>")
        print("  Sends the message to a running JARVIS instance.")
        print("  Start JARVIS with 'jarvis' or 'jarvis run' first.")
        sys.exit(1)
    msg = " ".join(sys.argv[2:]).strip()
    if not msg:
        print("Error: Message cannot be empty")
        sys.exit(1)
    path = _find_ipc_endpoint()
    if not path:
        sys.exit(1)
    try:
        sock = platform.ipc_connect(path)
        try:
            sock.sendall((msg + "\n").encode("utf-8"))
        finally:
            sock.close()
        print("Sent.")
    except (socket.error, OSError) as e:
        print(f"Error: Could not send to JARVIS: {e}")
        sys.exit(1)


def _ipc_endpoint_exists(path: str) -> bool:
    """Check if an IPC endpoint exists (socket file or port file)."""
    return os.path.exists(path) or os.path.exists(path + ".port")


def _format_age(created_at: float) -> str:
    """Render a confirmation's age as e.g. '40s ago', '5m ago', '2h ago'."""
    import time

    delta = max(0, time.time() - created_at)
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    return f"{int(delta // 3600)}h ago"


OFFLINE_NOTICE = "daemon offline — decisions queue and apply at next start"
SETUPS_OFFLINE_NOTICE = (
    "daemon offline — read from disk; what you do applies at its next start"
)


def _ipc_request(request: dict) -> "dict | None":
    """Send one request to the running daemon; None when it cannot be reached."""
    from .platform import current as platform

    path = _find_ipc_endpoint(quiet=True)
    if not path:
        return None
    try:
        sock = platform.ipc_connect(path)
        try:
            sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
            sock.settimeout(5.0)
            line = sock.makefile().readline()
        finally:
            sock.close()
    except (socket.error, OSError):
        return None
    try:
        return json.loads(line) if line else None
    except json.JSONDecodeError:
        return None


def _cmd_setups() -> None:
    """List, fill in or cancel pending server setups (#254)."""
    args = sys.argv[2:]
    if not args or args == ["list"]:
        setups, live = _load_setups()
        _print_setups(setups, live)
    elif args[0] == "fill" and len(args) <= 2:
        _setups_fill(args[1] if len(args) == 2 else None)
    elif args[0] == "cancel" and len(args) <= 2:
        _setups_cancel(args[1] if len(args) == 2 else None)
    elif args[0] == "edit" and len(args) == 2:
        _setups_edit(args[1])
    else:
        _show_setups_usage()
        sys.exit(1)


def _load_setups() -> "tuple[list, bool]":
    """(pending setups, whether the daemon answered). Falls back to the store."""
    response = _ipc_request({"type": "list_setups"})
    if response and response.get("type") == "setup_list":
        return response.get("setups", []), True
    from .core.setup_queue import read_store, store_path

    path = store_path()
    if not path.exists():
        return [], False
    try:
        return [s.to_dict() for s in read_store(path)], False
    except Exception as e:
        print(f"Error: could not read {path}: {e}")
        sys.exit(1)


def _print_setups(setups: list, live: bool) -> None:
    ordered = _ordered_setups(setups)
    if not ordered:
        print("No server is waiting for setup.")
    else:
        print("Servers waiting for setup:")
        for number, setup in enumerate(ordered, 1):
            print(
                f"  {number}. {setup['server_name']} ({setup['server_id']})"
                f"  [{setup['setup_id']}]"
            )
            print(f"       {_setup_needs(setup)}")
            if setup.get("goal_description"):
                print(f"       for: {setup['goal_description']}")
        hint = (
            "jarvis setups fill" if len(ordered) == 1 else "jarvis setups fill <number>"
        )
        print(f"\nFill in with: {hint}")
    if not live:
        print(f"({SETUPS_OFFLINE_NOTICE})")


def _setup_needs(setup: dict) -> str:
    """What a pending setup is waiting for, in one line."""
    if setup.get("kind") == "configure":
        keys = [f["key"] for f in setup.get("fields") or []]
        return f"change: {', '.join(keys)} (asked for by JARVIS)"
    return f"needs: {', '.join(setup.get('missing') or [])}"


def _ordered_setups(setups: list) -> list:
    """Oldest first, so a setup added later never renumbers one already shown."""
    return sorted(setups, key=lambda s: s.get("created_at") or 0)


def _resolve_setup(setups: list, ref: "str | None") -> dict:
    """The setup `ref` names, or exit with why not.

    `ref` may be the number `jarvis setups` shows, the id, the server id, a
    unique start of the id, or part of the server's id or name. With exactly
    one setup pending it may be left out.
    """
    ordered = _ordered_setups(setups)
    if not ordered:
        print("No server is waiting for setup.")
        sys.exit(1)
    if ref is None:
        if len(ordered) == 1:
            return ordered[0]
        print("More than one server is waiting for setup; say which:")
        _print_setups(ordered, True)
        sys.exit(1)
    if ref.isdigit():
        number = int(ref)
        if 1 <= number <= len(ordered):
            return ordered[number - 1]
        print(f"Error: there is no setup number {number}. Run 'jarvis setups'.")
        sys.exit(1)
    for setup in ordered:
        if ref in (setup.get("setup_id"), setup.get("server_id")):
            return setup
    low = ref.lower()
    matches = [
        s
        for s in ordered
        if str(s.get("setup_id", "")).startswith(low)
        or low in str(s.get("server_id", "")).lower()
        or low in str(s.get("server_name", "")).lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = ", ".join(s["server_id"] for s in matches)
        print(f"Error: '{ref}' matches more than one setup: {names}.")
    else:
        print(
            f"Error: No pending setup '{ref}'. Run 'jarvis setups' to see what is waiting."
        )
    sys.exit(1)


def _prompt_setup_values(spec, ask=input, ask_secret=None) -> "dict | None":
    """Ask for each setting in the terminal; None if the user declines to save.

    The plain-prompt version of the setup form, with the same rules: secrets
    are read without echo, Enter keeps what a field already holds, and a
    setting the server cannot run without is asked again.
    """
    import getpass

    from .core.setup_queue import as_setting_value, boolean_spellings, value_error

    ask_secret = ask_secret or getpass.getpass
    if spec.kind == "configure":
        print(f"\nChange settings for {spec.server_name} ({spec.server_id})")
        print("  JARVIS asked to change these settings. Check each one before saving.")
    elif spec.kind == "edit":
        print(f"\nSettings for {spec.server_name} ({spec.server_id})")
    else:
        print(f"\nSet up {spec.server_name} ({spec.server_id})")
        if spec.summary:
            print(f"  {spec.summary}")
    print("  Enter keeps what is shown, or leaves an optional setting unset.\n")

    values: dict = {}
    for field in spec.fields:
        key = field["key"]
        sensitive = bool(field.get("sensitive"))
        kind = "string" if sensitive else (field.get("type") or "string")
        needed = key in spec.missing
        baseline = (
            "" if sensitive else spec.current.get(key, str(field.get("default") or ""))
        )
        if kind == "boolean":
            # Unset reads as off, as on the form's switch: Enter writes nothing.
            no = boolean_spellings(field)[1]
            baseline = as_setting_value(field, baseline or no) or baseline
        suggested = None if sensitive else spec.proposed.get(key)
        initial = suggested if suggested is not None else baseline
        if field.get("description"):
            print(f"  {field['description']}")
        hint = _type_hint(field, kind)
        if hint:
            print(f"  {hint}")
        if needed:
            note = " (required)"
        elif suggested is not None:
            note = " (suggested by JARVIS; Enter accepts it)"
        elif field.get("required") or key in spec.secrets_set:
            note = " (already set; Enter keeps it)"
        else:
            note = ""
        shown = _shown_setting(field, kind, initial)
        prompt = f"{field.get('label') or key}{note}{f' [{shown}]' if shown else ''}: "
        while True:
            value = (ask_secret if sensitive else ask)(prompt).strip()
            if needed or suggested is not None or kind == "boolean":
                value = value or initial
            if value and kind == "boolean":
                value = as_setting_value(field, value) or value
            why = value_error(field, value) if value and not sensitive else None
            if why:
                print(f"  {field.get('label') or key} {why}.")
                continue
            if value or not needed:
                break
            print("  The server cannot run without this one.")
        if value and (needed or value != baseline):
            values[key] = value
        print()

    print("Saving:")
    for field in spec.fields:
        if field["key"] in values:
            kind = field.get("type") or "string"
            shown = (
                "•" * 8
                if field.get("sensitive")
                else _shown_setting(field, kind, values[field["key"]])
            )
            print(f"  {field.get('label') or field['key']}: {shown}")
    if not values:
        print("  (nothing)")
    answer = ask("Save these settings? [Y/n] ").strip().lower()
    return None if answer in ("n", "no") else values


def _type_hint(field: dict, kind: str) -> str:
    """What the plain prompt accepts for a typed setting (mcp-registry#115)."""
    if kind == "boolean":
        return "Answer on or off."
    if kind == "choice" and field.get("multiple"):
        separator = field.get("separator") or ","
        return f"Any of: {', '.join(field.get('choices') or [])} (separated by '{separator}')."
    if kind == "choice":
        return f"One of: {', '.join(field.get('choices') or [])}."
    if (
        kind == "integer"
        and isinstance(field.get("min"), int)
        and isinstance(field.get("max"), int)
    ):
        return f"A whole number from {field['min']} to {field['max']}."
    if kind == "integer":
        return "A whole number."
    return ""


def _shown_setting(field: dict, kind: str, value: str) -> str:
    """A setting's value as a person reads it: a boolean as on or off."""
    from .core.setup_queue import boolean_spellings

    if kind == "boolean" and value:
        return "on" if value == boolean_spellings(field)[0] else "off"
    return value


def _setup_form_values(spec) -> "dict | None":
    """The values to save: the setup form on a terminal, plain prompts otherwise.

    The form is Textual, the optional `tui` extra, drawn under the prompt
    (full-screen on Windows). Without Textual, or with input or output
    redirected, each setting is asked for in turn instead.
    """
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            from .tui.setup_form import run_inline
        except ImportError:
            pass
        else:
            return run_inline(spec)
    return _prompt_setup_values(spec)


def _installed_config(server_id: str) -> dict:
    """The server's settings in dmcp, read from its installed manifest."""
    import asyncio

    from .dispatch.dmcp_registry import get_server_manifest

    manifest = asyncio.run(get_server_manifest(logger, server_id))
    config = manifest.get("config") if isinstance(manifest, dict) else None
    return config if isinstance(config, dict) else {}


def _dmcp_config_set(server_id: str, key: str, value: str) -> "str | None":
    """Write one setting through dmcp, the value on stdin; an error message or None."""
    import subprocess

    try:
        result = subprocess.run(
            [Config.DMCP_BINARY, "config", server_id, "set", key, "--stdin"],
            input=value,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    if result.returncode != 0:
        return (result.stderr or "").strip() or f"dmcp exited {result.returncode}"
    return None


def _setups_fill(ref: "str | None") -> None:
    import subprocess

    from .core.confirmation_manager import queue_decision
    from .core.params_store import ParamsStore
    from .core.setup_queue import completion_queue_path, form_spec

    setups, live = _load_setups()
    setup = _resolve_setup(setups, ref)
    server_id = setup["server_id"]
    spec = form_spec(setup, _installed_config(server_id), ParamsStore(server_id).get())
    try:
        values = _setup_form_values(spec)
    except (KeyboardInterrupt, EOFError):
        print("\nNot saved; the setup is still pending.")
        sys.exit(130)
    if values is None:
        print("Not saved; the setup is still pending.")
        return

    for key, value in values.items():
        error = _dmcp_config_set(server_id, key, value)
        if error:
            print(f"Error: could not save {key}: {error}")
            sys.exit(1)
    secret = {f["key"] for f in setup.get("fields") or [] if f.get("sensitive")}
    plain = {k: v for k, v in values.items() if k not in secret}
    if plain:
        ParamsStore(server_id).set_many(plain)

    if live:
        response = _ipc_request({"type": "setup_completed", "id": setup["setup_id"]})
        if response and response.get("type") == "ack":
            print(response.get("message", "Saved."))
            return
        # The daemon went away while the form was open: finish here instead.

    print(f"Running the setup of {server_id}...")
    if subprocess.run([Config.DMCP_BINARY, "setup", server_id]).returncode != 0:
        print(f"Error: the setup of {server_id} failed; it is still pending.")
        sys.exit(1)
    queue_decision(
        {"type": "setup_completed", "id": setup["setup_id"]}, completion_queue_path()
    )
    print(
        f"{server_id} is set up. JARVIS clears it from the queue when it next starts."
    )


def _setups_cancel(ref: "str | None") -> None:
    from .core.confirmation_manager import queue_decision
    from .core.setup_queue import completion_queue_path

    setups, live = _load_setups()
    setup = _resolve_setup(setups, ref)
    if live:
        response = _ipc_request({"type": "cancel_setup", "id": setup["setup_id"]})
        if response and response.get("type") == "ack":
            print(response.get("message", "Cancelled."))
            return
    queue_decision(
        {"type": "cancel_setup", "id": setup["setup_id"]}, completion_queue_path()
    )
    print(
        f"Cancelled the setup of {setup['server_id']}; applies when JARVIS next starts."
    )


def _installed_servers() -> list:
    """Each installed server's id, name and manifest path, from `dmcp list --json`."""
    import subprocess

    try:
        result = subprocess.run(
            [Config.DMCP_BINARY, "list", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        servers = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    if not isinstance(servers, list):
        return []
    return [s for s in servers if isinstance(s, dict) and s.get("id")]


def _resolve_server(servers: list, ref: str) -> dict:
    """The installed server `ref` names (its id, or part of its id or name), or exit."""
    for server in servers:
        if server["id"] == ref:
            return server
    low = ref.lower()
    matches = [
        s
        for s in servers
        if low in s["id"].lower() or low in str(s.get("name") or "").lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        print(f"Error: '{ref}' matches more than one server:")
        for s in matches:
            print(f"  {s['id']}  ({s.get('name') or s['id']})")
    else:
        print(f"Error: no installed server matches '{ref}'. Run 'dmcp list'.")
    sys.exit(1)


def _setups_edit(ref: str) -> None:
    """The setup form for an installed server's settings, filled in with what is set."""
    from .core.params_store import ParamsStore
    from .core.setup_queue import form_spec, missing_required, setting_fields

    server = _resolve_server(_installed_servers(), ref)
    server_id = server["id"]
    setups, _ = _load_setups()
    waiting = [s for s in setups if s.get("server_id") == server_id]
    if waiting:
        # Filling that in resumes the goal waiting on it; an edit would not.
        print(f"{server_id} is waiting for setup; opening that instead.")
        _setups_fill(waiting[0]["setup_id"])
        return

    try:
        with open(server["manifest_path"], encoding="utf-8") as f:
            manifest = json.load(f)
    except (KeyError, OSError, ValueError) as e:
        print(f"Error: cannot read the settings of {server_id}: {e}")
        sys.exit(1)
    props = manifest.get("configurableProperties") or []
    name = manifest.get("name") or server_id
    if not props:
        print(f"{name} has no settings.")
        return
    config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
    spec = form_spec(
        {
            "server_id": server_id,
            "server_name": name,
            "fields": setting_fields(props),
            "missing": missing_required(props, config),
            "kind": "edit",
        },
        config,
        ParamsStore(server_id).get(),
    )
    try:
        values = _setup_form_values(spec)
    except (KeyboardInterrupt, EOFError):
        print("\nNot saved; nothing changed.")
        sys.exit(130)
    if values is None:
        print("Not saved; nothing changed.")
        return
    if not values:
        print("Nothing to save; the settings stay as they are.")
        return

    for key, value in values.items():
        error = _dmcp_config_set(server_id, key, value)
        if error:
            print(f"Error: could not save {key}: {error}")
            sys.exit(1)
    secret = {f["key"] for f in spec.fields if f.get("sensitive")}
    plain = {k: v for k, v in values.items() if k not in secret}
    if plain:
        ParamsStore(server_id).set_many(plain)
    labels = {f["key"]: f.get("label") or f["key"] for f in spec.fields}
    print(
        f"Saved {', '.join(labels[k] for k in values)} for {name}. "
        "They apply the next time it starts."
    )


def _show_setups_usage() -> None:
    print("Usage:")
    print("  jarvis setups                  # List servers waiting for setup, numbered")
    print("  jarvis setups fill [<which>]   # Fill in a server's settings")
    print("  jarvis setups cancel [<which>] # Drop a pending setup")
    print("  jarvis setups edit <server>    # Change an installed server's settings")
    print("  <which>: the number from the list, the id, or part of the server's name;")
    print("  optional when only one server is waiting.")


def _cmd_confirm() -> None:
    """List or resolve pending tool confirmations (#192).

    Prefers the running daemon's socket; falls back to the on-disk store and
    decision queue when it is not reachable, so a review is possible between
    restarts as well as across one.
    """
    from .platform import current as platform

    if len(sys.argv) == 2:
        request = {"type": "list_confirmations"}
    else:
        subcmd = sys.argv[2]
        if subcmd == "approve-all":
            request = {"type": "approve_all_confirmations"}
        elif subcmd in ("approve", "deny") and len(sys.argv) >= 4:
            confirmation_id = sys.argv[3]
            # Optional 4th arg: comma-separated indices for a per-task decision
            # within a batch (#187) — "approve <id> 0,2" approves items 0 and 2,
            # denies the rest of that batch. Whole-batch approve/deny (no
            # indices) stays all-or-nothing, as before.
            if len(sys.argv) >= 5:
                try:
                    indices = [int(i) for i in sys.argv[4].split(",") if i.strip()]
                except ValueError:
                    print(f"Error: invalid index list '{sys.argv[4]}' (want e.g. 0,2)")
                    sys.exit(1)
                if subcmd == "deny":
                    print("Error: index list only applies to 'approve' (per-task pick)")
                    sys.exit(1)
                request = {
                    "type": "partial_approve_confirmation",
                    "id": confirmation_id,
                    "approved_indices": indices,
                }
            else:
                request = {
                    "type": (
                        "approve_confirmation"
                        if subcmd == "approve"
                        else "deny_confirmation"
                    ),
                    "id": confirmation_id,
                }
        else:
            _show_confirm_usage()
            sys.exit(1)

    path = _find_ipc_endpoint(quiet=True)
    if not path:
        _confirm_offline(request)
        return

    try:
        sock = platform.ipc_connect(path)
        try:
            sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
            sock.settimeout(5.0)
            response_line = sock.makefile().readline()
        finally:
            sock.close()
    except (socket.error, OSError) as e:
        print(f"Could not reach JARVIS: {e}")
        _confirm_offline(request)
        return

    if not response_line:
        print("Error: No response from JARVIS.")
        sys.exit(1)

    try:
        response = json.loads(response_line)
    except json.JSONDecodeError:
        print("Error: Invalid response from JARVIS.")
        sys.exit(1)

    if response.get("type") == "confirmation_list":
        _print_confirmation_list(response.get("confirmations", []))
    elif response.get("type") == "ack":
        print(response.get("message", "Done."))
    else:
        print(f"Error: {response.get('message', 'Unexpected response')}")
        sys.exit(1)


def _print_confirmation_list(confirmations: list) -> None:
    """Render the pending list — the one renderer for live and offline alike."""
    if not confirmations:
        print("No pending confirmations.")
        return
    print(f"Pending confirmations ({len(confirmations)}):")
    for c in confirmations:
        goal_desc = c.get("goal_description")
        session = c.get("session_id")
        age = _format_age(c["created_at"]) if c.get("created_at") else "?"
        header = f'  [{c["id"]}] '
        if goal_desc:
            header += f'goal "{goal_desc}"  '
        if session:
            header += f"(session {session}, {age})"
        else:
            header += f"({age})"
        print(header)
        for i, line in enumerate(c.get("tool_lines") or c.get("tool_names", [])):
            print(f"        [{i}] {line}")


def _describe_decision(decision: dict) -> str:
    """One-line rendering of a queued decision, e.g. ``approve items 0,2``."""
    kind = decision.get("type")
    if kind == "approve_confirmation":
        return "approve"
    if kind == "deny_confirmation":
        return "deny"
    if kind == "partial_approve_confirmation":
        indices = ",".join(str(i) for i in decision.get("approved_indices", []))
        return f"approve items {indices}"
    return "approve-all"


def _print_queued_decisions(queued: list) -> None:
    """Show what a previous offline session already decided (#224 follow-up)."""
    if not queued:
        return
    print(f"Queued decisions ({len(queued)}), applied at next start:")
    for d in queued:
        target = f'[{d["id"]}] ' if d.get("id") else ""
        print(f"  {target}{_describe_decision(d)}")


def _confirm_offline(request: dict) -> None:
    """Serve `jarvis confirm` from disk when the daemon is unreachable.

    Listing reads the #224 store; a decision is appended to the queue file as
    the exact socket-protocol message a live client would have sent, and the
    daemon replays it at startup. Queuing a decision for an id that is not in
    the store is refused — an approval nobody can match is a silent no-op at
    best and a stale approval waiting for an id collision at worst.
    """
    from .core.confirmation_manager import (
        decision_queue_path,
        load_decision_queue,
        load_pending_summaries,
        queue_decision,
        store_path,
    )

    pending = load_pending_summaries(store_path())
    queue_path = decision_queue_path()

    if request["type"] == "list_confirmations":
        _print_confirmation_list(pending)
        _print_queued_decisions(load_decision_queue(queue_path))
        print(OFFLINE_NOTICE)
        return

    known = {c["id"] for c in pending}
    if request["type"] == "approve_all_confirmations":
        if not known:
            print("Nothing pending.")
            return
        target = f"approve-all ({len(known)} pending)"
    else:
        if request["id"] not in known:
            print(f"Error: No pending confirmation with id '{request['id']}'.")
            print("  Run 'jarvis confirm' to see what is waiting.")
            sys.exit(1)
        target = f"{_describe_decision(request)} {request['id']}"

    replaced = queue_decision(request, queue_path)
    print(f"Queued: {target} — applies when the daemon starts.")
    if replaced:
        print("  (replaces the earlier queued decision for this confirmation)")


def _show_confirm_usage() -> None:
    print("Usage:")
    print("  jarvis confirm                        # List pending, by goal/session")
    print("  jarvis confirm approve <id>           # Approve the whole batch")
    print("  jarvis confirm approve <id> 0,2       # Approve only items 0 and 2")
    print("  jarvis confirm deny <id>              # Deny the whole batch")
    print("  jarvis confirm approve-all            # Approve all pending")
    print("  (daemon off: decisions queue on disk and apply at its next start)")


def set_output_mode(mode: str) -> None:
    """
    Update OUTPUT_MODE in .env file.

    Args:
        mode: 'text' or 'voice'
    """
    if mode not in ["text", "voice"]:
        print(f"Error: Invalid mode '{mode}'. Must be 'text' or 'voice'")
        sys.exit(1)

    _update_env_setting("OUTPUT_MODE", mode)
    print(f"Output mode set to: {mode}")


def set_history_reset(enabled: bool) -> None:
    """
    Update RESET_HISTORY_AFTER_RESPONSE in .env file.

    Args:
        enabled: True to reset history, False to maintain context
    """
    value = "true" if enabled else "false"
    _update_env_setting("RESET_HISTORY_AFTER_RESPONSE", value)
    print(f"History reset {'enabled' if enabled else 'disabled'}")


def set_sudo_access(enabled: bool) -> None:
    """
    Enable or disable sudo access for jarvis user.

    Args:
        enabled: True to enable sudo, False to disable
    """
    from .core.sudo_manager import disable_sudo, enable_sudo

    value = "true" if enabled else "false"
    _update_env_setting("JARVIS_SUDO_ENABLED", value)

    if enabled:
        if enable_sudo():
            print("Sudo access enabled for jarvis user")
        else:
            print(
                "Failed to enable sudo access. Please run with sudo: sudo jarvis sudo enable"
            )
            sys.exit(1)
    else:
        if disable_sudo():
            print("Sudo access disabled for jarvis user")
        else:
            print(
                "Failed to disable sudo access. Please run with sudo: sudo jarvis sudo disable"
            )
            sys.exit(1)


def get_sudo_status() -> None:
    """Show current sudo access status."""
    from .core.sudo_manager import is_sudo_enabled

    enabled = is_sudo_enabled()
    config_preference = Config.JARVIS_SUDO_ENABLED

    print(f"Sudo access: {'enabled' if enabled else 'disabled'}")
    print(f"Config preference: {'enabled' if config_preference else 'disabled'}")

    if enabled != config_preference:
        print("Warning: System state differs from config preference")
        print("  Run 'jarvis sudo enable' or 'jarvis sudo disable' to sync")


def _legacy_redirect(old_cmd: str) -> None:
    """Print a migration hint for removed legacy commands."""
    print(f"'{old_cmd}' has been removed. Use the provider pool instead:")
    print("  jarvis providers                                    # List providers")
    print("  jarvis providers add --type ollama --model <model>  # Add provider")
    print("  jarvis providers edit <name> --model <model>        # Update a field")
    sys.exit(1)


def _update_env_setting(key: str, value: str) -> None:
    """
    Update a setting in .env file.

    Args:
        key: Environment variable name
        value: New value
    """
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()
    else:
        template_file = Path(__file__).parent / ".env.example"
        if template_file.exists():
            logger.info("Creating .env from template...")
            lines = template_file.read_text().splitlines()
        else:
            lines = []

    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}=") or line.startswith(f"#{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break

    if not found:
        lines.append(f"{key}={value}")

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(lines) + "\n")

    os.environ[key] = value
    setattr(Config, key, value)


def get_output_mode() -> str:
    """Get current output mode from config."""
    return Config.OUTPUT_MODE


def _check_capabilities() -> dict:
    """Check system capabilities for voice features."""
    capabilities = {
        "voice_input": False,
        "voice_output": False,
    }

    try:
        from .voice.audio import (
            check_audio_input_available,
            check_audio_output_available,
        )

        capabilities["voice_input"] = check_audio_input_available()
        capabilities["voice_output"] = check_audio_output_available()
    except Exception as e:
        logger.debug(f"Error checking capabilities: {e}")

    return capabilities


def _cmd_providers() -> None:
    """Manage the provider failover pool."""
    if len(sys.argv) == 2:
        providers = list_providers()
        if not providers:
            print("No providers configured.")
            print()
            print("Add one with:")
            print("  jarvis providers add --type ollama --model qwen3:4b")
            print(
                "  jarvis providers add --type api --model gpt-4 "
                "--url https://api.example.com --key sk-xxx"
            )
            return

        print(f"Provider pool ({len(providers)} providers):")
        print()
        for i, p in enumerate(providers):
            name = p.get("name", f"provider-{i}")
            ptype = p.get("type", "?")
            model = p.get("model", "?")
            url = p.get("url", "")
            priority = f"[{i + 1}]"
            print(f"  {priority} {name}")
            print(f"      type: {ptype}  model: {model}")
            if url:
                print(f"      url: {url}")
            if ptype == "api":
                print(f"      api_key: {'set' if p.get('api_key') else '(not set)'}")
            if p.get("temperature") is not None:
                print(f"      temperature: {p['temperature']}")
            if p.get("vision"):
                print("      vision: true")
            print()
        print(f"  File: {Config.PROVIDERS_FILE}")
        return

    subcmd = sys.argv[2]

    if subcmd == "add":
        args = sys.argv[3:]
        vision = "--vision" in args
        flags = parse_flags([a for a in args if a != "--vision"])
        ptype = flags.get("type", "")
        model = flags.get("model", "")
        if not ptype or not model:
            print(
                "Usage: jarvis providers add --type <ollama|api|lmstudio> --model <model>"
            )
            print(
                "  Optional: --name <label> --url <url> --key <api_key> "
                "--temperature <0.0-2.0> --vision"
            )
            sys.exit(1)
        temp = None
        if flags.get("temperature"):
            try:
                temp = float(flags["temperature"])
            except ValueError:
                print(
                    f"Error: Temperature must be a number, got '{flags['temperature']}'"
                )
                sys.exit(1)
        try:
            name, position = add_provider(
                ptype,
                model,
                name=flags.get("name"),
                url=flags.get("url"),
                api_key=flags.get("key"),
                temperature=temp,
                vision=vision,
            )
            print(f"Added provider '{name}' ({ptype}/{model}) at position {position}")
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif subcmd == "remove":
        if len(sys.argv) < 4:
            print("Usage: jarvis providers remove <name>")
            sys.exit(1)
        try:
            remove_provider(sys.argv[3])
            print(f"Removed provider '{sys.argv[3]}'")
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif subcmd == "move":
        if len(sys.argv) < 5:
            print("Usage: jarvis providers move <name> <position>")
            print("  Position is 1-based (1 = highest priority)")
            sys.exit(1)
        try:
            target_pos = int(sys.argv[4])
        except ValueError:
            print(f"Error: Position must be a number, got '{sys.argv[4]}'")
            sys.exit(1)
        try:
            move_provider(sys.argv[3], target_pos)
            print(f"Moved provider '{sys.argv[3]}' to position {target_pos}")
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    elif subcmd == "edit":
        if len(sys.argv) < 4:
            print("Usage: jarvis providers edit <name> --field <value>")
            print("  Fields: --model, --url, --key, --type, --vision <true|false>")
            sys.exit(1)
        flags = parse_flags(sys.argv[4:])
        if not flags:
            print(
                "Error: No fields to update. Use --model, --url, --key, --type, "
                "or --vision <true|false>"
            )
            sys.exit(1)
        try:
            updated = edit_provider(sys.argv[3], **flags)
            print(f"Updated provider '{sys.argv[3]}': {', '.join(updated)}")
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)

    else:
        print(f"Error: Unknown subcommand '{subcmd}'")
        _show_providers_usage()
        sys.exit(1)


def _show_providers_usage() -> None:
    print("Usage:")
    print("  jarvis providers                                  # List all")
    print("  jarvis providers add --type ollama --model <model> # Add provider")
    print("  jarvis providers remove <name>                    # Remove by name")
    print("  jarvis providers move <name> <position>           # Reorder priority")
    print("  jarvis providers edit <name> --model <model>      # Update a field")


def _cmd_constraints() -> None:
    """List active standing constraints."""
    from .core.constraint_store import load_constraints

    records = load_constraints()
    active = [r for r in records if r.get("active", True)]
    if not active:
        print("No standing constraints.")
        return
    print(f"Standing constraints ({len(active)}):")
    for c in active:
        print(f"  #{c['id']}  {c['text']}")
        print(f"          pattern: {c['pattern']}")
        print(
            f"          added:   {c['created_at'][:19]}  source: {c.get('source', '?')}"
        )


def _cmd_constrain() -> None:
    """Add a path-prefix deny constraint: jarvis constrain <path> [description]."""
    if len(sys.argv) < 3:
        print("Usage: jarvis constrain <path> [description]")
        print('Example: jarvis constrain /home/user/photos "never touch my photos"')
        sys.exit(1)
    path_arg = sys.argv[2]
    text = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else f"deny access to {path_arg}"
    from .core.constraint_store import add_constraint

    record = add_constraint(text=text, pattern=path_arg, source="cli")
    print(f"Constraint #{record['id']} added: {text!r}")
    print(f"  Blocks any dispatch touching paths under: {record['pattern']}")


def _cmd_unconstrain() -> None:
    """Remove a constraint by id: jarvis unconstrain <id>."""
    if len(sys.argv) < 3:
        print("Usage: jarvis unconstrain <id>")
        print("  (get ids from: jarvis constraints)")
        sys.exit(1)
    constraint_id = sys.argv[2]
    from .core.constraint_store import remove_constraint

    if remove_constraint(constraint_id):
        print(f"Constraint #{constraint_id} removed.")
    else:
        print(f"Error: No constraint with id '{constraint_id}'.")
        sys.exit(1)


def _cmd_web() -> None:
    """Phone access over Tailscale (docs/web-gateway.md): serve, allow, revoke, list."""
    from .server.web_gateway import (
        ROLES,
        Allowlist,
        build_gateway,
        serve_forever,
    )

    args = sys.argv[2:]
    allowlist = Allowlist(Config.WEB_ALLOWLIST_FILE)
    sub = args[0] if args else "start"

    if sub == "allow":
        if len(args) < 2:
            print(f"Usage: jarvis web allow <tailscale-login> [{'|'.join(ROLES)}]")
            sys.exit(1)
        role = args[2] if len(args) > 2 else "trusted"
        try:
            allowlist.set(args[1], role)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)
        print(f"{args[1].lower()} may use JARVIS from a phone as {role}.")
        return

    if sub == "revoke":
        if len(args) < 2:
            print("Usage: jarvis web revoke <tailscale-login>")
            sys.exit(1)
        if not allowlist.remove(args[1]):
            print(f"{args[1]} is not on the allowlist.")
            sys.exit(1)
        print(f"Removed {args[1].lower()}. Open sessions end at their next request.")
        return

    if sub == "list":
        entries = allowlist.load()
        if not entries:
            print(
                "Nobody is allowed. Add one: jarvis web allow <tailscale-login> owner"
            )
        for login, role in sorted(entries.items()):
            print(f"  {login:<40} {role}")
        return

    dev_login = None
    if sub != "start":
        print(f"Error: Unknown web command '{sub}'")
        show_usage()
        sys.exit(1)
    if "--insecure-dev-login" in args:
        rest = args[args.index("--insecure-dev-login") + 1 :]
        if not rest:
            print("Usage: jarvis web start --insecure-dev-login <login>")
            sys.exit(1)
        dev_login = rest[0]

    if not Config.WEB_ENABLED:
        print("Remote access is off. Turn it on with:")
        print("  JARVIS_WEB_ENABLED=true jarvis web")
        print("  (or add JARVIS_WEB_ENABLED=true to jarvis.conf)")
        sys.exit(1)
    try:
        import websockets  # noqa: F401
    except ImportError:
        import subprocess

        print("Installing the web extra (websockets)...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "websockets>=13"], check=True
        )
    if not dev_login and not allowlist.load():
        print("Nobody is on the allowlist, so every request would be refused.")
        print("  jarvis web allow <your-tailscale-login> owner")
        sys.exit(1)

    port = Config.WEB_PORT
    print(f"jarvis web listening on 127.0.0.1:{port} (loopback only)")
    if dev_login:
        print(
            f"WARNING: --insecure-dev-login: every request is treated as {dev_login}."
        )
    else:
        print(
            f"  Publish it inside your tailnet:  tailscale serve --bg --https=443 {port}"
        )
        print("  Never use `tailscale funnel`; the gateway refuses Funnel requests.")
    try:
        asyncio.run(serve_forever(build_gateway(dev_login), port))
    except KeyboardInterrupt:
        print("\nStopped.")
    except OSError as e:
        print(f"Error: could not listen on 127.0.0.1:{port}: {e}")
        sys.exit(1)
    except RuntimeError as e:
        print(f"Error: {e}")
        sys.exit(1)


def show_usage() -> None:
    """Display usage information."""
    print("JARVIS AI Assistant - CLI Interface")
    print()
    print("Usage:")
    print("  jarvis                    # Start voice activation mode")
    print("  jarvis tui                # Interactive TUI with session sidebar")
    print("  jarvis chat               # Start interactive text chat (stdin)")
    print("  jarvis text               # Set text output mode")
    print("  jarvis voice              # Set voice output mode")
    print('  jarvis ask "<message>"    # Ask a single question')
    print("  jarvis output-type        # Show current output mode")
    print("  jarvis history-reset on   # Enable history reset after each response")
    print("  jarvis history-reset off  # Disable history reset (maintain context)")
    print("  jarvis history-reset      # Show current history reset setting")
    print("  jarvis sudo enable        # Enable sudo access (requires root)")
    print("  jarvis sudo disable       # Disable sudo access (requires root)")
    print("  jarvis sudo               # Show current sudo access status")
    print()
    print("Standing Constraints:")
    print("  jarvis constrain <path> [desc]     # Add path-prefix deny constraint")
    print("  jarvis unconstrain <id>             # Remove constraint by id")
    print("  jarvis constraints                  # List active constraints")
    print()
    print("Pending Confirmations:")
    print("  jarvis confirm                        # List pending, by goal/session")
    print("  jarvis confirm approve <id>           # Approve the whole batch")
    print("  jarvis confirm approve <id> 0,2       # Approve only items 0 and 2")
    print("  jarvis confirm deny <id>              # Deny the whole batch")
    print("  jarvis confirm approve-all            # Approve all pending")
    print("  (daemon off: decisions queue on disk and apply at its next start)")
    print()
    print("Pending Server Setups:")
    print("  jarvis setups                         # List servers waiting for setup")
    print("  jarvis setups fill [<n|name>]         # Fill in a server's settings")
    print("  jarvis setups cancel [<n|name>]       # Drop a pending setup")
    print(
        "  jarvis setups edit <server>           # Change an installed server's settings"
    )
    print()
    print("Phone Access (opt-in, over Tailscale):")
    print("  jarvis web allow <login> [guest|trusted|owner]   # Let an account in")
    print("  jarvis web revoke <login>                        # Remove an account")
    print("  jarvis web list                                  # Show who is allowed")
    print("  JARVIS_WEB_ENABLED=true jarvis web               # Start the gateway")
    print()
    print("Provider Pool:")
    print("  jarvis providers                                    # List providers")
    print("  jarvis providers add --type ollama --model <model>  # Add Ollama")
    print("  jarvis providers add --type api --model <m> --url <u> --key <k>")
    print(
        "  jarvis providers add --type lmstudio --model <m>    # Add LM Studio (no key needed)"
    )
    print("  jarvis providers remove <name>                      # Remove provider")
    print("  jarvis providers move <name> <position>             # Reorder priority")
    print("  jarvis providers edit <name> --model <model>        # Update a field")
    print(
        "  jarvis auto-pull on/off                             # Auto-pull missing models"
    )


def _has_llm_configured() -> bool:
    """True if at least one provider is configured."""
    return bool(list_providers())


def _run_until_stopped(jarvis) -> None:
    """Run JARVIS to the end of its shutdown, then leave without waiting on stdin.

    A thread blocked reading the next line at the prompt (the chat input, or
    a terminal confirmation) holds stdin's lock, and normal interpreter exit
    waits for that lock forever: Ctrl+C reached "Shutdown complete" and the
    terminal still hung until Enter was pressed. Everything JARVIS owns is
    closed once run() returns, so flush what is buffered and exit.
    """
    import logging

    asyncio.run(jarvis.run())
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def main() -> None:
    """Main CLI entry point."""
    from .main import Jarvis

    # No arguments or "run" — dual-input mode (voice + socket + optional stdin)
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1] == "run"):
        if len(sys.argv) > 1:
            sys.argv.pop(1)
        if not _has_llm_configured():
            print("Error: No LLM configured.")
            print("  Quick start: jarvis providers add --type ollama --model qwen3:4b")
            print("  Or set a model: jarvis model qwen3:4b")
            sys.exit(1)
        print("Starting JARVIS (dual input: voice + socket)...")
        print(
            "  Say 'Hey Jarvis' for voice, or use 'jarvis send <msg>' from another terminal."
        )
        print("  Press Ctrl+C to stop.\n")

        try:
            jarvis = Jarvis()
            _run_until_stopped(jarvis)
        except KeyboardInterrupt:
            print("\nGoodbye.")
        except Exception as e:
            logger.error(f"Failed to start JARVIS: {e}")
            print(f"\nError: {e}")
            print("\nTip: Use 'jarvis chat' for text-only mode.")
            sys.exit(1)
        return

    command = sys.argv[1]

    if command == "send":
        _cmd_send()
        return

    if command == "chat":
        if not _has_llm_configured():
            print("Error: No LLM configured.")
            print("  Quick start: jarvis providers add --type ollama --model qwen3:4b")
            sys.exit(1)
        print("Starting JARVIS interactive chat...")
        print("Type your messages. Press Ctrl+C to exit.\n")
        try:
            jarvis = Jarvis(text_mode=True)
            _run_until_stopped(jarvis)
        except KeyboardInterrupt:
            print("\nGoodbye.")
        except Exception as e:
            logger.error(f"Failed to start JARVIS: {e}")
            print(f"\nError: {e}")
            sys.exit(1)

    elif command == "tui":
        try:
            from .tui import run_tui
        except ImportError as e:
            print("Error: TUI dependencies are not installed.")
            print("  Install with: pip install 'project-jarvis[tui]'")
            print(f"  (missing: {e.name if hasattr(e, 'name') else e})")
            sys.exit(1)
        try:
            run_tui()
        except KeyboardInterrupt:
            print("\nGoodbye.")
        except Exception as e:
            logger.error(f"TUI crashed: {e}", exc_info=True)
            print(f"\nError: {e}")
            sys.exit(1)

    elif command == "text":
        set_output_mode("text")

    elif command == "voice":
        capabilities = _check_capabilities()
        if not capabilities["voice_output"]:
            print("Warning: No audio output devices detected.")
            print("  Voice output will fallback to text mode.")
            response = input("Continue anyway? (y/n): ").strip().lower()
            if response not in ["y", "yes"]:
                print("Cancelled.")
                sys.exit(0)
        set_output_mode("voice")

    elif command == "output-type":
        mode = get_output_mode()
        print(f"Current output mode: {mode}")

    elif command == "history-reset":
        if len(sys.argv) == 2:
            enabled = Config.RESET_HISTORY_AFTER_RESPONSE
            print(f"History reset: {'enabled' if enabled else 'disabled'}")
        elif len(sys.argv) == 3:
            value = sys.argv[2].lower()
            if value in ["on", "true", "1", "yes", "enable"]:
                set_history_reset(True)
            elif value in ["off", "false", "0", "no", "disable"]:
                set_history_reset(False)
            else:
                print(f"Error: Invalid value '{value}'. Use 'on' or 'off'")
                sys.exit(1)
        else:
            print("Usage: jarvis history-reset [on|off]")
            sys.exit(1)

    elif command == "sudo":
        if len(sys.argv) == 2:
            get_sudo_status()
        elif len(sys.argv) == 3:
            value = sys.argv[2].lower()
            if value in ["on", "true", "1", "yes", "enable"]:
                set_sudo_access(True)
            elif value in ["off", "false", "0", "no", "disable"]:
                set_sudo_access(False)
            else:
                print(f"Error: Invalid value '{value}'. Use 'enable' or 'disable'")
                sys.exit(1)
        else:
            print("Usage: jarvis sudo [enable|disable]")
            sys.exit(1)

    elif command == "confirm":
        _cmd_confirm()

    elif command == "setups":
        _cmd_setups()

    elif command == "web":
        _cmd_web()

    elif command == "model":
        _legacy_redirect("jarvis model")

    elif command == "ask":
        if len(sys.argv) < 3:
            print("Error: Message required")
            print('Usage: jarvis ask "<message>"')
            sys.exit(1)

        message = " ".join(sys.argv[2:])
        jarvis = Jarvis(text_mode=True)
        jarvis.ask(message)

    elif command in ("provider", "llm-url", "api-key", "llm-config"):
        _legacy_redirect(f"jarvis {command}")

    elif command == "providers":
        _cmd_providers()

    elif command == "auto-pull":
        if len(sys.argv) == 2:
            auto_pull = getattr(Config, "LLM_AUTO_PULL", False)
            print(f"Auto-pull missing models: {'enabled' if auto_pull else 'disabled'}")
        elif len(sys.argv) == 3:
            value = sys.argv[2].lower()
            if value in ["on", "true", "1", "yes", "enable"]:
                _update_env_setting("LLM_AUTO_PULL", "true")
                print("Auto-pull enabled")
            elif value in ["off", "false", "0", "no", "disable"]:
                _update_env_setting("LLM_AUTO_PULL", "false")
                print("Auto-pull disabled")
            else:
                print(f"Error: Invalid value '{value}'. Use 'on' or 'off'")
                sys.exit(1)
        else:
            print("Usage: jarvis auto-pull [on|off]")
            sys.exit(1)

    elif command == "constraints":
        _cmd_constraints()

    elif command == "constrain":
        _cmd_constrain()

    elif command == "unconstrain":
        _cmd_unconstrain()

    elif command in ["-h", "--help", "help"]:
        show_usage()

    else:
        print(f"Error: Unknown command '{command}'")
        print()
        show_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
