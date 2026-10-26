"""Dispatch MCP transport helpers (connect/disconnect/call/signal-log)."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from logging import Logger
from typing import Any, Callable, Dict, List, Optional

from ..config import Config

# The logger name dispatch stamps on pushed-signal notifications (#26).
_PUSH_SIGNAL_LOGGER = "dispatch.signal"


def _normalize_pushed_signal(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map dispatch's raw SignalEntry to the daemon's canonical signal shape.

    Dispatch serializes signals as ``kind``/``message``; the daemon (EventMerger,
    goal_manager, boundary verification) reads ``type``/``data``. Translate here
    so a pushed signal is indistinguishable from a polled one downstream.
    """
    norm: Dict[str, Any] = {
        "type": raw.get("kind"),
        "pid": raw.get("pid"),
        "data": raw.get("message"),
        "timestamp": raw.get("timestamp"),
    }
    if raw.get("nonce") is not None:
        norm["nonce"] = raw.get("nonce")
    if raw.get("payload") is not None:
        norm["payload"] = raw.get("payload")
    return norm


def _make_logging_callback(adapter: Any, logger: Logger):
    """Build the MCP logging-notification handler for pushed dispatch signals.

    dispatch pushes each wakeup-worthy signal as a ``notifications/message`` with
    ``logger="dispatch.signal"``; we normalize it and hand it to the adapter's
    signal sink (EventMerger.enqueue_pushed) — a dict for a single ``fire_wake=true``
    signal, a list for a merged ``fire_wake=false`` batch (#189). The sink is read
    at call time, so it may be unset during early connect — such signals are
    dropped and the poll fallback still catches up (#26).
    """

    async def _on_log_message(params: Any) -> None:
        try:
            if getattr(params, "logger", None) != _PUSH_SIGNAL_LOGGER:
                return
            sink = getattr(adapter, "_signal_sink", None)
            if sink is None:
                logger.debug("Dispatch: pushed signal dropped — sink not registered")
                return
            raw = getattr(params, "data", None)
            if isinstance(raw, list):
                # A merged fire_wake=false batch: hand the whole group over as
                # one unit so ROOT runs it as a single turn (#189).
                batch = [
                    _normalize_pushed_signal(s) for s in raw if isinstance(s, dict)
                ]
                if batch:
                    sink(batch)
            elif isinstance(raw, dict):
                sink(_normalize_pushed_signal(raw))
            else:
                logger.debug(
                    "Dispatch: pushed signal had no dict/list payload — ignoring"
                )
        except Exception as e:  # never let a bad push break the session
            logger.error(f"Dispatch: error handling pushed signal: {e}")

    return _on_log_message


# The SDK's default environment is HOME, LOGNAME, PATH, SHELL and USER only.
# Servers dispatch runs through dmcp also need the desktop session: a sign-in
# wrapper opens its link in the browser (DISPLAY / WAYLAND_DISPLAY) and keeps
# tokens in the keyring over the session bus. Without these, no browser opened
# and secret-tool failed ("Cannot autolaunch D-Bus without X11 $DISPLAY"), so
# tokens fell back to a file.
_DESKTOP_SESSION_VARS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
)


def _desktop_session_env() -> Dict[str, str]:
    return {k: os.environ[k] for k in _DESKTOP_SESSION_VARS if os.environ.get(k)}


async def connect(adapter: Any, logger: Logger) -> None:
    """Connect DispatchAdapter to dispatch serve over stdio MCP."""
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import get_default_environment, stdio_client

        logger.info(f"Dispatch: Spawning '{Config.DISPATCH_BINARY} serve'")
        params = StdioServerParameters(
            command=Config.DISPATCH_BINARY,
            args=["serve"],
            # Merge onto the SDK's default env (PATH, HOME, ...) rather than
            # replacing it: a bare env= drops PATH, so the spawned process
            # resolves binaries against os.defpath only and can't find
            # ~/.local/bin or Homebrew installs (#170).
            env={
                **get_default_environment(),
                **_desktop_session_env(),
                "RUST_LOG": "dispatch=warn",
            },
        )
        from ..core.logger import JarvisLogger

        # Its stderr must not reach the terminal: the TUI draws there.
        adapter._errlog = JarvisLogger.child_stderr("dispatch")
        client = stdio_client(params, errlog=adapter._errlog)
        read, write = await client.__aenter__()
        adapter._client = client
        try:
            # Register the logging handler so dispatch's pushed signals
            # (notifications/message, logger="dispatch.signal") wake ROOT the
            # moment a task completes, rather than waiting for the next poll (#26).
            session = ClientSession(
                read,
                write,
                logging_callback=_make_logging_callback(adapter, logger),
            )
            await session.__aenter__()
            adapter.session = session
            await session.initialize()
        except BaseException:
            # Quitting while JARVIS starts cancels this mid-handshake. The
            # transport must be closed here, in the task that opened it: left
            # open, its stdout reader waits on `dispatch serve` forever and
            # asyncio.run never returns, so the terminal hangs after the TUI
            # exits.
            await disconnect(adapter, logger)
            raise
        adapter._connected = True
        logger.info("Dispatch: Connected successfully")
    except FileNotFoundError:
        msg = (
            f"Dispatch: binary '{Config.DISPATCH_BINARY}' not found in PATH.\n"
            "  Build the Rust binaries first:\n"
            "    cd deps/rust/dispatch && cargo build --release\n"
            "    cp target/release/dispatch ~/.local/bin/\n"
            "  Or set DISPATCH_BINARY=/path/to/dispatch in ~/.config/jarvis/jarvis.conf\n"
            "  JARVIS will run in conversation-only mode without dispatch."
        )
        logger.warning(msg)
        raise FileNotFoundError(msg)
    except Exception as e:
        logger.error(
            f"Dispatch: Connection failed (binary='{Config.DISPATCH_BINARY}'): {e}"
        )
        raise


