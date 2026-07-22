# JARVIS Security Architecture

## Overview

Project JARVIS is a research platform studying the security implications
of AI agents with system-level access.  This document describes the
threat model, the attack surfaces inherited from the broader AI-agent
ecosystem, and how JARVIS's architecture addresses them — with explicit
reference to the vulnerabilities that emerged with OpenClaw, the first
AI agent to trigger a major public security incident (early 2026).

---

## Seven-Threat Taxonomy — Implementation Status

**This table is the canonical status of each research threat's mitigation.**
Where the website or the paper states a mitigation in the present tense, it must
match the status here. `implemented` = enforced in code; `partial` = present but
with a stated gap; `proposed` = designed, not built; `OS-side` = owned by the
OS embodiment, not the core. (Bloated Context and Forgetful Context were split
into distinct threats in 2026-07: Bloated = constraints crowded out of a full
window; Forgetful = constraints never durably stored in the first place.)

| # | Threat | Enforcement point | Status |
|---|--------|-------------------|--------|
| 1 | Malicious MCP Servers | registry vetting + `dmcp` manifest-hash verify + agent source-confinement | **implemented** (official tier not yet populated) |
| 2 | Prompt Injection | dispatch 128-bit boundary nonce, verified by the daemon (`jarvis/dispatch/boundary.py`, #165) | **implemented** — the daemon verifies the tag on every EXIT/TIMEOUT and marks failures UNVERIFIED in-band; system prompts instruct the LLM to treat boundary content as data-only |
| 3 | Misleading MCP Server Usage | official-tier review of tool descriptions + structured schema | **partial** |
| 4 | Unauthorized Sudo via MCP | userspace Threat-Level-Access confirmation gate with host-floor classification (`jarvis/core/threat_level.py`) | **implemented** — command-execution tools and dangerous payloads are force-confirmed regardless of manifest flags (#159/#162 closed) |
| 5 | Sudo Capability Exploitation | same confirmation gate | **implemented** |
| 6 | Bloated Context | daemon two-tier context + dispatch rolling window + contextor pruning | **partial** |
| 7 | Forgetful Context (novel) | — | **not yet mitigated** — no persistent constraint register in the daemon; highest-priority open item |
| — | Kernel 4-tier policy engine (`/dev/jarvis`) | linux-jarvisos + daemon `KernelClient` | **OS-side** — not consulted from the daemon today |

### On the "TLA" acronym (important for the paper)

**TLA = Threat Level Access.** It is a **userspace**, non-blocking,
human-in-the-loop confirmation gate on the dispatch path
(`docs/tla-confirmation-design.md`, `jarvis/core/confirmation_manager.py`,
`jarvis/runtime/dispatch_flow.py`) — the LLM is deliberately kept out of the
confirmation loop so it cannot misrepresent an action. Every privileged tool
call is evaluated against a host-assigned threat level and escalation requires
explicit out-of-band user approval.

Enforcement is **in userspace** (the JARVIS daemon), so do **not** describe the
current core behavior as "OS-enforced." The kernel `/dev/jarvis` policy engine
is part of the OS embodiment and is **not** consulted from the daemon today —
`KernelClient.policy_check` and `get_api_key` have no callers in the execution
path. (Earlier drafts expanded TLA as "Tool-Level Action"; "Threat Level
Access" is now the canonical expansion.)

---

## Threat Model

### Assets to Protect

| Asset | Impact if compromised |
|---|---|
| User shell / file system | Arbitrary code execution, data theft |
| `.env` / API keys | Cloud service compromise, cost attacks |
| Conversation history | Privacy violation, exfiltration |
| Contextor memory store | Memory poisoning, RAG manipulation |
| MCP server processes | Lateral movement, capability escalation |
| Unix input socket | Unauthorised command injection |
| Unix GUI socket (`jarvis.sock`) | Unauthorised command injection + TLA confirmation control (approve/deny) |

### Attacker Profiles

1. **Remote attacker** — reaches JARVIS over a network connection.
2. **Local attacker (different user)** — runs code as a different OS user on
   the same host.
3. **Same-user attacker** — runs code as the same OS user (e.g. a malicious
   npm/pip package in the user’s environment).
4. **Prompt injector** — embeds adversarial instructions in content JARVIS
   reads: web pages, files, emails, MCP tool output.

---

## OpenClaw CVE Comparison

OpenClaw exposed the first real-world AI-agent attack surface at scale.
The table below maps their critical CVEs to JARVIS design decisions.

### CVE-2026-25253 — RCE via Auth Token Exfiltration (1-click)

**OpenClaw:** `applySettingsFromUrl()` accepted an attacker-controlled
`gatewayUrl` query parameter and automatically opened a WebSocket to it,
transmitting the user’s authentication token.  Attacker captured the
token, reconnected to the legitimate gateway, and achieved full RCE.

**JARVIS:** Has no WebSocket gateway, no auth token, and no TCP port
listener.  The entire attack surface for this CVE does not exist.

- Status: ✅ Eliminated by design (no network gateway)

### CVE-2026-28472 “ClawJacked” — WebSocket Auth Bypass

**OpenClaw:** Device identity verification in the WebSocket handshake
could be bypassed by manipulating headers, allowing unauthenticated
remote devices to impersonate trusted paired devices.

**JARVIS:** No WebSocket gateway, no device pairing.  Eliminated by
the same design decision.

- Status: ✅ Eliminated by design (no network gateway)

### Exposed Instances (~40K discoverable on Shodan)

**OpenClaw:** Default configuration listened on TCP port 18789 with no
authentication.

**JARVIS:** Uses Unix domain sockets under `$JARVIS_DATA_DIR` (Linux default
`~/.local/share/jarvis/`): `input.sock`, `output.sock`, and the bidirectional
GUI socket `jarvis.sock`.  These are file-system objects — not TCP ports
— and are not reachable from the network.  `jarvis/core/socket_security.py`
hardens their permissions to `0600` (owner-only) at creation time.

- Status: ✅ Not network-exposed · ⚠️ Same-user local processes can still
  reach the socket (see § Remaining Attack Surfaces below)

### Malicious Skill Marketplace

**OpenClaw:** ClawHub allowed open submission of skills.  335 malicious
skills were published (≈12% of the registry), including keyloggers and
crypto-wallet stealers disguised as utilities.

**JARVIS:** `mcp-registry` is a curated, pull-request-gated JSON registry.
MCP servers are declared as human-readable manifests reviewed before
inclusion.  There is no anonymous upload endpoint.  The model is
deliberately inspired by Arch Linux’s AUR: open contribution, community
review, and maintainer oversight.

- Status: ✅ Curated, PR-gated registry · ✅ `dmcp install` now verifies
  `integrity.manifestSha256` (raw bytes, before parse/merge) and the agent is
  source-confined to configured registries · ⚠️ cryptographic (keyed)
  signatures still planned.  See `mcp-registry/docs/TRUST-MODEL.md`.

### API Key / Chat History Exposure

**OpenClaw:** Exposed instances leaked Anthropic API keys, Telegram and
Slack tokens, and months of complete chat histories.

**JARVIS:**
- Default provider is local Ollama — no API key required.
- `.env` is in `.gitignore` and never emitted to logs.
- Chat history and sessions live in the contextor SQLite store
  (`$JARVIS_DATA_DIR/memory/`, Linux default `~/.local/share/jarvis/memory/`;
  local, not served).
- Output socket broadcasts only to processes that explicitly connect.

- Status: ✅ No default cloud exposure · ⚠️ Users adding API providers
  must protect their `providers.json` (API keys stored there)

### Root / Privileged Execution

**OpenClaw:** Users commonly ran as root or with administrator privileges.

**JARVIS:** `JARVIS_SUDO_ENABLED=false` by default.  Sudo access is an
explicit opt-in requiring a config change.  The `shellmcp` server inherits
only the current user’s permissions.

- Status: ✅ Mitigated by default

---

## Remaining Attack Surfaces

### 1. Prompt Injection → Shell Execution

**Risk:** JARVIS reads content from web pages, files, or tool outputs when
instructed.  A malicious document could embed instruction text that the
LLM interprets as a user command and routes to `shellmcp`.

**Current mitigations:**
- `CONFIRMATION_MODE=smart` (default) — the host assigns a minimum threat
  level to every tool call: `classify()` = max(host floor for
  command-execution tools, manifest-declared level, dangerous-payload scan of
  params), and anything >= ELEVATED is blocked pending user confirmation.
  A tool author cannot opt out of gating a dangerous tool — the former
  `shellmcp` gap (#159/#162) is closed by the host floor in
  `jarvis/core/threat_level.py`.
- `jarvis/core/threat_level.py` — scans dispatched tool *parameters* for
  dangerous payloads (sudo, `rm -rf`, pipe-to-shell, …) and raises the
  confirmation threat level accordingly. (There is no scanner on direct user
  input.)
- **MCP output containment hashing — implemented end-to-end (#165).** Tool
  output is wrapped in a boundary tag keyed by a **128-bit CSPRNG nonce**
  (`dispatch/src/nonce.rs`), emitted in the EXIT signal as
  `[hash=h] 200 <h>...raw MCP server output...</h>`
  (`dispatch/src/orchestrator.rs`). The daemon verifies the tag against the
  trusted per-task nonce on every EXIT/TIMEOUT
  (`jarvis/dispatch/boundary.py`; `EventMerger` calls `verify_and_mark`) and
  prepends an in-band UNVERIFIED marker on failure; all system prompts
  (`jarvis/config.py`) instruct the LLM to treat boundary-tagged content as
  data only.

**Not yet mitigated:**
- Injection arriving through LLM-processed external content (web pages,
  documents) that bypasses the direct-input scanner.
- `CONFIRMATION_MODE=allow_all` disables all execution gates.

**Recommendation:** Never set `CONFIRMATION_MODE=allow_all` in environments
where JARVIS has web-browsing or file-reading capabilities.

### 2. Same-User Unix Socket Injection

**Risk:** Any process running as the same OS user can connect to
`$JARVIS_DATA_DIR/input.sock` (or the GUI socket `jarvis.sock`) and inject
commands into the JARVIS event loop.  Both the input and GUI sockets also
accept TLA confirmation-control messages (`approve_confirmation`,
`approve_all_confirmations`, …), so socket compromise defeats the
confirmation gate as well.

**Current mitigations:**
- `jarvis/core/socket_security.py` sets socket permissions to `0600` —
  only the owner can read/write.
- `verify_socket_ownership()` checks that the socket was created by the
  current user before connecting, preventing pre-created hijack sockets.

**Planned:**
- `SO_PEERCRED` check on connection accept — the kernel exposes the
  connecting process’s UID, GID, and PID.  JARVIS can refuse connections
  from unexpected PIDs.
- Session token: a random token generated at startup that the connecting
  process must include in its first message, preventing passive observers
  from injecting commands mid-session.

### 3. MCP Server Trust

**Risk:** A malicious local process exposing the MCP stdio interface could
be picked up if `dispatch` or `dmcp` auto-discovered arbitrary processes.

**Current mitigations:**
- MCP servers must be explicitly registered in the dispatch config or
  `dmcp` manifest.  No auto-discovery of arbitrary local processes.
- `mcp-registry` requires PR review before inclusion.

**Now implemented:**
- `dmcp install` verifies `integrity.manifestSha256` (raw bytes, before
  parse/merge) and the autonomous agent is source-confined to configured
  registries (id-only install, no source mutation over `dmcp serve`).
  Cryptographic signing of the registry itself remains planned.

### 4. Contextor Memory Poisoning (RAG Poisoning)

**Risk:** If an attacker can influence what gets stored in the contextor
(e.g. via a crafted conversation), poisoned memories could be injected
into future LLM contexts through RAG retrieval.

**Current mitigations:**
- Contextor stores data at `$JARVIS_DATA_DIR/memory/` (Linux default
  `~/.local/share/jarvis/memory/`) with user-only file permissions.
- `DATA_CONSENT=false` disables proactive memory, reducing the attack
  window to only explicit `remember this` commands.

**Not yet mitigated:**
- No content validation on memories stored through the LLM path.

---

## Security Configuration Reference

| Setting | Safe default | Risk if changed |
|---|---|---|
| `CONFIRMATION_MODE` | `smart` | `allow_all` disables all tool confirmation |
| `CONFIRMATION_TIMEOUT` | `0` (never auto-deny; confirmations stay pending until answered, #185) | a positive value auto-denies unanswered confirmations after N seconds — safer for unattended/headless hosts, at the cost of killing unattended legitimate work |
| `JARVIS_SUDO_ENABLED` | `false` | `true` grants shell access to privileged commands |
| `providers.json` | (empty) | API providers store keys in this file |
| `NOTIFICATION_SILENT` | `false` | `true` suppresses desktop confirmation UI |
| `DATA_CONSENT` | `true` | Controls proactive vs explicit memory only |

---

## Responsible Disclosure

Security issues should be reported via the process described in
`SECURITY.md`.  Please do not open public GitHub issues for unpatched
vulnerabilities.

---

## Changelog — corrected claims

*2026-07-22:* taxonomy formalized to seven threats (Forgetful Context split from Bloated Context, 2026-07); Threat 2 upgraded to implemented — the daemon now verifies the dispatch boundary nonce (`jarvis/dispatch/boundary.py`, #165) and system prompts carry the data-only instruction; Threats 4/5 upgraded to implemented — the host floor in `threat_level.py` force-confirms exec tools regardless of manifest (#159/#162 closed); nonexistent `input_guard.py` replaced with the real payload scanner in `threat_level.py`; `~/.jarvis/` paths corrected to `$JARVIS_DATA_DIR` (Linux default `~/.local/share/jarvis/`, contextor at `.../memory/`); GUI socket (`jarvis.sock`) added to assets and the same-user risk analysis (both sockets carry TLA confirmation control); `CONFIRMATION_TIMEOUT` added to the configuration reference.
