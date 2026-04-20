"""
JARVIS Kernel Client — /dev/jarvis bridge

Connects the JARVIS AI daemon to the linux-jarvisos kernel driver.

Responsibilities
----------------
1. Open /dev/jarvis and report the daemon's state + active LLM provider/model
   to the kernel so sysfs is always accurate.

2. Run a background poll() loop: when the kernel enqueues a query (from a
   thermal event, OOM handler, audit subsystem, etc.) this thread reads it,
   dispatches it through the normal JARVIS inference pipeline, and posts the
   response back via JARVIS_IOC_RESPOND.

3. Expose jarvis_key_get(key_id) so LLM providers can retrieve API keys from
   the kernel keyring instead of from environment variables.

4. Expose jarvis_policy_check(server, tool) so the dispatch layer can gate
   actions through the kernel policy engine before execution.

Graceful degradation
--------------------
If the kernel module is not loaded (/dev/jarvis absent) the client silently
does nothing — JARVIS runs in "userspace-only" mode.  The kernel integration
is strictly additive.

Thread safety
-------------
The ioctl calls are protected by a threading.Lock().  The poll loop runs in
a daemon thread and is stopped by calling client.stop().

Usage
-----
    client = KernelClient(jarvis_instance)
    client.start()          # spawns background thread
    # ... daemon runs ...
    client.stop()

Key/provider reporting is called directly:
    client.report_provider(KernelClient.PROVIDER_OLLAMA, "llama3:8b")
    key = client.get_api_key("claude-api-key")   # may return None
"""

import ctypes
import fcntl
import os
import select
import struct
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .main import Jarvis

from .core.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# UAPI constants (must match linux/include/uapi/linux/jarvis.h)
# ---------------------------------------------------------------------------

JARVIS_MAX_QUERY_LEN = 4096
JARVIS_MAX_RESP_LEN = 65536
JARVIS_MODEL_NAME_LEN = 64
JARVIS_KEY_ID_LEN = 64
JARVIS_KEY_DATA_LEN = 512
JARVIS_POLICY_PATTERN_LEN = 128

# jarvis_state
STATE_OFFLINE = 0
STATE_IDLE = 1
STATE_PROCESSING = 2
STATE_ERROR = 3

# jarvis_provider
PROVIDER_NONE = 0
PROVIDER_OLLAMA = 1
PROVIDER_CLAUDE = 2
PROVIDER_OPENAI = 3
PROVIDER_OPENAI_COMPAT = 4

# jarvis_policy_tier
TIER_SAFE = 0
TIER_ELEVATED = 1
TIER_DANGEROUS = 2
TIER_FORBIDDEN = 3

# Query types
QTYPE_GENERIC = 0
QTYPE_SYSEVT = 1
QTYPE_AUDIT = 2
QTYPE_DIAG = 3
QTYPE_VOICE_CMD = 4
QTYPE_MCP_CALL = 5
QTYPE_POLICY_REQ = 6

# IOCTL numbers — computed from the kernel macro expansion:
#   _IOC(dir, type, nr, size)  type='J'=0x4A
#   _IOR  = dir=2, _IOW = dir=1, _IOWR = dir=3, _IO = dir=0
# Format: (direction<<30) | (size<<16) | (type<<8) | nr
_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2
_JARVIS_TYPE = ord("J")


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (_JARVIS_TYPE << 8) | nr


# struct sizes (little-endian, host byte order assumed)
# jarvis_status:  u32 state + u32 provider + u32 pending + u32 model_loaded
#                 + 64 model_name + 64 provider_name = 144 bytes
_STATUS_SIZE = 4 + 4 + 4 + 4 + JARVIS_MODEL_NAME_LEN + JARVIS_MODEL_NAME_LEN
# jarvis_query: u64 id + u32 type + u32 flags + u32 len + u32 pad
#               + u64 timestamp + JARVIS_MAX_QUERY_LEN data
_QUERY_SIZE = 8 + 4 + 4 + 4 + 4 + 8 + JARVIS_MAX_QUERY_LEN
# jarvis_response: u64 id + u32 status + u32 flags + u32 len + u32 pad
#                  + JARVIS_MAX_RESP_LEN data
_RESPONSE_SIZE = 8 + 4 + 4 + 4 + 4 + JARVIS_MAX_RESP_LEN
# jarvis_policy_check: 128 server + 128 tool + u32 tier + u32 allowed
_POLICY_CHECK_SIZE = JARVIS_POLICY_PATTERN_LEN * 2 + 4 + 4
# jarvis_key_op: 64 id + 512 data + u32 len + u32 pad
_KEY_OP_SIZE = JARVIS_KEY_ID_LEN + JARVIS_KEY_DATA_LEN + 4 + 4