async def disconnect(adapter: Any, logger: Logger) -> None:
    """Disconnect DispatchAdapter session/client safely; a second call is a no-op.

    The transport is closed even when closing the session fails: it is what
    stops `dispatch serve` and releases the stdout reader.
    """
    session, client = adapter.session, adapter._client
    adapter.session = adapter._client = None
    adapter._connected = False
    for name, ctx in (("session", session), ("transport", client)):
        if ctx is None:
            continue
        try:
            await ctx.__aexit__(None, None, None)
        except Exception as e:
            logger.error(f"Dispatch: Disconnect error ({name}): {e}")
    errlog = getattr(adapter, "_errlog", None)
    adapter._errlog = None
    if errlog is not None and errlog is not sys.__stderr__:
        errlog.close()
    if client is not None:
        logger.info("Dispatch: Disconnected")


def require_connection(adapter: Any, logger: Logger, op_name: str) -> bool:
    """Return False and log if adapter is disconnected."""
    if adapter._connected:
        return True
    logger.warning(f"Dispatch: {op_name} called but not connected")
    return False


_POLLED_OPS = frozenset({"get_task_status"})


async def call_tool(
    adapter: Any,
    logger: Logger,
    *,
    tool_name: str,
    params: Dict[str, Any],
    op_name: str,
    timeout_error: str,
    failure_prefix: str,
    extractor: Callable[[Any], Dict[str, Any]],
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Call a dispatch MCP tool with timeout and normalized logging.

    ``timeout`` overrides ``adapter.timeout`` for this call only.
    Pass a large value (e.g. 600.0) for tools that intentionally block
    until an event fires (e.g. 'wait').
    """
    effective_timeout = adapter.timeout if timeout is None else timeout
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            adapter.session.call_tool(tool_name, params),
            timeout=effective_timeout,
        )
        elapsed = time.perf_counter() - t0
        content = extractor(result)
        # Status is polled (every second while F4 is open): at INFO it rotated
        # a 5 MB jarvis.log in two minutes.
        log = logger.debug if op_name in _POLLED_OPS else logger.info
        log(f"Dispatch: {op_name} completed in {elapsed:.2f}s — result: {content}")
        return content
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - t0
        logger.error(
            f"Dispatch: {op_name} timed out after {elapsed:.2f}s (limit={effective_timeout}s)"
        )
        return {"error": timeout_error.format(timeout=effective_timeout)}
    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error(f"Dispatch: {op_name} failed after {elapsed:.2f}s — {e}")
        return {"error": f"{failure_prefix}: {e}"}


async def get_signal_window(adapter: Any, logger: Logger) -> List[Dict[str, Any]]:
    """Read dispatch's signal window via its MCP log tool, in the pushed shape.

    The entries come from ``structuredContent``: each is the SignalEntry JSON a
    push carries, so a signal seen both ways gets one (pid, type, timestamp)
    key, and the nonce boundary verification trusts stays its own field. The
    text dispatch also returns cannot give either. An EXIT dispatch holds for a
    fire_wake=false batch keeps ``held``.
    """
    if not adapter._connected:
        return []

    try:
        result = await asyncio.wait_for(
            adapter.session.call_tool("log", {}),
            timeout=adapter.timeout,
        )
    except Exception as e:
        logger.error(f"Dispatch: Failed to get signals: {e}")
        return []
    content = adapter._extract_content(result)
    entries = content.get("entries") if isinstance(content, dict) else None
    if not isinstance(entries, list):
        if not getattr(adapter, "_warned_unstructured_log", False):
            adapter._warned_unstructured_log = True
            logger.warning(
                "Dispatch: its log tool returns no structured entries, so a signal "
                "that was not pushed (a KILL, a lost push) never reaches JARVIS; "
                "update dispatch"
            )
        return []
    signals = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        sig = _normalize_pushed_signal(raw)
        if raw.get("held"):
            sig["held"] = True
        signals.append(sig)
    return signals
