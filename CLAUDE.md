# CLAUDE.md — Project JARVIS Session Context

This file captures architectural decisions, in-progress work, and conventions so
no context is lost across session boundaries.

---

## Ecosystem Overview

Project JARVIS is a vertically integrated AI assistant stack. Each repo has a
strictly scoped responsibility:

```
Project-JARVIS (Python)     — voice/CLI assistant daemon, LLM orchestration
  jarvis/dispatch/          — Python adapter that manages the Rust dispatch binary
  jarvis/core/              — security, confirmation, logging, I/O
  jarvis/tui/               — Textual TUI (decomposed into ~12 focused modules)
  jarvis/llm/               — LLM client (Ollama)
  jarvis/voice/             — STT (Vosk) + TTS (Piper)
  jarvis/sessions/          — session persistence
  jarvis/runtime/           — event loop, signal handling

deps/rust/ (planned)        — Rust submodule dependencies (see below)
  dispatch/                 — Rust/Tokio signal-driven MCP task orchestrator
  dmcp/                     — Rust MCP server manager (like a package manager)
  contextor/                — Rust vector-based context/memory store

mcp-registry/               — JSON registry of installable MCP servers
discover/                   — KDE Discover fork; MCP Server subcatalogue added
                              in Development section (changes on non-main branch)
jarvisos/                   — Custom Linux distro (lower priority — see focus below)
jarvisos-app/               — Desktop GUI widget
```

### The Two-Dispatch Clarification

`jarvis/dispatch/` (Python) is the **interface layer**: it wraps the Rust
`dispatch` binary, manages subprocess lifecycle, translates Python calls into
MCP JSON-RPC, and surfaces signals back to the JARVIS event loop.

The standalone `dispatch` repo (Rust/Tokio) is the **execution engine**: it
spawns MCP tool calls in parallel, tracks PIDs, fires INIT/EXIT/REMIND/WAIT/KILL
signals, and only wakes the LLM when there is something to reason about.

They are not alternatives — Python dispatch wraps Rust dispatch.

---

## Active Branch & PR

- **Working branch**: `yakup/dev`
- **Open PR**: #43 "Yakup/dev" → main (created 2026-04-24)
- **Claude feature branches**: `claude/review-jarvis-core-EQODl`, `claude/memory-update-forget`

---

## Current State of yakup/dev (as of 2026-04-24)

### What's done

**Phase 2.2 — TUI decomposition** (many commits, complete):
`main.py` and `app.py` were shrunk significantly. The TUI is now split into:
`app.py`, `actions.py`, `lifecycle.py`, `local_input.py`, `output.py`,
`session_sidebar.py`, `status_bar.py`, `help_screen.py`,
`slash_commands_doc.py`, `confirm_modal.py`, `settings_modal.py`.

**Phase 2.2 security additions** (commit `09e39d50`, complete):
Four new files added, all additive (nothing calls them yet):
- `jarvis/tui/confirm_modal.py` — Textual `ModalScreen` for tool confirmation;
  shows tool list with Allow/Deny buttons + y/n/Escape keybindings
- `jarvis/tui/settings_modal.py` — Read-only `DataTable` of all 16 Config
  attributes; highlights `CONFIRMATION_MODE=allow_all` and
  `JARVIS_SUDO_ENABLED=true` in red
- `jarvis/core/socket_security.py` — `harden_socket_path()` (chmod 0600),
  `verify_socket_ownership()`, `warn_if_allow_all()`
- `jarvis/core/input_guard.py` — regex scanner for 4 prompt-injection
  patterns: instruction override, jailbreak preamble, role override, system
  tag injection

**Security architecture doc** (commit `c63df55a`, complete):
`docs/SECURITY-ARCHITECTURE.md` — maps OpenClaw CVEs to JARVIS design decisions,
documents remaining attack surfaces and mitigations.

### What's pending (Commit 3 — timed out before it could be pushed)

The 4 security files above exist but are **not yet imported or called by
anything**. The following wiring changes are needed across ~5 existing files:

1. `jarvis/tui/app.py` — import and mount `ConfirmModal` + `SettingsModal`;
   bind `Ctrl+,` to open settings modal
2. `jarvis/tui/lifecycle.py` — register TUI confirmation callback so
   `confirmation_manager` can push confirm requests to the TUI
3. `jarvis/core/confirmation_manager.py` — add TUI channel alongside the
   existing socket/CLI channels
4. Socket setup code (likely `cli.py` or `runtime/`) — call
   `harden_socket_path()` and `warn_if_allow_all()` at startup
