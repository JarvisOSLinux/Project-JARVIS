"""`jarvis web` — phone access to a home JARVIS over Tailscale. Opt-in, off by default.

SECURITY CONTEXT (read before touching this file)
--------------------------------------------------
``docs/SECURITY-ARCHITECTURE.md`` lists OpenClaw's CVE-2026-25253 (token
exfiltration through a gateway URL) and CVE-2026-28472 (WebSocket
device-identity bypass) as eliminated by design because JARVIS had no WebSocket
gateway. This module is one, so the same threats are closed another way:

1. **Separate process.** A crash here never takes the daemon down, and all it
   can do is what ``jarvis.sock`` allows -- narrowed further by ``ROLE_SENDS``.
2. **Loopback only, never public.** Binds ``127.0.0.1``. Reached through
   ``tailscale serve``, never Funnel; a Funnel request is refused.
3. **Identity from Tailscale, none of our own.** ``tailscale serve`` adds the
   sender's ``Tailscale-User-Login`` header and strips any the client sent. No
   passwords, tokens or pairing codes. A request without it is refused, and
   the login must be on the allowlist, each with a role.
4. **Its own origin.** The page is served by this process; the WebSocket's
   ``Origin`` must equal ``Host`` and ``Host`` must end in the tailnet suffix.
   That closes cross-site WebSocket hijacking and DNS rebinding.
5. **Remote mode always asks.** A session is refused while the daemon runs in
   ``allow_all`` and ended if it switches to it, and no phone may switch it
   there. Approve all names the requests the phone shows.
6. **The role table covers the chat.** A chat line the daemon runs as a slash
   command needs the role that command's own message needs (``acts_as``).
7. **A written trail.** Every state-changing request is appended to the audit
   file with the account that sent it, never with an API key.
8. **The owner's phone is jarvis-app** (Yakup, 2026-11-10): settings,
   providers with their keys, approve all, voice listening. Keys (and
   ``headers``) the daemon lists are removed before they reach a phone; a key
   typed on the phone goes to the daemon over Tailscale's encrypted link, and
   a provider moved to another host loses its stored key
   (``core/providers.edit_provider``). What belongs to the laptop
   stays there: shutting JARVIS down (nothing could start it again from a
   phone), the wake sound's file, and server setups.

Residual risk: any local process can connect to the loopback port and send the
identity header itself. On a single-user host that is no more than the same
user already has through ``jarvis.sock``; on a shared host, do not enable this.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any, Optional

from ..config import Config
from ..core import settings
from ..core.logger import get_logger
from ..core.secret_scrubber import redact_secrets
from ..platform import current as platform

logger = get_logger(__name__)

HOST = "127.0.0.1"
WEB_DIR = Path(__file__).parent / "web"
ROLES = ("guest", "trusted", "owner")

CLOSE_REFUSED = 4003
CLOSE_IDLE = 4008
CLOSE_UNSAFE = 4009
CLOSE_UPSTREAM = 4010

_GUEST_SENDS = frozenset(
    {
        "message",
        "stop_stream",
        # Stops JARVIS's work in the session; kills nothing (#270).
        "stop",
        "list_sessions",
        "list_confirmations",
        "list_clients",
        "ping",
    }
)
_TRUSTED_SENDS = _GUEST_SENDS | {
    "approve_confirmation",
    "deny_confirmation",
    "partial_approve_confirmation",
    "create_session",
    "switch_session",
    "get_status",
    "list_work",
    # Also kills the session's running tasks; stop_task kills one.
    "stop_all",
    "stop_task",
}
_PROVIDER_CHANGES = frozenset(
    {"add_provider", "edit_provider", "remove_provider", "move_provider"}
)
# The owner's phone is jarvis-app (docstring, point 8).
_OWNER_SENDS = (
    _TRUSTED_SENDS
    | _PROVIDER_CHANGES
    | {
        "rename_session",
        "delete_session",
        "approve_all_confirmations",
        "get_settings",
        "set_setting",
        "list_providers",
        "start_listening",
        "stop_listening",
    }
)
# Absent on purpose for every role: shutdown_request, setups, and the raw
# confirmation_response.
ROLE_SENDS = {
    "guest": _GUEST_SENDS,
    "trusted": _TRUSTED_SENDS,
    "owner": _OWNER_SENDS,
}

# What a phone may be shown. Anything else the daemon broadcasts (provider
# lists, settings, setups) stays off the wire.
RELAYED = frozenset(
    {
        "state",
        "response",
        "user_message",
        "activity",
        "confirmation_request",
        "confirmation_list",
        "ack",
        "error",
        "session_list",
        "session_switched",
        "session_error",
        "client_list",
        "ping",
        "pong",
        "DAEMON_SHUTDOWN",
    }
)
# Action blocks carry tool parameters and raw output, status names paths and
# providers, and work lists every running task (#267): for the roles trusted to
# approve what JARVIS runs, not for a guest.
_TRUSTED_RELAYED = RELAYED | {"action", "status", "work"}
# Provider lists reach the owner without their keys (`shown_to`).
_OWNER_RELAYED = _TRUSTED_RELAYED | {
    "settings",
    "config_updated",
    "config_error",
    "provider_list",
    "provider_error",
}
RELAYED_FOR = {
    "guest": RELAYED,
    "trusted": _TRUSTED_RELAYED,
    "owner": _OWNER_RELAYED,
}

_AUDITED = frozenset(
    {
        "message",
        "stop_stream",
        "stop",
        "stop_all",
        "stop_task",
        "approve_confirmation",
        "deny_confirmation",
        "partial_approve_confirmation",
        "create_session",
        "switch_session",
        "rename_session",
        "delete_session",
        "approve_all_confirmations",
        "set_setting",
        "start_listening",
        "stop_listening",
    }
    | _PROVIDER_CHANGES
)

RATE_WINDOW_S = 10.0
RATE_MAX = 20
MAX_FRAME = 64 * 1024
UPSTREAM_LINE_LIMIT = 8 * 1024 * 1024


# The daemon runs a chat line that starts with one of these as the command
# (`root_handlers.on_user_input`), which does what this socket message does.
_SLASH_SENDS = {
    "/new": "create_session",
    "/sessions": "list_sessions",
    "/switch": "switch_session",
    "/rename": "rename_session",
    "/delete": "delete_session",
}


def acts_as(msg: dict) -> str:
    """The socket message `msg` amounts to once the daemon has read it.

    A guest may chat but not delete a session, and the daemon reads
    "/delete 5bd5" typed into the chat as the delete itself. The words are
    split as `stop.handle_command` and `session_commands.handle_slash_command`
    split them, so the gateway and the daemon read one line the same way.
    """
    kind = msg["type"]
    content = msg.get("content")
    if kind != "message" or not isinstance(content, str):
        return kind
    words = content.strip().split()
    if not words or not words[0].startswith("/"):
        return kind
    command = words[0].lower()
    if command == "/stop":
        rest = [w.lower() for w in words[1:]]
        if rest == ["all"]:
            return "stop_all"
        if len(rest) == 1 and rest[0].isdigit():
            return "stop_task"
        return "stop"
    return _SLASH_SENDS.get(command, kind)


def sendable(role: str, msg: Any) -> bool:
    """True when `role` may relay `msg` to the daemon."""
    if not (isinstance(msg, dict) and isinstance(msg.get("type"), str)):
        return False
    if msg["type"] == "message" and not isinstance(msg.get("content"), str):
        return False
    allowed = ROLE_SENDS.get(role, frozenset())
    return msg["type"] in allowed and acts_as(msg) in allowed


def refused(msg: dict) -> Optional[str]:
    """Why a phone may not send `msg` although its role may send the type."""
    kind = msg.get("type")
    if kind == "approve_all_confirmations":
        ids = msg.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            # Without them the daemon approves whatever waits when it reads
            # the message, a request that came in after the phone drew its
            # list included.
            return (
                "Approve all must name the requests it approves. Reload the "
                "page and try again."
            )
        return None
    if kind != "set_setting":
        return None
    key, value = msg.get("key"), msg.get("value")
    if key == "CONFIRMATION_MODE":
        # Read as the daemon reads it: "allow_all " is allow_all to it.
        mode, _ = settings.parse(settings.find(key), value)
        if mode == "allow_all":
            return (
                "Approvals can't be turned off from a phone: remote access "
                "needs them on. Change it at the laptop."
            )
    if key == "WAKE_CHIME_PATH" and ("" if value is None else str(value).strip()):
        return (
            "The wake sound is a file on the laptop; from a phone it can only "
            "be restored to the default."
        )
    return None


# Credentials a providers.json entry may hold. `headers` is written by hand
# and may carry one (an `x-api-key`, an `Authorization`).
_PROVIDER_SECRETS = ("api_key", "headers")


def without_keys(providers: Any) -> list:
    """A provider list as a phone gets it: whether each has a key, not the key."""
    shown = []
    for p in providers if isinstance(providers, list) else []:
        if isinstance(p, dict):
            kept = {k: v for k, v in p.items() if k not in _PROVIDER_SECRETS}
            p = {**kept, "api_key_set": bool(p.get("api_key"))}
        shown.append(p)
    return shown


def _without_commands(item: Any) -> Any:
    """A pending confirmation as a guest sees it: which tools, not the commands."""
    if not isinstance(item, dict):
        return item
    return {k: item[k] for k in ("id", "tool_names", "created_at") if k in item}


def _without_digest(session: Any) -> Any:
    """A session as a guest sees it: no LLM-written summary of its turns."""
    if not isinstance(session, dict):
        return session
    return {k: v for k, v in session.items() if k not in ("summary", "metadata")}


def relayable(msg: Any, role: str = "guest") -> bool:
    """True when a daemon message may be shown on a phone signed in as `role`."""
    return isinstance(msg, dict) and msg.get("type") in RELAYED_FOR.get(
        role, frozenset()
    )


def shutdown_notice(msg: dict) -> dict:
    """A shutdown snapshot carries session detail; a phone gets only the state."""
    return {"type": "DAEMON_SHUTDOWN", "state": msg.get("state")}


def shown_to(msg: Any, role: str) -> Optional[dict]:
    """`msg` as a phone signed in as `role` may see it, or None."""
    if not isinstance(msg, dict):
        return None
    if msg.get("type") == "DAEMON_SHUTDOWN":
        return shutdown_notice(msg)
    if not relayable(msg, role):
        return None
    # What JARVIS ran, with its parameters and output, is for the roles that
    # approve it (the action events): not in a session's history, a pending
    # request's details, or the summary of every session's older turns.
    if "action" not in RELAYED_FOR[role]:
        kind = msg.get("type")
        if kind == "session_switched":
            kept = [
                m
                for m in msg.get("messages") or []
                if isinstance(m, dict) and m.get("role") != "action"
            ]
            msg = {
                **msg,
                "session": _without_digest(msg.get("session")),
                "messages": kept,
            }
        elif kind == "session_list":
            sessions = msg.get("sessions")
            if isinstance(sessions, list):
                msg = {**msg, "sessions": [_without_digest(x) for x in sessions]}
        elif kind == "confirmation_request":
            msg = {k: msg[k] for k in ("type", "id", "tools", "timeout") if k in msg}
        elif kind == "confirmation_list":
            # Each pending item's tool_lines are its rendered commands.
            items = msg.get("confirmations")
            if isinstance(items, list):
                msg = {**msg, "confirmations": [_without_commands(c) for c in items]}
    if msg.get("type") == "provider_list":
        msg = {**msg, "providers": without_keys(msg.get("providers"))}
    return msg


def confirmation_mode(settings_reply: dict) -> Optional[str]:
    """The host's mode in a ``settings`` reply; None when it does not say."""
    for entry in settings_reply.get("settings") or []:
        if isinstance(entry, dict) and entry.get("key") == "CONFIRMATION_MODE":
            mode = entry.get("value")
            return mode if isinstance(mode, str) else None
    return None


