"""Socket and output-broadcast runtime helpers."""

from __future__ import annotations

import asyncio
import json
import os
from logging import Logger
from typing import Any

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
                "confirmations": app.confirmation.list_pending(),
            },
        )
        return True

    if msg_type == "approve_confirmation":
        app.events.inject_confirmation_response(
            {
                "type": "confirmation_response",
                "id": msg.get("id", ""),
                "approved": True,
            }
        )
        await _gui_write(
            writer, {"type": "ack", "message": f"Approved {msg.get('id', '')}"}
        )
        return True

    if msg_type == "deny_confirmation":
        app.events.inject_confirmation_response(
            {
                "type": "confirmation_response",
                "id": msg.get("id", ""),
                "approved": False,
            }
        )
        await _gui_write(
            writer, {"type": "ack", "message": f"Denied {msg.get('id', '')}"}
        )
        return True

    if msg_type == "approve_all_confirmations":
        ids = [c["id"] for c in app.confirmation.list_pending()]
        for cid in ids:
            app.events.inject_confirmation_response(
                {"type": "confirmation_response", "id": cid, "approved": True}
            )
        await _gui_write(
            writer, {"type": "ack", "message": f"Approved {len(ids)} confirmation(s)"}
        )
        return True

    return False


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
# GUI socket — session CRUD
# ---------------------------------------------------------------------------

SESSION_HISTORY_LIMIT = 200

_ROLE_BY_ENTRY_TYPE = {"user_prompt": "user", "assistant_reply": "assistant"}


def _session_error(message: str) -> dict:
    return {"type": "session_error", "message": message}


def _entries_to_messages(entries: list) -> list:
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
    messages = _entries_to_messages(recall_result.get("entries", []))
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
