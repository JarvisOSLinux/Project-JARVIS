"""Host-side threat classification for the TLA (Threat Level Access) gate.

The confirmation gate must not let a dangerous tool escape approval simply by
omitting ``confirmation_required`` from its manifest. The *host* assigns a
minimum threat level to a tool based on what it can do — e.g. arbitrary command
execution can escalate via ``sudo`` — and the gate confirms at or above a
threshold. A manifest may RAISE a tool's level (declare it more dangerous, or
opt in via ``confirmation_required``) but can never lower it below the host
floor.

Classification is per TOOL, not per server: a server's dangerous tool
(``run_command``) is gated while its safe siblings (``web_search``) are not.

Beyond tool identity, the *parameters* are scanned for dangerous payloads
(``rm -rf``, ``dd if=``, ``| sh`` …): a host-safe tool handed a destructive
argument is raised too. This is raise-only and complements the identity floor;
it never lowers a level (Project-JARVIS #162).

The four tiers mirror the kernel policy engine's vocabulary so the userspace
gate and the OS embodiment speak the same language.
"""

import re
from enum import IntEnum
from typing import Any, Dict, Optional


class ThreatLevel(IntEnum):
    SAFE = 0
    ELEVATED = 1
    DANGEROUS = 2
    FORBIDDEN = 3


# Bare tool names the host always treats as at least DANGEROUS: arbitrary
# command / script execution, which can escalate (sudo) or mutate the system.
# Author-proof — a manifest cannot lower a tool below this floor.
HOST_DANGEROUS_TOOLS = frozenset(
    {
        "run_command",
        "execute_command",
        "run_script",
        "execute_script",
        "exec",
        "shell",
        "bash",
        "sh",
        "spawn",
    }
)

_MANIFEST_LEVELS = {
    "safe": ThreatLevel.SAFE,
    "elevated": ThreatLevel.ELEVATED,
    "dangerous": ThreatLevel.DANGEROUS,
    "forbidden": ThreatLevel.FORBIDDEN,
}


def _host_floor(tool_name: Optional[str]) -> ThreatLevel:
    if not tool_name:
        return ThreatLevel.SAFE
    bare = tool_name.split(".")[-1].strip().lower()
    return ThreatLevel.DANGEROUS if bare in HOST_DANGEROUS_TOOLS else ThreatLevel.SAFE


def _declared(tool_metadata: Dict[str, Any]) -> ThreatLevel:
    raw = tool_metadata.get("threat_level")
    if isinstance(raw, str) and raw.strip().lower() in _MANIFEST_LEVELS:
        return _MANIFEST_LEVELS[raw.strip().lower()]
    # Legacy opt-in: `confirmation_required` means "at least ELEVATED".
    if tool_metadata.get("confirmation_required"):
        return ThreatLevel.ELEVATED
    return ThreatLevel.SAFE


# Substrings in tool *parameters* that mark a payload as dangerous regardless
# of which tool carries it: a host-"safe" tool (an HTTP fetch, a file writer)
# handed one of these is doing something destructive or escalating. Deliberately
# narrow — only signatures that essentially never occur in benign input — so a
# false positive (which costs only an extra confirmation) stays rare.
_DANGEROUS_PAYLOAD_PATTERNS = (
    re.compile(r"\bsudo\s+\S", re.IGNORECASE),  # privilege escalation
    re.compile(r"\brm\s+-\w*[rf]", re.IGNORECASE),  # rm -rf / -r / -f
    re.compile(r"\bdd\s+if=", re.IGNORECASE),  # raw disk copy
    re.compile(r"\bmkfs\b|\bmkswap\b", re.IGNORECASE),  # format a filesystem
    re.compile(
        r">\s*/dev/(?:sd|nvme|hd|vd|mmcblk)", re.IGNORECASE
    ),  # write block device
    re.compile(r"\|\s*(?:sh|bash|zsh|dash)\b", re.IGNORECASE),  # pipe into a shell
    re.compile(r":\(\)\s*\{.*\|.*&", re.DOTALL),  # fork bomb :(){ :|:& };:
    re.compile(
        r"\bchmod\s+-\w*R\w*\s+0*777\b", re.IGNORECASE
    ),  # recursive world-writable
)


def _iter_strings(value: Any):
    """Yield every string reachable in a params structure (dict/list/scalar)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _payload_floor(params: Any) -> ThreatLevel:
    if not params:
        return ThreatLevel.SAFE
    for text in _iter_strings(params):
        if any(pattern.search(text) for pattern in _DANGEROUS_PAYLOAD_PATTERNS):
            return ThreatLevel.DANGEROUS
    return ThreatLevel.SAFE


def classify(
    tool_name: Optional[str],
    tool_metadata: Optional[Dict[str, Any]] = None,
    params: Any = None,
) -> ThreatLevel:
    """Effective threat level = ``max(host floor, manifest, payload)``.

    The manifest may raise a tool's level but can never lower it below the host
    floor, and a dangerous *payload* raises the level even for a host-safe tool
    — so neither a permissive manifest nor a benign tool identity can hide a
    destructive parameter.
    """
    metadata = tool_metadata or {}
    return ThreatLevel(
        max(
            int(_host_floor(tool_name)),
            int(_declared(metadata)),
            int(_payload_floor(params)),
        )
    )