def unsafe_mode_change(msg: dict) -> bool:
    return (
        msg.get("type") == "config_updated"
        and msg.get("key") == "CONFIRMATION_MODE"
        and msg.get("value") == "allow_all"
    )


class Allowlist:
    """Tailscale logins and their roles, in a 0600 JSON file under the config dir."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict[str, str]:
        try:
            with open(self.path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"web: unreadable allowlist {self.path}: {exc}")
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(login).lower(): role for login, role in data.items() if role in ROLES
        }

    def role_for(self, login: str) -> Optional[str]:
        return self.load().get(login.lower())

    def set(self, login: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"Unknown role '{role}'. Use one of: {', '.join(ROLES)}.")
        entries = self.load()
        entries[login.lower()] = role
        self._save(entries)

    def remove(self, login: str) -> bool:
        entries = self.load()
        if entries.pop(login.lower(), None) is None:
            return False
        self._save(entries)
        return True

    def _save(self, entries: dict[str, str]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(entries, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)


class AuditLog:
    """Append-only JSON lines: who asked for what, from which role."""

    def __init__(self, path: str):
        self.path = path

    def record(self, login: str, role: str, msg: dict) -> None:
        entry: dict[str, Any] = {
            "at": time.time(),
            "login": login,
            "role": role,
            "type": msg.get("type"),
        }
        kind = msg.get("type")
        if kind == "message":
            text, _ = redact_secrets(str(msg.get("content", "")))
            entry["content"] = text[:200]
        elif kind == "set_setting":
            entry["key"] = str(msg.get("key"))[:64]
            entry["value"] = redact_secrets(str(msg.get("value")))[0][:200]
        elif kind in _PROVIDER_CHANGES:
            fields = msg.get("fields") if isinstance(msg.get("fields"), dict) else {}
            # The message's own "type" is not the provider's.
            given = {**{k: v for k, v in msg.items() if k != "type"}, **fields}
            entry["provider"] = {
                k: redact_secrets(str(given[k]))[0][:200]
                for k in ("name", "ptype", "type", "model", "url", "position")
                if given.get(k) is not None
            }
            if given.get("api_key") or given.get("key"):
                entry["key_changed"] = True
        elif kind == "approve_all_confirmations":
            entry["ids"] = [str(i)[:64] for i in msg.get("ids") or []][:100]
        elif "id" in msg:
            entry["id"] = str(msg["id"])[:64]
        if "approved_indices" in msg:
            entry["approved_indices"] = msg["approved_indices"]
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning(f"web: could not write audit log {self.path}: {exc}")


class RateLimit:
    def __init__(self, limit: int = RATE_MAX, window: float = RATE_WINDOW_S):
        self.limit = limit
        self.window = window
        self.stamps: list[float] = []

    def allow(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        self.stamps = [t for t in self.stamps if now - t < self.window]
        if len(self.stamps) >= self.limit:
            return False
        self.stamps.append(now)
        return True


def request_login(headers: Any, dev_login: Optional[str]) -> tuple[Optional[str], str]:
    """The Tailscale account that sent this request, or (None, why not)."""
    if headers.get("Tailscale-Funnel-Request"):
        return None, "Funnel requests are refused"
    if dev_login:
        # A request `tailscale serve` (or any proxy) passed on carries these;
        # one left pointing at this port would otherwise make every tailnet
        # peer the dev owner. Serve sends no identity for a tagged node, but
        # always X-Forwarded-For.
        if any(_proxied(name) for name in headers.keys()):
            return None, "The dev login is for this computer only"
        return dev_login, ""
    login = (headers.get("Tailscale-User-Login") or "").strip()
    if not login:
        return None, "No Tailscale identity on the request"
    return login, ""


def _proxied(header: str) -> bool:
    name = header.lower()
    return name.startswith(("tailscale-", "x-forwarded-")) or name == "forwarded"


def host_ok(headers: Any, dev_login: Optional[str]) -> bool:
    host = (headers.get("Host") or "").split(":")[0].lower()
    if not host:
        return False
    if dev_login:
        return host in ("localhost", "127.0.0.1")
    return host.endswith(Config.WEB_HOST_SUFFIX)


def origin_ok(headers: Any) -> bool:
    """Same-origin only: Origin's host must be exactly the Host header."""
    origin = headers.get("Origin") or ""
    host = headers.get("Host") or ""
    if not origin or not host:
        return False
    for scheme in ("https://", "http://"):
        if origin.startswith(scheme):
            return origin[len(scheme) :].lower() == host.lower()
    return False


