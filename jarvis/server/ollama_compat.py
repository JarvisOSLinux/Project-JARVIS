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
4. **Plain model names are inference only.** They proxy to a
   ``BaseLLMProvider`` and skip ROOT/DISPATCH, MCP tools, and the TLA
   confirmation gate.

Harness routing
---------------
The model name ``JARVIS_OLLAMA_SERVER_HARNESS_MODEL`` (default ``jarvis``)
runs the request through the full harness instead: the prompt is injected
into the daemon's event loop like any other input, so ROOT, DISPATCH, MCP
tools and the confirmation gate all apply. Because that can run tools, it
**always requires the bearer token, loopback or not**. Ollama clients that
cannot send a header cannot reach it.

A request is one harness turn: ROOT's progress notes stream as they arrive
and the turn ends with ROOT's reply. Turns run one at a time, only the last
user message is used (the harness keeps its own session), and output from
other inputs (voice, TUI) during a turn can appear in it. A turn that needs
a tool confirmation waits until it is answered elsewhere or times out.
"""

from __future__ import annotations

import hmac
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator, Optional

from ..config import Config
from ..core.logger import get_logger
from ..core.secret_scrubber import redact_secrets, redaction_notice
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


def _last_user_text(messages: list) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content", ""))
    return ""


class HarnessBridge:
    """Runs one prompt through the daemon's full ROOT/DISPATCH harness.

    Rides the same output callback the sockets use. It registers once and
    forwards to whichever turn is in flight, since the callback list is read
    from the event-loop thread while HTTP threads would be changing it.
    """

    def __init__(self, app: Any, timeout: float):
        self._app = app
        self._timeout = timeout
        self._lock = threading.Lock()
        self._queue: Optional["queue.Queue[dict]"] = None
        app.output_manager.add_output_callback(self._on_output)

    def close(self) -> None:
        self._app.output_manager.remove_output_callback(self._on_output)

    def _on_output(self, response: dict) -> None:
        q = self._queue
        if q is not None:
            q.put(response)

    def run(self, prompt: str) -> Iterator[str]:
        """Yield ROOT's progress notes, then its reply, for one turn."""
        app = self._app
        if prompt.lstrip().startswith("/"):
            raise ValueError("slash commands are not accepted over HTTP")
        if not (app._running and app.events._running):
            raise RuntimeError("JARVIS event loop is not running")

        clean, redacted = redact_secrets(prompt)
        with self._lock:
            q: "queue.Queue[dict]" = queue.Queue()
            self._queue = q
            try:
                if redacted:
                    yield redaction_notice(redacted) + "\n\n"
                app.events.inject_user_input(clean)
                deadline = time.monotonic() + self._timeout
                while True:
                    try:
                        response = q.get(timeout=max(deadline - time.monotonic(), 0))
                    except queue.Empty:
                        raise TimeoutError(
                            f"no reply from JARVIS within {self._timeout:g}s "
                            "(a tool may be waiting for confirmation)"
                        ) from None
                    text = str(response.get("output", ""))
                    if response.get("comment"):
                        yield text + "\n\n"
                        continue
                    yield text
                    return
            finally:
                self._queue = None


class _OllamaCompatHandler(BaseHTTPRequestHandler):
    """Request handler. ``self.server`` carries the provider + token."""

    server: "_OllamaCompatHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("ollama_server: " + format, *args)

    def _check_auth(self, force: bool = False) -> bool:
        if not (force or self.server.auth_required):
            return True
        expected = f"Bearer {self.server.token}"
        actual = self.headers.get("Authorization", "")
        if not hmac.compare_digest(actual, expected):
            self._send_json(
                401,
                {"error": "unauthorized: send the JARVIS bearer token"},
            )
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

    def _is_harness(self, model: Optional[str]) -> bool:
        name = self.server.harness_model
        return self.server.harness is not None and model in (name, f"{name}:latest")

    def _model_entries(self) -> list:
        names = [self.server.provider.model]
        if self.server.harness is not None:
            names.insert(0, self.server.harness_model)
        return [self._model_entry(n) for n in names]

    def _model_entry(self, name: str) -> dict:
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
            self._send_json(200, {"models": self._model_entries()})
        elif self.path == "/api/ps":
            self._send_json(200, {"models": self._model_entries()})
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
                "details": self._model_entry(payload.get("model", ""))["details"],
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
        harness = self._is_harness(payload.get("model"))
        if harness and not self._check_auth(force=True):
            return
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

        chunks: Optional[Iterator[str]] = None
        if harness:
            chunks = self.server.harness.run(_last_user_text(messages))

        if payload.get("stream", True):
            self._stream(chunks or provider.stream_chat(messages), envelope)
            return

        try:
            text = "".join(chunks) if chunks is not None else provider.chat(messages)
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
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()
        emit(envelope("", True))


class _OllamaCompatHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple,
        provider: BaseLLMProvider,
        token: str,
        auth_required: bool,
        harness: Optional[HarnessBridge],
        harness_model: str,
    ):
        super().__init__(address, _OllamaCompatHandler)
        self.provider = provider
        self.token = token
        self.auth_required = auth_required
        self.harness = harness
        self.harness_model = harness_model


class OllamaCompatServer:
    """Manages the opt-in Ollama-compatible endpoint's lifecycle."""

    def __init__(self, provider: BaseLLMProvider, app: Any = None):
        """``app`` enables harness routing; without it only plain models serve."""
        self.provider = provider
        self._app = app
        self._harness: Optional[HarnessBridge] = None
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

        token = _load_or_create_token()
        address = (host, Config.OLLAMA_SERVER_PORT)

        if self._app is not None:
            self._harness = HarnessBridge(
                self._app, Config.OLLAMA_SERVER_HARNESS_TIMEOUT
            )
        try:
            self._httpd = _OllamaCompatHTTPServer(
                address,
                self.provider,
                token,
                auth_required=not loopback,
                harness=self._harness,
                harness_model=Config.OLLAMA_SERVER_HARNESS_MODEL,
            )
        except OSError as e:
            logger.error(
                f"ollama_server: failed to bind {host}:{Config.OLLAMA_SERVER_PORT}: {e}"
            )
            self._close_harness()
            return

        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
            name="jarvis-ollama-server",
        )
        self._thread.start()

        bound_host, bound_port = self._httpd.server_address[:2]
        auth = "bearer token required" if not loopback else "no token (loopback)"
        if self._harness is not None:
            auth += (
                f"; model {Config.OLLAMA_SERVER_HARNESS_MODEL!r} runs the full "
                f"harness and always needs the token ({Config.OPENAI_SERVER_TOKEN_FILE})"
            )
        logger.warning(
            f"ollama_server: ENABLED on http://{bound_host}:{bound_port} — {auth}. "
            "This is a deliberate exception to JARVIS's normal no-network-listener "
            "design — see docs/SECURITY-ARCHITECTURE.md."
        )

    def _close_harness(self) -> None:
        if self._harness is not None:
            self._harness.close()
            self._harness = None

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._close_harness()
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