5. Input handling path — call `input_guard.scan()` before passing user input
   to the LLM

This is the immediate next task when resuming work.

---

## Planned Architectural Changes

### 1. deps/rust/ submodule reorganization

`dispatch`, `dmcp`, and `contextor` are currently submodules at repo root.
Move them to:
```
deps/
  rust/
    dispatch/
    dmcp/
    contextor/
```
This cleanly separates Python source from vendored Rust dependencies.

### 2. pyproject.toml Rust integration

Add `deps/rust/` components as installable extras via a build hook:
- Base install (`jarvis-ai`) — Python runtime only
- `jarvis-ai[tools]` — also builds and installs Rust binaries
  (`dispatch`, `dmcp`) via `cargo build --release` + copies to
  `~/.local/bin` (or similar)
- Approach: binaries route first (subprocess-invoked). PyO3/maturin only
  if `contextor` needs to be called directly from Python for vector search.

---

## Security / Threat Model Notes

### Context Bloat (Threat #7 in jarvisos taxonomy)

The jarvisos README calls this "Forgetful context constraint enforcement" but
"context bloat" is the more precise engineering description:

- **Cause (context bloat)**: as conversation grows, early-session security
  constraints get diluted or pushed down in attention weight
- **Effect**: LLM stops honoring rules it was told at session start

**Mitigation (planned, not yet implemented)**:
The daemon must own security invariants, not the model. Implement a
**persistent constraint register** that prepends active security rules into
every LLM prompt, independent of conversation length. The model's context
window is unreliable for security enforcement; the daemon wrapper is not.

### 4-Tier Action Policy

| Tier | Behaviour |
|------|-----------|
| SAFE | Run silently |
| ELEVATED | Run + audit log entry |
| DANGEROUS | Block until explicit user confirmation |
| FORBIDDEN | Hard block unconditionally |

The `confirm_modal.py` TUI component and `confirmation_manager.py` are the
Python-level implementation of the DANGEROUS tier gate.
`socket_security.py` hardens the confirmation channel against replay.
`input_guard.py` catches prompt injection before it reaches the LLM.

---

## Focus & Priorities

### Primary: Project-JARVIS — distro-agnostic daemon

The goal is a daemon that works on any Linux (and eventually macOS/Windows)
with stock Ollama. Kernel integration (`linux-jarvisos`) is research, not the
deployment path.

### jarvisos

Lower priority. Target is a minimal base: the custom kernel + the TUI-only
JARVIS daemon. No ISO installer work right now. Will revisit later.

### mcp-registry

Needs real servers beyond the current calculator/hello examples. Even 5-10
useful servers would significantly change the `dmcp` value proposition.

---

## Conventions

- **Python**: Black formatting, type hints on public functions
- **Rust**: `cargo fmt`, `cargo clippy` clean before pushing
- **Commit messages**: imperative mood, reference the file/module changed
- **Branch naming**: `claude/<task>-<id>` for Claude-authored branches
- **No comments** explaining what code does; only explain non-obvious WHY
- **No backwards-compat shims**: delete unused code outright
- Prefer editing existing files over creating new ones
- Test with `make check` before pushing (format + lint + typecheck + tests)

---

## Quick File Reference (yakup/dev)

| Path | Purpose |
|------|---------|
| `jarvis/main.py` | Entry point (shrunk in Phase 2.2) |
| `jarvis/tui/app.py` | Textual app shell |
| `jarvis/tui/lifecycle.py` | App startup/shutdown, callback registration |
| `jarvis/tui/confirm_modal.py` | Tool confirmation modal (UNWIRED) |
| `jarvis/tui/settings_modal.py` | Config viewer modal (UNWIRED) |
| `jarvis/core/confirmation_manager.py` | Multi-channel confirmation gate |
| `jarvis/core/socket_security.py` | Socket hardening (UNWIRED) |
| `jarvis/core/input_guard.py` | Prompt-injection scanner (UNWIRED) |
| `jarvis/dispatch/adapter.py` | Python wrapper for Rust dispatch binary |
| `jarvis/dispatch/discovery.py` | MCP server discovery logic |
| `jarvis/dispatch/event_merger.py` | Merges voice/CLI/socket/dispatch events |
| `jarvis/dispatch/goal_manager.py` | Long-horizon goal tracking with timers |
| `docs/SECURITY-ARCHITECTURE.md` | Threat model vs OpenClaw CVEs |
| `docs/memory-management.md` | Update & forget design spec (`update_memory` action) |