IOCTL_STATUS = _ioc(_IOC_READ, 1, _STATUS_SIZE)
IOCTL_SET_STATE = _ioc(_IOC_WRITE, 2, 4)
IOCTL_SET_MODEL = _ioc(_IOC_WRITE, 3, JARVIS_MODEL_NAME_LEN)
IOCTL_RESPOND = _ioc(_IOC_WRITE, 4, _RESPONSE_SIZE)
IOCTL_FLUSH = _ioc(_IOC_NONE, 7, 0)
IOCTL_SET_PROVIDER = _ioc(_IOC_WRITE, 8, 4)
IOCTL_SYSMON = _ioc(_IOC_READ, 10, 116)  # sizeof(jarvis_sysmon)
IOCTL_POLICY_CHECK = _ioc(_IOC_READ | _IOC_WRITE, 22, _POLICY_CHECK_SIZE)
IOCTL_KEY_GET = _ioc(_IOC_READ | _IOC_WRITE, 31, _KEY_OP_SIZE)

JARVIS_DEV = "/dev/jarvis"

# ---------------------------------------------------------------------------
# ctypes struct layouts
# ---------------------------------------------------------------------------


class JarvisQuery(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint64),
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("len", ctypes.c_uint32),
        ("_pad", ctypes.c_uint32),
        ("timestamp", ctypes.c_uint64),
        ("data", ctypes.c_uint8 * JARVIS_MAX_QUERY_LEN),
    ]


class JarvisResponse(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint64),
        ("status", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("len", ctypes.c_uint32),
        ("_pad", ctypes.c_uint32),
        ("data", ctypes.c_uint8 * JARVIS_MAX_RESP_LEN),
    ]


class JarvisPolicyCheck(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("server", ctypes.c_uint8 * JARVIS_POLICY_PATTERN_LEN),
        ("tool", ctypes.c_uint8 * JARVIS_POLICY_PATTERN_LEN),
        ("tier", ctypes.c_uint32),
        ("allowed", ctypes.c_uint32),
    ]


class JarvisKeyOp(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint8 * JARVIS_KEY_ID_LEN),
        ("data", ctypes.c_uint8 * JARVIS_KEY_DATA_LEN),
        ("len", ctypes.c_uint32),
        ("_pad", ctypes.c_uint32),
    ]


# ---------------------------------------------------------------------------
# Kernel client
# ---------------------------------------------------------------------------


