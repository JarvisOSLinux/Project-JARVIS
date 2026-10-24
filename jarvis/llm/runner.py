"""Supervises the ``llama-server`` processes that serve JARVIS's local models.

One process per model: llama-server loads a single model, and the chat model
and the embedding model need different flags (``--embedding``). A spec that
names an existing file is passed as ``-m``; anything else is a Hugging Face
reference (``user/repo[:quant]``) that llama-server downloads into
``MODELS_DIR/llm`` on first start.
"""

import atexit
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from typing import Dict, List, Optional

from ..config import Config
from ..core.logger import JarvisLogger, get_logger

logger = get_logger(__name__)

POLL_INTERVAL = 1.0


class RunnerError(RuntimeError):
    """llama-server is missing or did not come up."""


def find_binary() -> Optional[str]:
    return shutil.which(Config.LLAMA_SERVER_BIN)


class LlamaServer:
    """One managed llama-server process serving one model."""

    def __init__(self, model: str, port: int, embedding: bool = False):
        self.model = model
        self.port = port
        self.embedding = embedding
        self._proc: Optional[subprocess.Popen] = None
        self._stderr = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://{Config.LLM_HOST}:{self.port}"

    def is_up(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=2) as response:
                return response.status == 200
        except Exception:
            return False

    def _command(self, binary: str) -> List[str]:
        cmd = [
            binary,
            "--host",
            Config.LLM_HOST,
            "--port",
            str(self.port),
            "--alias",
            self.model,
            "-ngl",
            str(Config.LLM_GPU_LAYERS),
        ]
        if os.path.isfile(self.model):
            cmd += ["-m", self.model]
        else:
            cmd += ["-hf", self.model]
        if self.embedding:
            cmd.append("--embedding")
        else:
            cmd.append("--jinja")
            if Config.LLM_CONTEXT_WINDOW > 0:
                cmd += ["-c", str(Config.LLM_CONTEXT_WINDOW)]
        return cmd

    def ensure_running(self) -> None:
        """Start the server if nothing answers on its port, and wait for it.

        Raises RunnerError when the binary is missing, the process exits, or
        the model is not ready within ``LLM_START_TIMEOUT`` (a first start may
        be downloading several GB).
        """
        with self._lock:
            if self.is_up():
                return
            if self._proc is None or self._proc.poll() is not None:
                self._spawn()
            self._wait_ready()

    def _spawn(self) -> None:
        binary = find_binary()
        if binary is None:
            raise RunnerError(
                f"'{Config.LLAMA_SERVER_BIN}' not found on PATH; install llama.cpp "
                "(Arch: pacman -S llama.cpp) or set LLAMA_SERVER_BIN"
            )
        cache = os.path.join(Config.MODELS_DIR, "llm")
        os.makedirs(cache, exist_ok=True)
        env = dict(os.environ, LLAMA_CACHE=cache)
        self._stderr = JarvisLogger.child_stderr("llama-server")
        cmd = self._command(binary)
        logger.info(f"Starting llama-server for '{self.model}' on port {self.port}")
        logger.debug(f"llama-server command: {cmd}")
        try:
            self._proc = subprocess.Popen(
                cmd,
                env=env,
                stdout=self._stderr,
                stderr=self._stderr,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise RunnerError(f"could not start llama-server: {exc}") from exc

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + Config.LLM_START_TIMEOUT
        while time.monotonic() < deadline:
            if self.is_up():
                logger.info(f"llama-server ready for '{self.model}'")
                return
            code = self._proc.poll() if self._proc else None
            if code is not None:
                raise RunnerError(
                    f"llama-server exited with code {code} while loading "
                    f"'{self.model}'; see llama-server.log"
                )
            time.sleep(POLL_INTERVAL)
        raise RunnerError(
            f"llama-server not ready after {Config.LLM_START_TIMEOUT:.0f}s "
            f"loading '{self.model}'"
        )

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


_servers: Dict[int, LlamaServer] = {}
_servers_lock = threading.Lock()


def get_server(model: str, port: int, embedding: bool = False) -> LlamaServer:
    """Return the server owning ``port`` for ``model``, creating it lazily.

    A port serves one model: asking for a different model on a port that is
    taken stops the old server first, so failing over between local providers
    swaps the model instead of colliding on the port.
    """
    with _servers_lock:
        current = _servers.get(port)
        if current is not None and current.model == model:
            return current
        replaced = current
        _servers[port] = server = LlamaServer(model, port, embedding)
    if replaced is not None:
        replaced.stop()
    return server


def stop_all() -> None:
    with _servers_lock:
        servers = list(_servers.values())
    for server in servers:
        server.stop()


atexit.register(stop_all)
