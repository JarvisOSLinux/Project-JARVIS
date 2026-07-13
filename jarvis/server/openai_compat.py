"""OpenAI-compatible local HTTP endpoint — opt-in, off by default.

SECURITY CONTEXT (read before touching this file)
--------------------------------------------------
``docs/SECURITY-ARCHITECTURE.md`` states, as a security property, that
JARVIS has "no WebSocket gateway, no auth token, and no TCP port
listener" and that this eliminates real OpenClaw CVE classes
(exposed-instance attacks like the ~40k Ollama-style deployments found on
Shodan). This module is the one deliberate exception to that claim, and
it exists specifically so JARVIS can act as a drop-in backend for
OpenAI-compatible clients (aider, continue.dev, etc.) — the same role
Ollama's ``:11434`` or LM Studio's ``:1234`` play.

Because it reopens network-listener attack surface on purpose, it ships
with safeguards that are not optional:

1. **Off by default.** Nothing in this module runs unless
   ``JARVIS_OPENAI_SERVER_ENABLED=true`` is set. ``jarvis run`` never
   starts it otherwise.
2. **Loopback-only by default.** Binding anything other than
   ``127.0.0.1``/``localhost``/``::1`` additionally requires
   ``JARVIS_OPENAI_SERVER_ALLOW_NONLOCAL=true`` — changing the host
   alone is not enough.
3. **Bearer token required on every request.** Generated on first use,
   stored at ``Config.OPENAI_SERVER_TOKEN_FILE`` with ``0600``
   permissions (mirrors the socket-hardening approach in
   ``core/socket_security.py``). There is no anonymous access mode —
   this is the exact gap that made stock Ollama instances routinely
   discoverable on Shodan.
4. **Inference only, no tool execution.** This proxies straight to a
   ``BaseLLMProvider`` (chat/stream_chat) — it does not go through
   ROOT/DISPATCH, MCP tool calls, or the TLA confirmation gate. A
   client hitting this endpoint gets LLM completions, nothing more; it
   cannot make JARVIS run a shell command.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from ..config import Config
from ..core.logger import get_logger
from ..llm.base import BaseLLMProvider

logger = get_logger(__name__)

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _load_or_create_token() -> str:
    """Return the bearer token, generating and persisting one on first use."""
    path = Config.OPENAI_SERVER_TOKEN_FILE

    if os.path.isfile(path):
        with open(path) as f:
            token = f.read().strip()
        if token:
            return token

    token = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(token + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.warning(f"openai_server: could not chmod token file {path}: {exc}")

    return token


class _OpenAICompatHandler(BaseHTTPRequestHandler):
    """Request handler. ``self.server`` carries the provider + token."""

    server: "_OpenAICompatHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("openai_server: " + format, *args)

    def _check_auth(self) -> bool:
        expected = f"Bearer {self.server.token}"
        actual = self.headers.get("Authorization", "")
        if not hmac.compare_digest(actual, expected):
            self._send_json(401, {"error": {"message": "Unauthorized"}})
            return False
        return True

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/v1/models":
            self._send_json(404, {"error": {"message": "not found"}})
            return
        if not self._check_auth():
            return

        provider = self.server.provider
        self._send_json(
            200,
            {
                "object": "list",
                "data": [{"id": provider.model, "object": "model"}],
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "not found"}})
            return
        if not self._check_auth():
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": {"message": "invalid JSON body"}})
            return

        messages = payload.get("messages", [])
        if payload.get("stream"):
            self._handle_stream(messages)
        else:
            self._handle_blocking(messages)

    def _handle_blocking(self, messages: list) -> None:
        provider = self.server.provider
        try:
            text = provider.chat(messages)
        except Exception as e:
            logger.error(f"openai_server: chat error: {e}")
            self._send_json(500, {"error": {"message": str(e)}})
            return

        self._send_json(
            200,
            {
                "id": "chatcmpl-jarvis",
                "object": "chat.completion",
                "model": provider.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": provider.last_prompt_tokens,
                    "completion_tokens": provider.last_completion_tokens,
                },
            },
        )

    def _handle_stream(self, messages: list) -> None:
        provider = self.server.provider
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        try:
            for chunk in provider.stream_chat(messages):
                event = {
                    "id": "chatcmpl-jarvis",
                    "object": "chat.completion.chunk",
                    "model": provider.model,
                    "choices": [{"index": 0, "delta": {"content": chunk}}],
                }
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
        except Exception as e:
            logger.error(f"openai_server: stream error: {e}")

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class _OpenAICompatHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple, provider: BaseLLMProvider, token: str):
        super().__init__(address, _OpenAICompatHandler)
        self.provider = provider
        self.token = token


class OpenAICompatServer:
    """Manages the opt-in OpenAI-compatible endpoint's lifecycle."""

    def __init__(self, provider: BaseLLMProvider):
        self.provider = provider
        self._httpd: Optional[_OpenAICompatHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        """Start the server if enabled and the bind target is safe.

        No-ops silently if ``OPENAI_SERVER_ENABLED`` is false (the
        default) — safe to call unconditionally at startup.
        """
        if not Config.OPENAI_SERVER_ENABLED:
            return
        if self._httpd is not None:
            return

        host = Config.OPENAI_SERVER_HOST
        if host not in _LOOPBACK_HOSTS and not Config.OPENAI_SERVER_ALLOW_NONLOCAL:
            logger.error(
                f"openai_server: JARVIS_OPENAI_SERVER_HOST={host!r} is not "
                "loopback and JARVIS_OPENAI_SERVER_ALLOW_NONLOCAL is not set "
                "— refusing to start. This guard exists because a "
                "non-loopback bind reopens the network attack surface JARVIS "
                "is otherwise designed without. See "
                "docs/SECURITY-ARCHITECTURE.md before overriding it."
            )
            return

        token = _load_or_create_token()
        address = (host, Config.OPENAI_SERVER_PORT)

        try:
            self._httpd = _OpenAICompatHTTPServer(address, self.provider, token)
        except OSError as e:
            logger.error(
                f"openai_server: failed to bind {host}:{Config.OPENAI_SERVER_PORT}: {e}"
            )
            return

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
            name="jarvis-openai-server",
        )
        self._thread.start()

        bound_host, bound_port = self._httpd.server_address[:2]
        logger.warning(
            f"openai_server: ENABLED on http://{bound_host}:{bound_port} — "
            f"bearer token required, stored at {Config.OPENAI_SERVER_TOKEN_FILE} "
            "(0600). This is a deliberate exception to JARVIS's normal "
            "no-network-listener design — see docs/SECURITY-ARCHITECTURE.md."
        )

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
