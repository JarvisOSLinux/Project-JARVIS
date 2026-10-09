"""Socket and output-broadcast runtime helpers."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from logging import Logger
from typing import Any, Optional

from ..config import Config
from ..core.providers import (
    add_provider,
    edit_provider,
    list_providers,
    remove_provider,
)
from ..core.voice_state import VoiceState
from ..platform import current as platform
from ..voice.chime import validate_chime_path


async def run_socket_listener(app: Any, logger: Logger) -> None:
    """Listen on IPC endpoint for external text/JSON input."""
    path = Config.JARVIS_INPUT_SOCKET
    if not path:
        return
    server = await platform.create_ipc_server(
        path, lambda r, w: handle_socket_connection(app, logger, r, w)
    )
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        server.close()
        await server.wait_closed()
        platform.ipc_cleanup(path)


async def handle_socket_connection(
    app: Any,
    logger: Logger,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle one input socket connection; route lines into the event loop."""
    if not await platform.ipc_verify_peer(reader, writer):
        logger.warning("JARVIS: Rejected socket connection — peer verification failed")
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return
    try:
        while app._running:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            # Check for confirmation responses / queries — handled here,
            # never treated as chat input.
            if text.startswith("{"):
                try:
                    msg = json.loads(text)
                    handled = await _handle_confirmation_query(app, msg, writer)
                    if handled:
                        continue
                    if msg.get("type") == "confirmation_response":
                        app.events.inject_confirmation_response(msg)
                        continue
                except json.JSONDecodeError:
                    pass

            app.events.inject_user_input(text)
            logger.info(f"JARVIS: Socket input: {text[:80]}...")
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def confirmation_history_message(app: Any) -> dict[str, Any]:
    """Recently resolved confirmations, newest first (#235)."""
    return {"type": "confirmation_history", "history": app.confirmation.list_history()}


def enrich_pending_with_goals(app: Any) -> list[dict[str, Any]]:
    """list_pending() plus each item's owning goal description (#192).

    ``session_id`` is the goal id (#190); resolve it here rather than in
    ConfirmationManager, which has no GoalManager reference.
    """
    pending = app.confirmation.list_pending()
    goals = getattr(app, "goals", None)
    for item in pending:
        goal = (
            goals.get_goal(item["session_id"])
            if goals and item.get("session_id")
            else None
        )
        item["goal_description"] = goal.description if goal else None
    return pending


async def _handle_confirmation_query(
    app: Any, msg: dict, writer: asyncio.StreamWriter
) -> bool:
    """Handle list/approve/deny confirmation queries, shared by both sockets.

    Returns True if `msg` was a confirmation query and has been handled (the
    caller should not fall through to its own message routing for it).
    """
    msg_type = msg.get("type")

    if msg_type == "list_confirmations":
        await _gui_write(
            writer,
            {
                "type": "confirmation_list",
                "confirmations": enrich_pending_with_goals(app),
            },
        )
        return True

    if msg_type == "list_confirmation_history":
        await _gui_write(writer, confirmation_history_message(app))
        return True

    ack = apply_confirmation_decision(app, msg)
    if ack is None:
        return False
    await _gui_write(writer, {"type": "ack", "message": ack})
    return True


