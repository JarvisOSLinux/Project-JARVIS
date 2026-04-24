# JARVIS Security Architecture

## Overview

Project JARVIS is a research platform studying the security implications
of AI agents with system-level access.  This document describes the
threat model, the attack surfaces inherited from the broader AI-agent
ecosystem, and how JARVIS's architecture addresses them — with explicit
reference to the vulnerabilities that emerged with OpenClaw, the first
AI agent to trigger a major public security incident (early 2026).

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

**JARVIS:** Uses Unix domain sockets at `~/.jarvis/input.sock` and
`~/.jarvis/output.sock`.  These are file-system objects — not TCP ports
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

- Status: ✅ Curated registry · ⚠️ No cryptographic manifest signatures yet
  (planned: SHA-256 checksums verified by `dmcp install`)

### API Key / Chat History Exposure

**OpenClaw:** Exposed instances leaked Anthropic API keys, Telegram and
Slack tokens, and months of complete chat histories.

**JARVIS:**
- Default provider is local Ollama — no API key required.
- `.env` is in `.gitignore` and never emitted to logs.
- Chat history lives in `~/.jarvis/` (local, not served).
- Output socket broadcasts only to processes that explicitly connect.

- Status: ✅ No default cloud exposure · ⚠️ Users choosing `LLM_PROVIDER=api`
  must protect their own `.env`

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
- `CONFIRMATION_MODE=smart` (default) — tools declaring
  `confirmation_required: true` (including `shellmcp`) prompt the user
  before execution.
- `jarvis/core/input_guard.py` — scans direct user input for known
  injection patterns and logs a WARNING.  (Does not yet scan LLM-processed
  content from external sources.)
- **Planned — MCP output containment hashing:** Tool output returned to
  the LLM will be wrapped in a PID-derived hash tag:
  ```
  Containment Hash: <hash>
  <hash>
  ...raw MCP server output...
  </hash>
  ```
  The system prompt instructs the LLM to treat tagged content as data
  only, never as instructions.  This creates a clear semantic boundary
  between user instructions and external content, significantly raising
  the bar for injection via tool results.

**Not yet mitigated:**
- Injection arriving through LLM-processed external content (web pages,
  documents) that bypasses the direct-input scanner.
- `CONFIRMATION_MODE=allow_all` disables all execution gates.

**Recommendation:** Never set `CONFIRMATION_MODE=allow_all` in environments
where JARVIS has web-browsing or file-reading capabilities.

### 2. Same-User Unix Socket Injection

**Risk:** Any process running as the same OS user can connect to
`~/.jarvis/input.sock` and inject commands into the JARVIS event loop.

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

**Planned:**
- SHA-256 checksums on registry manifests, verified by `dmcp install`
  before running any server binary.

### 4. Contextor Memory Poisoning (RAG Poisoning)

**Risk:** If an attacker can influence what gets stored in the contextor
(e.g. via a crafted conversation), poisoned memories could be injected
into future LLM contexts through RAG retrieval.

**Current mitigations:**
- Contextor stores data at `~/.jarvis/` with user-only file permissions.
- `DATA_CONSENT=false` disables proactive memory, reducing the attack
  window to only explicit `remember this` commands.

**Not yet mitigated:**
- No content validation on memories stored through the LLM path.

---

## Security Configuration Reference

| Setting | Safe default | Risk if changed |
|---|---|---|
| `CONFIRMATION_MODE` | `smart` | `allow_all` disables all tool confirmation |
| `JARVIS_SUDO_ENABLED` | `false` | `true` grants shell access to privileged commands |
| `LLM_PROVIDER` | `ollama` | `api` requires protecting API keys in `.env` |
| `NOTIFICATION_SILENT` | `false` | `true` suppresses desktop confirmation UI |
| `DATA_CONSENT` | `true` | Controls proactive vs explicit memory only |

---

## Responsible Disclosure

Security issues should be reported via the process described in
`SECURITY.md`.  Please do not open public GitHub issues for unpatched
vulnerabilities.
