"""Ollama-compatible local HTTP endpoint — opt-in, off by default.

Lets Ollama clients (Open WebUI, the ``ollama`` Python/JS libraries, editor
plugins) talk to JARVIS the way they talk to ``ollama serve``. It listens on
``:11456`` by default so it can run beside a real Ollama on ``:11434``.

SECURITY CONTEXT (read ``openai_compat.py``'s docstring first)
---------------------------------------------------------------
This is a second deliberate exception to JARVIS having no TCP listener, with
the same safeguards except one:

1. **Off by default** (``JARVIS_OLLAMA_SERVER_ENABLED=true`` to start it).
2. **Loopback-only by default.** Any other bind also needs
   ``JARVIS_OLLAMA_SERVER_ALLOW_NONLOCAL=true``.
3. **Token only off-loopback.** Ollama clients cannot send a bearer token, so
   a loopback bind is open to every local user, exactly like Ollama. A
   non-loopback bind requires the same bearer token as the OpenAI endpoint.
4. **Inference only.** It proxies to a ``BaseLLMProvider`` and does not go
   through ROOT/DISPATCH, MCP tools, or the TLA confirmation gate.
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator, Optional

from ..config import Config
from ..core.logger import get_logger
from ..llm.base import BaseLLMProvider
from .openai_compat import _LOOPBACK_HOSTS, _load_or_create_token

logger = get_logger(__name__)

# Reported by /api/version. Clients only compare it against a minimum.
OLLAMA_API_VERSION = "0.6.0"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _generate_messages(payload: dict) -> list:
    messages = []
    if payload.get("system"):
        messages.append({"role": "system", "content": payload["system"]})
    messages.append({"role": "user", "content": payload.get("prompt", "")})
    return messages


class _OllamaCompatHandler(BaseHTTPRequestHandler):
    """Request handler. ``self.server`` carries the provider + token."""

    server: "_OllamaCompatHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("ollama_server: " + format, *args)

    def _check_auth(self) -> bool:
        if self.server.token is None:
            return True
        expected = f"Bearer {self.server.token}"
        actual = self.headers.get("Authorization", "")
        if not hmac.compare_digest(actual, expected):
            self._send_json(401, {"error": "unauthorized"})
            return False
        return True

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, text: str, head: bool = False) -> None:
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _model_entry(self) -> dict:
        name = self.server.provider.model
        return {
            "name": name,
            "model": name,
            "modified_at": _now(),
            "size": 0,
            "digest": "",
            "details": {"format": "", "family": "jarvis", "parameter_size": ""},
        }

    def do_HEAD(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send_text(200, "Ollama is running", head=True)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send_text(200, "Ollama is running")
            return
        if not self._check_auth():
            return
        if self.path == "/api/version":
            self._send_json(200, {"version": OLLAMA_API_VERSION})
        elif self.path == "/api/tags":
            self._send_json(200, {"models": [self._model_entry()]})
        elif self.path == "/api/ps":
            self._send_json(200, {"models": [self._model_entry()]})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        routes = {
            "/api/chat": self._handle_chat,
            "/api/generate": self._handle_generate,
            "/api/show": self._handle_show,
        }
        handler = routes.get(self.path)
        if handler is None:
            self._send_json(404, {"error": "not found"})
            return
        if not self._check_auth():
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON body"})
            return
        handler(payload)

    def _handle_show(self, payload: dict) -> None:
        self._send_json(
            200,
            {
                "modelfile": "",
                "parameters": "",
                "template": "",
                "details": self._model_entry()["details"],
                "model_info": {},
                "capabilities": ["completion"],
            },
        )

    def _handle_chat(self, payload: dict) -> None:
        messages = payload.get("messages", [])
        self._respond(
            payload,
            messages,
            lambda text, done: {"message": {"role": "assistant", "content": text}},
        )

    def _handle_generate(self, payload: dict) -> None:
        self._respond(
            payload,
            _generate_messages(payload),
            lambda text, done: {"response": text},
        )

    def _respond(self, payload: dict, messages: list, shape: Any) -> None:
        """Answer one chat/generate request, streamed (the default) or not."""
        provider = self.server.provider
        model = payload.get("model") or provider.model
        started = time.monotonic()

        def envelope(text: str, done: bool) -> dict:
            out = {
                "model": model,
                "created_at": _now(),
                **shape(text, done),
                "done": done,
            }
            if done:
                out.update(
                    done_reason="stop",
                    total_duration=int((time.monotonic() - started) * 1e9),
                    prompt_eval_count=provider.last_prompt_tokens,
                    eval_count=provider.last_completion_tokens,
                )
            return out

        if payload.get("stream", True):
            self._stream(provider.stream_chat(messages), envelope)
            return

        try:
            text = provider.chat(messages)
        except Exception as e:
            logger.error(f"ollama_server: chat error: {e}")
            self._send_json(500, {"error": str(e)})
            return
        self._send_json(200, envelope(text, True))

    def _stream(self, chunks: Iterator[str], envelope: Any) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()

        def emit(obj: dict) -> None:
            self.wfile.write(json.dumps(obj).encode() + b"\n")
            self.wfile.flush()

        try:
            for chunk in chunks:
                emit(envelope(chunk, False))
        except Exception as e:
            logger.error(f"ollama_server: stream error: {e}")
            emit({"error": str(e)})
            return
        emit(envelope("", True))


class _OllamaCompatHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple, provider: BaseLLMProvider, token: Optional[str]):
        super().__init__(address, _OllamaCompatHandler)
        self.provider = provider
        self.token = token


class OllamaCompatServer:
    """Manages the opt-in Ollama-compatible endpoint's lifecycle."""

    def __init__(self, provider: BaseLLMProvider):
        self.provider = provider
        self._httpd: Optional[_OllamaCompatHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        """Start the server if enabled and the bind target is safe.

        No-ops silently if ``OLLAMA_SERVER_ENABLED`` is false (the default).
        """
        if not Config.OLLAMA_SERVER_ENABLED:
            return
        if self._httpd is not None:
            return

        host = Config.OLLAMA_SERVER_HOST
        loopback = host in _LOOPBACK_HOSTS
        if not loopback and not Config.OLLAMA_SERVER_ALLOW_NONLOCAL:
            logger.error(
                f"ollama_server: JARVIS_OLLAMA_SERVER_HOST={host!r} is not "
                "loopback and JARVIS_OLLAMA_SERVER_ALLOW_NONLOCAL is not set "
                "— refusing to start. See docs/SECURITY-ARCHITECTURE.md "
                "before overriding it."
            )
            return

        token = None if loopback else _load_or_create_token()
        address = (host, Config.OLLAMA_SERVER_PORT)

        try:
            self._httpd = _OllamaCompatHTTPServer(address, self.provider, token)
        except OSError as e:
            logger.error(
                f"ollama_server: failed to bind {host}:{Config.OLLAMA_SERVER_PORT}: {e}"
            )
            return

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
            name="jarvis-ollama-server",
        )
        self._thread.start()

        bound_host, bound_port = self._httpd.server_address[:2]
        auth = "bearer token required" if token else "no token (loopback)"
        logger.warning(
            f"ollama_server: ENABLED on http://{bound_host}:{bound_port} — {auth}. "
            "This is a deliberate exception to JARVIS's normal no-network-listener "
            "design — see docs/SECURITY-ARCHITECTURE.md."
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