def apply_confirmation_decision(app: Any, msg: dict) -> str | None:
    """Turn one approve/deny protocol message into injected event(s).

    Returns the ack text, or None if `msg` is not a decision. This is the sole
    translation from the wire protocol to `CONFIRMATION_RESPONSE` events, so
    every client — socket, GUI, and the offline queue replayed at startup —
    resolves through identical injections.
    """
    msg_type = msg.get("type")

    if msg_type == "approve_confirmation":
        app.events.inject_confirmation_response(
            {
                "type": "confirmation_response",
                "id": msg.get("id", ""),
                "approved": True,
            }
        )
        return f"Approved {msg.get('id', '')}"

    if msg_type == "deny_confirmation":
        app.events.inject_confirmation_response(
            {
                "type": "confirmation_response",
                "id": msg.get("id", ""),
                "approved": False,
            }
        )
        return f"Denied {msg.get('id', '')}"

    if msg_type == "partial_approve_confirmation":
        # Per-task decision (#187): approve a subset of a batched confirmation
        # by index; the rest are denied. `approved_indices` is a list of ints
        # into the confirmation's items (as sent in the request `details`).
        indices = msg.get("approved_indices", [])
        app.events.inject_confirmation_response(
            {
                "type": "confirmation_response",
                "id": msg.get("id", ""),
                "approved_indices": indices,
            }
        )
        return f"Resolved {msg.get('id', '')} ({len(indices)} approved)"

    if msg_type == "approve_all_confirmations":
        ids = [c["id"] for c in app.confirmation.list_pending()]
        for cid in ids:
            app.events.inject_confirmation_response(
                {"type": "confirmation_response", "id": cid, "approved": True}
            )
        return f"Approved {len(ids)} confirmation(s)"

    return None


def on_output_for_broadcast(app: Any, response: dict[str, Any]) -> None:
    """Schedule async broadcast of response to output subscribers."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_to_output_clients(app, response))
    except RuntimeError:
        pass


async def broadcast_to_output_clients(app: Any, response: dict[str, Any]) -> None:
    """Send response JSON line to all connected output clients."""
    line = json.dumps(response, ensure_ascii=False) + "\n"
    data = line.encode("utf-8")
    dead: list[asyncio.StreamWriter] = []
    for writer in app._output_clients:
        try:
            writer.write(data)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            dead.append(writer)
    for writer in dead:
        if writer in app._output_clients:
            app._output_clients.remove(writer)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def run_output_socket_listener(app: Any, logger: Logger) -> None:
    """Listen on IPC endpoint for output subscribers (apps/widgets)."""
    path = Config.JARVIS_OUTPUT_SOCKET
    if not path:
        return
    server = await platform.create_ipc_server(
        path, lambda r, w: handle_output_connection(app, logger, r, w)
    )
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        app._output_clients.clear()
        server.close()
        await server.wait_closed()
        platform.ipc_cleanup(path)


async def handle_output_connection(
    app: Any,
    logger: Logger,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle output subscriber lifecycle."""
    if not await platform.ipc_verify_peer(reader, writer):
        logger.warning("JARVIS: Rejected output connection — peer verification failed")
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return

    app._output_clients.append(writer)
    logger.info(
        f"JARVIS: Output subscriber connected ({len(app._output_clients)} total)"
    )
    try:
        await reader.read()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        if writer in app._output_clients:
            app._output_clients.remove(writer)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        logger.debug("JARVIS: Output subscriber disconnected")


# ---------------------------------------------------------------------------
# GUI socket — bidirectional structured JSON for desktop apps
# ---------------------------------------------------------------------------


async def run_gui_socket_listener(app: Any, logger: Logger) -> None:
    """Listen on IPC endpoint for GUI app connections (bidirectional)."""
    path = Config.JARVIS_GUI_SOCKET
    if not path:
        return
    server = await platform.create_ipc_server(
        path, lambda r, w: handle_gui_connection(app, logger, r, w)
    )
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        app._gui_clients.clear()
        app._gui_capabilities.clear()
        cancel_all_config_requests(app)
        server.close()
        await server.wait_closed()
        platform.ipc_cleanup(path)


DEFAULT_CLIENT_LABEL = "unlabeled"