async def open_upstream(path: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to jarvis.sock; on Windows the endpoint is a loopback TCP port."""
    if os.path.exists(path):
        return await asyncio.open_unix_connection(path, limit=UPSTREAM_LINE_LIMIT)
    with open(path + ".port") as f:
        port = int(f.read().strip())
    return await asyncio.open_connection("127.0.0.1", port, limit=UPSTREAM_LINE_LIMIT)


class Gateway:
    def __init__(
        self,
        allowlist: Allowlist,
        audit: AuditLog,
        gui_socket: str,
        idle_timeout: int,
        dev_login: Optional[str] = None,
    ):
        self.allowlist = allowlist
        self.audit = audit
        self.gui_socket = gui_socket
        self.idle_timeout = idle_timeout
        self.dev_login = dev_login

    # -- HTTP -----------------------------------------------------------

    def process_request(self, connection: Any, request: Any):
        """Serve the page, or vet the WebSocket upgrade. None lets it through."""
        path = request.path.split("?")[0]
        headers = request.headers

        def refuse(status: HTTPStatus, why: str):
            logger.warning(f"web: refused {path}: {why}")
            return connection.respond(status, why + "\n")

        if not host_ok(headers, self.dev_login):
            return refuse(HTTPStatus.FORBIDDEN, "Unexpected Host")
        login, why = request_login(headers, self.dev_login)
        if login is None:
            return refuse(HTTPStatus.FORBIDDEN, why)
        if self.allowlist.role_for(login) is None:
            return refuse(HTTPStatus.FORBIDDEN, f"{login} is not allowed")

        if path == "/ws":
            if not origin_ok(headers):
                return refuse(HTTPStatus.FORBIDDEN, "Cross-origin request")
            return None

        name = {"/": "index.html"}.get(path, path.lstrip("/"))
        asset = WEB_DIR / name
        if not name or "/" in name or not asset.is_file():
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        response = connection.respond(HTTPStatus.OK, asset.read_text(encoding="utf-8"))
        kind = {
            ".html": "text/html",
            ".js": "text/javascript",
            ".css": "text/css",
        }.get(asset.suffix, "text/plain")
        response.headers["Content-Type"] = f"{kind}; charset=utf-8"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        return response

    # -- WebSocket ------------------------------------------------------

    async def handle(self, ws: Any) -> None:
        login, _ = request_login(ws.request.headers, self.dev_login)
        role = self.allowlist.role_for(login or "")
        if login is None or role is None:
            await ws.close(CLOSE_REFUSED, "Not allowed")
            return
        label = f"web:{login}"

        try:
            reader, writer = await open_upstream(self.gui_socket)
        except (OSError, ValueError):
            await ws.close(CLOSE_UPSTREAM, "JARVIS is not running on the host")
            return

        logger.info(f"web: {login} connected as {role}")
        try:
            # The daemon's latest `state`, as this phone was shown it.
            state: dict = {}
            if not await self._handshake(ws, reader, writer, login, role, label, state):
                return
            await self._relay(ws, reader, writer, login, role, state)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            logger.info(f"web: {login} disconnected")

    async def _handshake(
        self,
        ws: Any,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        login: str,
        role: Optional[str],
        label: str,
        state: dict,
    ) -> bool:
        """Label this client, and refuse to run while confirmations are off.

        What the daemon sends before its settings reply is kept and relayed
        once the session opens: it greets every client with its current
        state, and the page, never told, said "Offline" until the next change.
        """
        await self._upstream_send(
            writer,
            {
                "type": "hello",
                "label": label,
                "subscribe": ["activity", "action", "user_message"],
            },
        )
        await self._upstream_send(writer, {"type": "get_settings"})
        early = []
        try:
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if not line:
                    await ws.close(CLOSE_UPSTREAM, "JARVIS closed the connection")
                    return False
                msg = json.loads(line)
                if msg.get("type") != "settings":
                    early.append(msg)
                    continue
                mode = confirmation_mode(msg)
                if mode != "smart" and mode != "ask_all":
                    await ws.close(
                        CLOSE_UNSAFE,
                        "Remote access needs confirmations on; the host is set to "
                        f"{mode or 'an unknown mode'}",
                    )
                    return False
                break
        except (asyncio.TimeoutError, json.JSONDecodeError, OSError):
            await ws.close(CLOSE_UPSTREAM, "JARVIS did not answer")
            return False
        await ws.send(json.dumps({"type": "gateway", "login": login, "role": role}))
        for msg in early:
            shown = shown_to(msg, role)
            if shown is not None:
                if shown.get("type") == "state":
                    state["last"] = shown
                await ws.send(json.dumps(shown, ensure_ascii=False))
        return True

    async def _relay(
        self,
        ws: Any,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        login: str,
        role: Optional[str],
        state: dict,
    ) -> None:
        last_active = time.monotonic()
        limiter = RateLimit()

        async def refuse(msg: Any, reply: dict) -> None:
            await ws.send(json.dumps(reply))
            # The page shows "working" once it sends a chat line; the daemon
            # never saw this one, so no state of its own will follow.
            if isinstance(msg, dict) and msg.get("type") == "message":
                if state.get("last"):
                    await ws.send(json.dumps(state["last"]))

        async def phone_to_daemon() -> None:
            nonlocal last_active, role
            async for raw in ws:
                role = self.allowlist.role_for(login)
                if role is None:
                    await ws.close(CLOSE_REFUSED, "Access revoked")
                    return
                if isinstance(raw, bytes) or len(raw) > MAX_FRAME:
                    await ws.close(1009, "Message too big")
                    return
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not sendable(role, msg):
                    kind = (
                        acts_as(msg)
                        if isinstance(msg, dict) and isinstance(msg.get("type"), str)
                        else None
                    )
                    await refuse(
                        msg,
                        {
                            "type": "error",
                            "message": f"'{kind}' is not available to a {role}",
                        },
                    )
                    continue
                why = refused(msg)
                if why is not None:
                    reply = (
                        {"type": "config_error", "key": msg.get("key")}
                        if msg["type"] == "set_setting"
                        else {"type": "error"}
                    )
                    await refuse(msg, {**reply, "message": why})
                    continue
                if msg["type"] != "ping":
                    if not limiter.allow():
                        await refuse(msg, {"type": "error", "message": "Slow down"})
                        continue
                    last_active = time.monotonic()
                if msg["type"] in _AUDITED:
                    self.audit.record(login, role, msg)
                await self._upstream_send(writer, msg)

        async def daemon_to_phone() -> None:
            while True:
                line = await reader.readline()
                if not line:
                    await ws.close(CLOSE_UPSTREAM, "JARVIS closed the connection")
                    return
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if unsafe_mode_change(msg):
                    await ws.close(
                        CLOSE_UNSAFE,
                        "The host switched to allow_all; remote access ended",
                    )
                    return
                shown = shown_to(msg, role)
                if shown is None:
                    continue
                if shown.get("type") == "state":
                    state["last"] = shown
                await ws.send(json.dumps(shown, ensure_ascii=False))

        async def watch_idle() -> None:
            while True:
                await asyncio.sleep(min(30, max(1, self.idle_timeout / 4)))
                if time.monotonic() - last_active > self.idle_timeout:
                    await ws.close(CLOSE_IDLE, "Idle timeout; reload to reconnect")
                    return

        tasks = [
            asyncio.create_task(phone_to_daemon()),
            asyncio.create_task(daemon_to_phone()),
            asyncio.create_task(watch_idle()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _upstream_send(writer: asyncio.StreamWriter, msg: dict) -> None:
        writer.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()


def build_gateway(dev_login: Optional[str] = None) -> Gateway:
    return Gateway(
        Allowlist(Config.WEB_ALLOWLIST_FILE),
        AuditLog(Config.WEB_AUDIT_FILE),
        Config.JARVIS_GUI_SOCKET,
        Config.WEB_IDLE_TIMEOUT,
        dev_login,
    )


async def serve_forever(gateway: Gateway, port: int) -> None:
    from websockets.asyncio.server import serve

    if not platform.ipc_verify_owner(gateway.gui_socket) and os.path.exists(
        gateway.gui_socket
    ):
        raise RuntimeError("jarvis.sock is not owned by this user; refusing to relay")
    async with serve(
        gateway.handle,
        HOST,
        port,
        process_request=gateway.process_request,
        max_size=MAX_FRAME,
        server_header=None,
    ):
        await asyncio.Future()
