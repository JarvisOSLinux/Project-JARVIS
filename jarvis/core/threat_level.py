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
across both Unix and Windows shells (``rm -rf``, ``dd if=``, ``| sh``,
``Remove-Item -Recurse``, ``vssadmin delete shadows``, ``| iex`` …): a
host-safe tool handed a destructive argument is raised too. This is raise-only
and complements the identity floor; it never lowers a level (Project-JARVIS
#162).

The fourth input is the *registry tier* (#223): the dispatch path stamps the
server's install-time trust tier (``registry_tier``) and whether the local
manifest declares this tool (``registry_declared``) into the metadata, and a
tool floors at ELEVATED unless its declaration passed the registry's gate —
tier ``official``/``community`` AND manifest-declared. A server nobody
reviewed (URL install, unreadable manifest, legacy tier vocabulary) cannot
lower itself below ELEVATED by self-declaring ``safe``. Metadata WITHOUT the
``registry_tier`` key is out-of-dispatch context and gets no tier floor, so
bare-metadata callers keep their contract. Raise-only like every other input.

The fifth input is the *privileged-command prefix table* (#208): each
``BasePlatform`` subclass carries a per-OS list of command prefixes that
imply elevation (``pacman``, ``systemctl stop``, ``reg delete``, …), built
for the retired ``shellmcp`` askpass path and otherwise orphaned once dmcp's
scope model took over deciding elevation. Its signal outlives that path: a
tool with a command-ish parameter but no shell-ish *name* evades the host
floor, and a privileged subsystem verb like ``pacman -Syu`` or ``systemctl
stop firewalld`` trips none of the (deliberately narrow) payload regexes
either. Raise-only, folded in at the same DANGEROUS severity as the payload
scan's own escalation signatures (``sudo``, ``rm -rf``, …).

The four tiers mirror the kernel policy engine's vocabulary so the userspace
gate and the OS embodiment speak the same language.
"""

import re
from enum import IntEnum
from functools import lru_cache
from typing import Any, Dict, Optional

import jarvis.platform as _platform_mod


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
        # The PTY job model is that same execution, addressable: run_job IS
        # execute_command on a terminal that outlives the call, and send_input
        # writes arbitrary bytes into that live terminal — a shell one keystroke
        # away. Gating only the blocking spelling would leave the interactive
        # one, which is the one to prefer for anything that can pause for input,
        # running unconfirmed (as root, on the system-scope shell server).
        "run_job",
        "send_input",
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


# Tiers whose per-tool declarations passed the registry's PR gate (validator
# requires a threat_level on every tool of a live entry; promotions and
# revocation-lifts need the maintainer label). Everything else — "unknown",
# legacy "vetted"/"unreviewed", a revoked tier, a missing value — never had a
# reviewed declaration, so nothing it says can lift the floor.
_GATED_TIERS = frozenset({"official", "community"})


def _tier_floor(tool_metadata: Dict[str, Any]) -> ThreatLevel:
    if "registry_tier" not in tool_metadata:
        # No tier context (bare-metadata callers, non-dispatch paths): the
        # caution default lives in the dispatch plumbing that stamps the key,
        # not here — classify() with empty metadata must stay SAFE for benign
        # tools or every out-of-dispatch confirmation check would gate.
        return ThreatLevel.SAFE
    tier = str(tool_metadata.get("registry_tier") or "").strip().lower()
    if tier in _GATED_TIERS and tool_metadata.get("registry_declared"):
        return ThreatLevel.SAFE
    return ThreatLevel.ELEVATED


# Substrings in tool *parameters* that mark a payload as dangerous regardless
# of which tool carries it: a host-"safe" tool (an HTTP fetch, a file writer)
# handed one of these is doing something destructive or escalating. Deliberately
# narrow — only signatures that essentially never occur in benign input — so a
# false positive (which costs only an extra confirmation) stays rare. Covers
# both Unix/POSIX and Windows/PowerShell shells, since a host-safe tool runs
# on whatever OS the daemon is on.
_DANGEROUS_PAYLOAD_PATTERNS = (
    # --- Unix / POSIX shells ---
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
    # --- Windows / PowerShell shells --- (require the destructive flag/drive so
    # bare 'del'/'format'/'rd' in prose never trips)
    re.compile(
        r"\bRemove-Item\b.*?\s-(?:Recurse|Force|r)\b", re.IGNORECASE
    ),  # recursive/forced delete
    re.compile(r"\b(?:rd|rmdir)\b.*?\s/s\b", re.IGNORECASE),  # recursive dir delete
    re.compile(r"\bdel\b.*?\s/(?:s|q)\b", re.IGNORECASE),  # recursive/quiet delete
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),  # format a drive
    re.compile(r"\bdiskpart\b", re.IGNORECASE),  # partition editor
    re.compile(r"\breg\s+delete\b", re.IGNORECASE),  # registry key delete
    re.compile(
        r"\bvssadmin\s+delete\s+shadows\b", re.IGNORECASE
    ),  # wipe shadow copies (ransomware)
    re.compile(r"\bbcdedit\b", re.IGNORECASE),  # boot configuration edit
    re.compile(r"\bcipher\s+/w", re.IGNORECASE),  # wipe free space
    re.compile(
        r"\bSet-ExecutionPolicy\b.*?\b(?:Bypass|Unrestricted)\b", re.IGNORECASE
    ),  # disable script-signing enforcement
    re.compile(
        r"\|\s*(?:iex|powershell)\b|\bIEX\s*\(", re.IGNORECASE
    ),  # pipe into PowerShell / Invoke-Expression
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


@lru_cache(maxsize=None)
def _privileged_pattern(prefixes: tuple) -> "re.Pattern":
    alternation = "|".join(re.escape(prefix) for prefix in prefixes)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


def _privileged_floor(params: Any) -> ThreatLevel:
    if not params:
        return ThreatLevel.SAFE
    prefixes = tuple(_platform_mod.current.privileged_prefixes())
    if not prefixes:
        return ThreatLevel.SAFE
    pattern = _privileged_pattern(prefixes)
    for text in _iter_strings(params):
        if pattern.search(text):
            return ThreatLevel.DANGEROUS
    return ThreatLevel.SAFE


def classify(
    tool_name: Optional[str],
    tool_metadata: Optional[Dict[str, Any]] = None,
    params: Any = None,
) -> ThreatLevel:
    """Effective threat level = ``max(host floor, manifest, payload,
    privileged-command, tier)``.

    The manifest may raise a tool's level but can never lower it below the host
    floor, and a dangerous *payload* raises the level even for a host-safe tool
    — so neither a permissive manifest nor a benign tool identity can hide a
    destructive parameter. The privileged-command floor (#208) catches the gap
    between those two: a command-ish parameter naming an elevation-implying
    subsystem verb (``pacman``, ``systemctl stop``, ``reg delete``, …) that
    matches no payload signature and arrives under a tool name the host floor
    doesn't know. The registry-tier floor (#223) raises an unreviewed tool to
    ELEVATED: only a declaration that passed the registry's gate (tier
    ``official``/``community``) classifies below that. All five inputs raise
    only; none can lower another.
    """
    metadata = tool_metadata or {}
    return ThreatLevel(
        max(
            int(_host_floor(tool_name)),
            int(_declared(metadata)),
            int(_payload_floor(params)),
            int(_privileged_floor(params)),
            int(_tier_floor(metadata)),
        )
    )