async def handle_gui_connection(
    app: Any,
    logger: Logger,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Handle a bidirectional GUI client connection."""
    if not await platform.ipc_verify_peer(reader, writer):
        logger.warning("JARVIS: Rejected GUI connection — peer verification failed")
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return

    app._gui_clients[writer] = DEFAULT_CLIENT_LABEL
    logger.info(f"JARVIS: GUI client connected ({len(app._gui_clients)} total)")

    try:
        await _gui_write(writer, {"type": "state", "state": app._gui_state})
    except Exception:
        pass

    try:
        while app._running:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=60.0)
            except asyncio.TimeoutError:
                try:
                    await _gui_write(writer, {"type": "ping"})
                except Exception:
                    break
                continue

            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue

            await _process_gui_message(app, logger, msg, writer)

    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        app._gui_clients.pop(writer, None)
        app._gui_capabilities.pop(writer, None)
        drop_config_waiter(app, writer)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        logger.info("JARVIS: GUI client disconnected")


async def _process_gui_message(
    app: Any,
    logger: Logger,
    msg: dict,
    writer: asyncio.StreamWriter,
) -> None:
    """Route an incoming GUI protocol message."""
    msg_type = msg.get("type")

    if await _handle_confirmation_query(app, msg, writer):
        return

    if msg_type == "message":
        content = msg.get("content", "").strip()
        if content:
            await set_gui_state(app, VoiceState.PROCESSING)
            app.events.inject_user_input(content)
            logger.info(f"JARVIS: GUI input: {content[:80]}...")

    elif msg_type == "confirmation_response":
        app.events.inject_confirmation_response(msg)

    elif msg_type == "hello":
        label = str(msg.get("label", "")).strip()
        if label:
            app._gui_clients[writer] = label
            logger.info(f"JARVIS: GUI client identified as '{label}'")
        capabilities = msg.get("capabilities")
        if isinstance(capabilities, list):
            app._gui_capabilities[writer] = {
                c for c in capabilities if isinstance(c, str)
            }

    elif msg_type == "get_status":
        await _gui_write(writer, build_status(app))

    elif msg_type == "get_history":
        await _gui_write(writer, _handle_get_history(app, msg))

    elif msg_type == "config_response":
        resolve_config_request(app, logger, msg, writer)

    elif msg_type == "list_clients":
        await _gui_write(
            writer,
            {"type": "client_list", "clients": list(app._gui_clients.values())},
        )

    elif msg_type == "shutdown_request":
        logger.info("JARVIS: shutdown_request received over the GUI socket")
        app.stop()

    elif msg_type == "start_listening":
        # Toggles whether the wake-word listener is enabled at all -- a
        # separate, orthogonal concept from the WOKEN/CAPTURING/PROCESSING/
        # SPEAKING session lifecycle in VoiceState, so it keeps its own
        # plain-string state rather than reusing VoiceState.IDLE.
        if app.voice_manager and hasattr(app.voice_manager, "activation"):
            app.voice_manager.activation.start_listening()
        await set_gui_state(app, "listening")

    elif msg_type == "stop_listening":
        if app.voice_manager and hasattr(app.voice_manager, "activation"):
            app.voice_manager.activation.stop_listening()
        await set_gui_state(app, "idle")

    elif msg_type == "stop_stream":
        await broadcast_to_gui_clients(
            app, {"type": "response", "content": "", "done": True}
        )
        await set_gui_state(app, VoiceState.IDLE)

    elif msg_type == "set_wake_chime_path":
        await _handle_set_wake_chime_path(app, msg, writer)

    elif msg_type == "reset_wake_chime_path":
        await _handle_reset_wake_chime_path(app, writer)

    elif msg_type == "get_settings":
        await _gui_write(
            writer,
            {
                "type": "settings",
                "confirmation_mode": Config.CONFIRMATION_MODE,
                "wake_chime_path": Config.WAKE_CHIME_PATH,
            },
        )

    elif msg_type == "set_confirmation_mode":
        await _handle_set_confirmation_mode(app, msg, writer)

    elif msg_type == "list_providers":
        await _gui_write(
            writer, {"type": "provider_list", "providers": list_providers()}
        )

    elif msg_type == "add_provider":
        await _reply_or_broadcast_providers(app, writer, _handle_add_provider(msg))

    elif msg_type == "edit_provider":
        await _reply_or_broadcast_providers(app, writer, _handle_edit_provider(msg))

    elif msg_type == "remove_provider":
        await _reply_or_broadcast_providers(app, writer, _handle_remove_provider(msg))

    elif msg_type == "list_sessions":
        await _gui_write(writer, _handle_list_sessions(app, msg))

    elif msg_type == "create_session":
        await _reply_or_broadcast(app, writer, _handle_create_session(app, msg))

    elif msg_type == "switch_session":
        await _reply_or_broadcast(app, writer, _handle_switch_session(app, msg))

    elif msg_type == "rename_session":
        await _reply_or_broadcast(app, writer, _handle_rename_session(app, msg))

    elif msg_type == "delete_session":
        await _reply_or_broadcast(app, writer, _handle_delete_session(app, msg))

    elif msg_type == "ping":
        try:
            await _gui_write(writer, {"type": "pong"})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GUI socket — wake chime config
# ---------------------------------------------------------------------------


async def _handle_set_wake_chime_path(
    app: Any, msg: dict, writer: asyncio.StreamWriter
) -> None:
    """Validated, single-purpose config write for WAKE_CHIME_PATH only --
    deliberately NOT a generic "set any config key" message. Exposing an
    unscoped config writer over the GUI socket would let any local client
    silently rewrite security-relevant settings (e.g. CONFIRMATION_MODE).
    """
    path = msg.get("path", "")
    error = validate_chime_path(path)
    if error:
        await _gui_write(
            writer, {"type": "config_error", "key": "WAKE_CHIME_PATH", "message": error}
        )
        return

    from ..cli import _update_env_setting

    _update_env_setting("WAKE_CHIME_PATH", path)
    await broadcast_to_gui_clients(
        app, {"type": "config_updated", "key": "WAKE_CHIME_PATH", "value": path}
    )


async def _handle_reset_wake_chime_path(app: Any, writer: asyncio.StreamWriter) -> None:
    """Restore WAKE_CHIME_PATH to the bundled default -- the settings-panel
    counterpart to `_handle_set_wake_chime_path`'s custom-path write."""
    from ..cli import _update_env_setting

    default = Config.DEFAULT_WAKE_CHIME_PATH
    _update_env_setting("WAKE_CHIME_PATH", default)
    await broadcast_to_gui_clients(
        app, {"type": "config_updated", "key": "WAKE_CHIME_PATH", "value": default}
    )


_VALID_CONFIRMATION_MODES = ("smart", "ask_all", "allow_all")


async def _handle_set_confirmation_mode(
    app: Any, msg: dict, writer: asyncio.StreamWriter
) -> None:
    """Validated, single-purpose config write for CONFIRMATION_MODE only --
    same rationale as `_handle_set_wake_chime_path`: no generic config setter
    over the GUI socket, only specific, whitelisted, validated keys.
    """
    mode = msg.get("mode", "")
    if mode not in _VALID_CONFIRMATION_MODES:
        await _gui_write(
            writer,
            {
                "type": "config_error",
                "key": "CONFIRMATION_MODE",
                "message": f"Invalid mode '{mode}'. Use one of: "
                f"{', '.join(_VALID_CONFIRMATION_MODES)}.",
            },
        )
        return

    from ..cli import _update_env_setting

    _update_env_setting("CONFIRMATION_MODE", mode)
    await broadcast_to_gui_clients(
        app, {"type": "config_updated", "key": "CONFIRMATION_MODE", "value": mode}
    )


# ---------------------------------------------------------------------------
# GUI socket — provider CRUD
# ---------------------------------------------------------------------------


def _provider_error(message: str) -> dict:
    return {"type": "provider_error", "message": message}


def _handle_add_provider(msg: dict) -> dict:
    try:
        add_provider(
            msg.get("ptype", ""),
            msg.get("model", ""),
            name=msg.get("name") or None,
            url=msg.get("url") or None,
            api_key=msg.get("api_key") or None,
            temperature=msg.get("temperature"),
        )
    except ValueError as e:
        return _provider_error(str(e))
    return {"type": "provider_list", "providers": list_providers()}


def _handle_edit_provider(msg: dict) -> dict:
    name = msg.get("name", "")
    fields = msg.get("fields") or {}
    if not name or not fields:
        return _provider_error("edit_provider requires 'name' and 'fields'")
    try:
        edit_provider(name, **fields)
    except ValueError as e:
        return _provider_error(str(e))
    return {"type": "provider_list", "providers": list_providers()}


def _handle_remove_provider(msg: dict) -> dict:
    name = msg.get("name", "")
    if not name:
        return _provider_error("remove_provider requires 'name'")
    try:
        remove_provider(name)
    except ValueError as e:
        return _provider_error(str(e))
    return {"type": "provider_list", "providers": list_providers()}


async def _reply_or_broadcast_providers(
    app: Any, writer: asyncio.StreamWriter, response: dict
) -> None:
    """Provider config is shared daemon state (providers.json), so a
    successful mutation broadcasts the refreshed list to every GUI client,
    matching `_reply_or_broadcast`'s session-CRUD pattern; errors are
    specific to the requester's malformed/invalid request."""
    if response.get("type") == "provider_error":
        await _gui_write(writer, response)
    else:
        await broadcast_to_gui_clients(app, response)


# ---------------------------------------------------------------------------
# GUI socket — status, activity, history, and the server-config form relay
# (#236: the TUI is a client of this socket, so what it used to read from the
# in-process engine has to be askable over the wire.)
# ---------------------------------------------------------------------------

CONFIG_FORM_CAPABILITY = "config_form"


def build_status(app: Any) -> dict:
    """What a client shows in its status line: session, model, context use."""
    session = app.sessions.current
    llm = getattr(app, "llm", None)
    provider = getattr(llm, "provider", None)
    return {
        "type": "status",
        "session": (
            {
                "id": session.id,
                "short_id": session.short_id(),
                "title": session.title,
            }
            if session
            else None
        ),
        "llm_configured": llm is not None,
        "provider": getattr(provider, "active_provider_name", None),
        "model": getattr(provider, "model", None),
        "prompt_tokens": getattr(provider, "last_prompt_tokens", 0) or 0,
        "context_window": getattr(Config, "LLM_CONTEXT_WINDOW", 0),
    }


def _handle_get_history(app: Any, msg: dict) -> dict:
    """A session's transcript, to the requester only (unlike switch_session,
    it changes no daemon state and is not broadcast)."""
    if not app.sessions.available:
        return _session_error("Sessions unavailable (memory disabled)")
    session = app.sessions.current
    session_id = msg.get("id") or (session.id if session else "")
    if not session_id:
        return {"type": "history", "session": None, "messages": []}
    limit = msg.get("limit", SESSION_HISTORY_LIMIT)
    recall_result = app.contextor.recall(
        "conversation_log", limit=limit, session_id=session_id
    )
    return {
        "type": "history",
        "session": session.to_dict() if session and session.id == session_id else None,
        "messages": entries_to_messages(recall_result.get("entries", [])),
    }


def on_gui_activity(app: Any, event: dict[str, Any]) -> None:
    """Forward a runtime activity line to GUI clients."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    message = {"type": "activity", **event}
    loop.create_task(broadcast_to_gui_clients(app, message))


async def request_config_form(
    app: Any,
    server_id: str,
    name: str,
    description: str,
    props: list,
    saved: dict,
) -> Optional[dict]:
    """Ask a client that declared the ``config_form`` capability to collect a
    server's configuration, so secrets are typed into a form rather than the
    conversation (#242).

    Returns ``{"confirmed", "values", "missing_required"}`` once a client
    answers, or None when no capable client is attached. Cancelled (not
    hung) if every client asked disconnects before answering.
    """
    targets = {
        w
        for w, caps in app._gui_capabilities.items()
        if CONFIG_FORM_CAPABILITY in caps and w in app._gui_clients
    }
    if not targets:
        return None
    request_id = uuid.uuid4().hex
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    app._config_requests[request_id] = (future, targets)
    message = {
        "type": "config_request",
        "request_id": request_id,
        "server_id": server_id,
        "name": name,
        "description": description,
        "props": props,
        "saved": saved,
    }
    for writer in list(targets):
        try:
            await _gui_write(writer, message)
        except (ConnectionError, OSError):
            drop_config_waiter(app, writer)
    try:
        return await future
    finally:
        app._config_requests.pop(request_id, None)


def resolve_config_request(
    app: Any, logger: Logger, msg: dict, writer: asyncio.StreamWriter
) -> None:
    """Complete a pending config request from a ``config_response``.

    The values are never logged. A response from a client that was not asked,
    for an unknown id, or arriving after another client answered is ignored.
    """
    entry = app._config_requests.get(str(msg.get("request_id", "")))
    if entry is None:
        return
    future, targets = entry
    if writer not in targets or future.done():
        return
    values = msg.get("values")
    missing = msg.get("missing_required")
    future.set_result(
        {
            "confirmed": msg.get("confirmed") is True,
            "values": values if isinstance(values, dict) else {},
            "missing_required": missing if isinstance(missing, list) else [],
        }
    )
    logger.info("JARVIS: config form answered by a GUI client")


def _cancelled_form() -> dict:
    return {"confirmed": False, "values": {}, "missing_required": []}


def drop_config_waiter(app: Any, writer: asyncio.StreamWriter) -> None:
    """A client left: stop waiting on it, and cancel any request it was the
    last remaining target of."""
    for future, targets in list(app._config_requests.values()):
        targets.discard(writer)
        if not targets and not future.done():
            future.set_result(_cancelled_form())


def cancel_all_config_requests(app: Any) -> None:
    for future, _ in list(app._config_requests.values()):
        if not future.done():
            future.set_result(_cancelled_form())


# ---------------------------------------------------------------------------
# GUI socket — session CRUD
# ---------------------------------------------------------------------------

SESSION_HISTORY_LIMIT = 200

_ROLE_BY_ENTRY_TYPE = {"user_prompt": "user", "assistant_reply": "assistant"}


def _session_error(message: str) -> dict:
    return {"type": "session_error", "message": message}


def entries_to_messages(entries: list) -> list:
    """Map conversation_log entries (chronological) to {role, content, timestamp}."""
    messages = []
    for entry in entries:
        role = _ROLE_BY_ENTRY_TYPE.get((entry.get("metadata") or {}).get("type"))
        if not role:
            continue
        messages.append(
            {
                "role": role,
                "content": entry.get("content", ""),
                "timestamp": entry.get("stored_at"),
            }
        )
    return messages


def _handle_list_sessions(app: Any, msg: dict) -> dict:
    if not app.sessions.available:
        return _session_error("Sessions unavailable (memory disabled)")
    limit = msg.get("limit", 50)
    offset = msg.get("offset", 0)
    sessions = app.sessions.list(limit=limit, offset=offset)
    return {"type": "session_list", "sessions": [s.to_dict() for s in sessions]}


def _handle_create_session(app: Any, msg: dict) -> dict:
    if not app.sessions.available:
        return _session_error("Sessions unavailable (memory disabled)")
    session = app.sessions.new_session(title=msg.get("title") or None)
    if not session:
        return _session_error("Failed to create session")
    return {"type": "session_switched", "session": session.to_dict(), "messages": []}


def _handle_switch_session(app: Any, msg: dict) -> dict:
    session_id = msg.get("id", "")
    if not session_id:
        return _session_error("switch_session requires 'id'")
    if not app.sessions.available:
        return _session_error("Sessions unavailable (memory disabled)")
    session = app.sessions.switch(session_id)
    if not session:
        return _session_error(f"No session matches '{session_id}'")
    recall_result = app.contextor.recall(
        "conversation_log", limit=SESSION_HISTORY_LIMIT, session_id=session.id
    )
    messages = entries_to_messages(recall_result.get("entries", []))
    return {
        "type": "session_switched",
        "session": session.to_dict(),
        "messages": messages,
    }


def _handle_rename_session(app: Any, msg: dict) -> dict:
    session_id = msg.get("id", "")
    title = msg.get("title", "")
    if not session_id or not title:
        return _session_error("rename_session requires 'id' and 'title'")
    if not app.sessions.available:
        return _session_error("Sessions unavailable (memory disabled)")
    if not app.sessions.rename(title, session_id=session_id):
        return _session_error(f"Rename failed for '{session_id}'")
    sessions = app.sessions.list(limit=50)
    return {"type": "session_list", "sessions": [s.to_dict() for s in sessions]}


def _handle_delete_session(app: Any, msg: dict) -> dict:
    session_id = msg.get("id", "")
    if not session_id:
        return _session_error("delete_session requires 'id'")
    if not app.sessions.available:
        return _session_error("Sessions unavailable (memory disabled)")
    if not app.sessions.delete(session_id):
        return _session_error(f"Delete failed for '{session_id}'")
    sessions = app.sessions.list(limit=50)
    return {"type": "session_list", "sessions": [s.to_dict() for s in sessions]}


async def _reply_or_broadcast(
    app: Any, writer: asyncio.StreamWriter, response: dict
) -> None:
    """Session mutations reflect shared daemon state, so success broadcasts
    to every GUI client; errors are specific to the requester's malformed
    request and go back to them alone."""
    if response.get("type") == "session_error":
        await _gui_write(writer, response)
    else:
        await broadcast_to_gui_clients(app, response)


async def set_gui_state(
    app: Any, state: VoiceState | str, meta: dict | None = None
) -> None:
    """Update tracked GUI state and broadcast to all GUI clients.

    `state` is normally a VoiceState member; the manual start_listening/
    stop_listening toggle passes a plain string instead (see its call site).
    `meta` carries transition-specific detail (e.g. a discard reason) and is
    omitted from the wire payload entirely when empty, keeping the common
    case a plain `{"type": "state", "state": ...}` message.
    """
    state_value = state.value if isinstance(state, VoiceState) else state
    app._gui_state = state_value
    message: dict[str, Any] = {"type": "state", "state": state_value}
    if meta:
        message["meta"] = meta
    await broadcast_to_gui_clients(app, message)


def on_gui_output(app: Any, response: dict[str, Any]) -> None:
    """Schedule async broadcast of response to GUI clients."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_gui_output_and_idle(app, response))
    except RuntimeError:
        pass


async def _gui_output_and_idle(app: Any, response: dict[str, Any]) -> None:
    """Translate daemon output into GUI protocol and reset state to idle.

    Skips the idle reset when the response is about to be spoken -- the
    OutputManager's own SPEAKING/IDLE bracket around the actual TTS call
    (wired in lifecycle.py) is the accurate signal for when speech ends,
    and firing idle here first would just flash it prematurely.
    """
    text = response.get("output", "")
    if text:
        await broadcast_to_gui_clients(
            app, {"type": "response", "content": text, "done": True}
        )
    about_to_speak = Config.OUTPUT_MODE == "voice" and app.output_manager.has_tts()
    if not about_to_speak:
        await set_gui_state(app, VoiceState.IDLE)


async def broadcast_to_gui_clients(app: Any, message: dict) -> None:
    """Send a JSON message to all connected GUI clients."""
    if not app._gui_clients:
        return
    data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    dead: set[asyncio.StreamWriter] = set()
    for writer in list(app._gui_clients):
        try:
            writer.write(data)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            dead.add(writer)
    for writer in dead:
        app._gui_clients.pop(writer, None)


async def _gui_write(writer: asyncio.StreamWriter, message: dict) -> None:
    data = (json.dumps(message) + "\n").encode("utf-8")
    writer.write(data)
    await writer.drain()