class KernelClient:
    """
    Interface to /dev/jarvis.  Thread-safe.  Starts a background poll thread.
    """

    # Re-export constants for callers
    PROVIDER_NONE = PROVIDER_NONE
    PROVIDER_OLLAMA = PROVIDER_OLLAMA
    PROVIDER_CLAUDE = PROVIDER_CLAUDE
    PROVIDER_OPENAI = PROVIDER_OPENAI
    PROVIDER_OPENAI_COMPAT = PROVIDER_OPENAI_COMPAT

    TIER_SAFE = TIER_SAFE
    TIER_ELEVATED = TIER_ELEVATED
    TIER_DANGEROUS = TIER_DANGEROUS
    TIER_FORBIDDEN = TIER_FORBIDDEN

    def __init__(self, jarvis: Optional["Jarvis"] = None, dev_path: str = JARVIS_DEV):
        self._dev_path = dev_path
        self._jarvis = jarvis  # may be set later via .set_jarvis()
        self._fd: Optional[int] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._available = False

    def set_jarvis(self, jarvis: "Jarvis") -> None:
        """Attach the Jarvis instance after construction (breaks circular dep)."""
        self._jarvis = jarvis

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """
        Open /dev/jarvis and start the poll thread.
        Returns True if the kernel module is present, False if gracefully skipped.
        """
        if not Path(self._dev_path).exists():
            logger.info(
                "kernel_client: %s not found — running without kernel integration",
                self._dev_path,
            )
            return False

        try:
            self._fd = os.open(self._dev_path, os.O_RDWR)
        except PermissionError:
            logger.warning(
                "kernel_client: permission denied on %s — "
                "run as root or add CAP_SYS_ADMIN",
                self._dev_path,
            )
            return False
        except OSError as exc:
            logger.warning("kernel_client: could not open %s: %s", self._dev_path, exc)
            return False

        self._available = True
        logger.info("kernel_client: connected to %s", self._dev_path)

        # Report initial state
        self._set_state(STATE_IDLE)

        # Start background poll loop
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="jarvis-kernel-poll",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Signal the poll thread to exit and close the device."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if self._fd is not None:
            try:
                self._set_state(STATE_OFFLINE)
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # State reporting (kernel → sysfs)
    # ------------------------------------------------------------------

    def report_provider(self, provider: int, model: str) -> None:
        """
        Tell the kernel which LLM provider and model are active.
        Reflected in /sys/class/misc/jarvis/ and JARVIS_IOC_STATUS.
        """
        if not self._available:
            return
        self._set_provider(provider)
        self._set_model(model)

    def _set_state(self, state: int) -> None:
        buf = struct.pack("I", state)
        self._ioctl(IOCTL_SET_STATE, buf)

    def _set_provider(self, provider: int) -> None:
        buf = struct.pack("I", provider)
        self._ioctl(IOCTL_SET_PROVIDER, buf)

    def _set_model(self, model: str) -> None:
        encoded = model.encode("utf-8")[: JARVIS_MODEL_NAME_LEN - 1]
        padded = encoded.ljust(JARVIS_MODEL_NAME_LEN, b"\x00")
        self._ioctl(IOCTL_SET_MODEL, padded)

    # ------------------------------------------------------------------
    # Key retrieval from kernel keyring
    # ------------------------------------------------------------------

    def get_api_key(self, key_id: str) -> Optional[str]:
        """
        Retrieve an API key stored in the _jarvis kernel keyring.

        Returns the key string on success, None if the key is not found or
        the kernel module is unavailable.  Falls back to the environment
        variable of the same name (uppercased, hyphens→underscores) if
        the kernel lookup fails, e.g. "claude-api-key" → CLAUDE_API_KEY.
        """
        if self._available:
            op = JarvisKeyOp()
            id_bytes = key_id.encode("utf-8")[: JARVIS_KEY_ID_LEN - 1]
            ctypes.memmove(op.id, id_bytes, len(id_bytes))

            try:
                self._ioctl(IOCTL_KEY_GET, op)
                if op.len > 0:
                    raw = bytes(op.data[: op.len]).rstrip(b"\x00")
                    if raw:
                        logger.debug(
                            "kernel_client: retrieved key '%s' from keyring", key_id
                        )
                        return raw.decode("utf-8", errors="replace")
            except OSError as exc:
                logger.debug(
                    "kernel_client: keyring lookup for '%s' failed: %s", key_id, exc
                )

        # Fallback: environment variable
        env_name = key_id.upper().replace("-", "_")
        val = os.environ.get(env_name)
        if val:
            logger.debug("kernel_client: using env fallback for key '%s'", key_id)
        return val

    # ------------------------------------------------------------------
    # Policy check
    # ------------------------------------------------------------------

    def policy_check(self, server: str, tool: str) -> tuple[int, bool]:
        """
        Check the kernel policy for a server:tool action.

        Returns (tier, allowed).  If the kernel module is unavailable,
        returns (TIER_ELEVATED, True) — permissive fallback.
        """
        if not self._available:
            return (TIER_ELEVATED, True)

        chk = JarvisPolicyCheck()
        srv_b = server.encode("utf-8")[: JARVIS_POLICY_PATTERN_LEN - 1]
        tol_b = tool.encode("utf-8")[: JARVIS_POLICY_PATTERN_LEN - 1]
        ctypes.memmove(chk.server, srv_b, len(srv_b))
        ctypes.memmove(chk.tool, tol_b, len(tol_b))

        try:
            self._ioctl(IOCTL_POLICY_CHECK, chk)
            return (int(chk.tier), bool(chk.allowed))
        except OSError as exc:
            logger.warning("kernel_client: policy_check failed: %s", exc)
            return (TIER_ELEVATED, True)

    # ------------------------------------------------------------------
    # Background poll loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """
        Continuously polls /dev/jarvis for kernel-originated queries and
        dispatches them through JARVIS inference.
        """
        logger.info("kernel_client: poll loop started")
        while not self._stop_event.is_set():
            try:
                readable, _, _ = select.select([self._fd], [], [], 1.0)
                if not readable:
                    continue
            except (OSError, ValueError):
                break

            query = JarvisQuery()
            try:
                n = os.read(self._fd, ctypes.sizeof(query))
                if len(n) < ctypes.sizeof(query):
                    continue
                ctypes.memmove(ctypes.addressof(query), n, len(n))
            except OSError as exc:
                logger.error("kernel_client: read error: %s", exc)
                break

            self._handle_query(query)

        logger.info("kernel_client: poll loop exited")

    def _handle_query(self, query: JarvisQuery) -> None:
        """Process one kernel query through JARVIS and post the response."""
        payload = (
            bytes(query.data[: query.len])
            .decode("utf-8", errors="replace")
            .rstrip("\x00")
        )
        q_type = query.type

        logger.info(
            "kernel_client: query #%d type=%d: %s", query.id, q_type, payload[:120]
        )

        if not self._jarvis:
            self._send_response(query.id, 1, "JARVIS not initialised")
            return

        # Build a prompt that gives the LLM context about the query origin
        type_labels = {
            QTYPE_GENERIC: "General query",
            QTYPE_SYSEVT: "Kernel system event",
            QTYPE_AUDIT: "Security audit event",
            QTYPE_DIAG: "Hardware diagnostic",
            QTYPE_VOICE_CMD: "Voice command",
            QTYPE_MCP_CALL: "MCP tool request",
            QTYPE_POLICY_REQ: "Policy authorisation request",
        }
        label = type_labels.get(q_type, f"Kernel query type {q_type}")
        prompt = f"[{label}]\n{payload}"

        try:
            self._set_state(STATE_PROCESSING)
            response = self._jarvis.ask(prompt=prompt)
            answer = (
                response.get("output", "")
                if isinstance(response, dict)
                else str(response)
            )
            self._send_response(query.id, 0, answer)
        except Exception as exc:
            logger.exception("kernel_client: inference error for query #%d", query.id)
            self._send_response(query.id, 1, f"inference error: {exc}")
        finally:
            self._set_state(STATE_IDLE)

    def _send_response(self, query_id: int, status: int, text: str) -> None:
        resp = JarvisResponse()
        resp.id = query_id
        resp.status = status
        payload = text.encode("utf-8")[:JARVIS_MAX_RESP_LEN]
        resp.len = len(payload)
        ctypes.memmove(resp.data, payload, len(payload))

        try:
            self._ioctl(IOCTL_RESPOND, resp)
        except OSError as exc:
            logger.warning(
                "kernel_client: failed to post response for #%d: %s", query_id, exc
            )

    # ------------------------------------------------------------------
    # Low-level ioctl helper
    # ------------------------------------------------------------------

    def _ioctl(self, request: int, buf) -> int:
        """
        Issue an ioctl.  buf may be bytes, bytearray, or a ctypes Structure.
        For Structures the in-place result is reflected back into buf.
        """
        if self._fd is None:
            raise OSError("device not open")

        with self._lock:
            if isinstance(buf, (bytes, bytearray)):
                mutable = bytearray(buf)
                ret = fcntl.ioctl(self._fd, request, mutable)
                return ret
            else:
                # ctypes Structure — pass by reference so the kernel can write back
                ret = fcntl.ioctl(self._fd, request, buf)
                return ret


# ---------------------------------------------------------------------------
# LLM provider → kernel provider constant mapping
# ---------------------------------------------------------------------------


def provider_from_config(provider_name: str) -> int:
    """
    Convert a JARVIS Config.LLM_PROVIDER string to a JARVIS_PROVIDER_* int.
    """
    mapping = {
        "ollama": PROVIDER_OLLAMA,
        "claude": PROVIDER_CLAUDE,
        "openai": PROVIDER_OPENAI,
        "api": PROVIDER_OPENAI_COMPAT,
    }
    return mapping.get(provider_name.lower(), PROVIDER_NONE)
