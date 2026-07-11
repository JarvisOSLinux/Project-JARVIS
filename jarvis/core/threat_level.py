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

The four tiers mirror the kernel policy engine's vocabulary so the userspace
gate and the OS embodiment speak the same language.
"""

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


def classify(
    tool_name: Optional[str],
    tool_metadata: Optional[Dict[str, Any]] = None,
) -> ThreatLevel:
    """Effective threat level = ``max(host floor, manifest declaration)``.

    The manifest may raise a tool's level but can never lower it below the host
    floor, so a dangerous tool cannot opt out of gating.
    """
    metadata = tool_metadata or {}
    return ThreatLevel(max(int(_host_floor(tool_name)), int(_declared(metadata))))
