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
- **Claude feature branches**: `claude/ai-os-security-research-CdkTO`

---

## Current State (as of 2026-05-27)

### What's done (on `claude/ai-os-security-research-CdkTO`)

**Phase 2.2 — TUI decomposition** (complete):
`main.py` and `app.py` were shrunk. TUI split into: `app.py`, `actions.py`,
`lifecycle.py`, `local_input.py`, `output.py`, `session_sidebar.py`,
`status_bar.py`, `help_screen.py`, `slash_commands_doc.py`, `confirm_modal.py`,
`settings_modal.py`.

**Phase 2.2 security additions** (files exist, not yet wired):
- `jarvis/tui/confirm_modal.py` — Textual `ModalScreen` for tool confirmation
- `jarvis/tui/settings_modal.py` — Read-only `DataTable` of all Config attributes
- `jarvis/core/socket_security.py` — `harden_socket_path()`, `verify_socket_ownership()`, `warn_if_allow_all()`
- `jarvis/core/input_guard.py` — regex scanner for 4 prompt-injection patterns

**Security wiring still pending** (these files exist but nothing calls them yet):
1. `jarvis/tui/app.py` — import + mount `ConfirmModal` + `SettingsModal`; bind `Ctrl+,`
2. `jarvis/tui/lifecycle.py` — register TUI confirmation callback
3. `jarvis/core/confirmation_manager.py` — add TUI channel
4. Socket setup (`cli.py` or `runtime/`) — call `harden_socket_path()` + `warn_if_allow_all()`
5. Input handling — call `input_guard.scan()` before LLM

**`run` action + startup bug fixes** (pushed):
- Fixed `lifecycle.py` sync condition, event merger kwarg, `warn_if_allow_all` call
- Fixed `Config` missing `LLM_IO_LOG` and `LLM_ROOT_PROMPT_UNIFIED` attributes
- Added `run` action to `command_parser.py` and `root_actions.py`
- Added global keyword fallback and stop-word filtering to `tool_discovery.py`

### Known issues on current branch

- `run` action is still fundamentally broken (keyword discovery finds wrong servers)
- Embedding search never fires (`EMBEDDING_SEARCH_THRESHOLD=100`, registry has ~24 servers)
- **Planned replacement**: see Tool Discovery Redesign below

---

## Tool Discovery Redesign (next major task)

Full spec: `docs/tool-discovery-redesign.md`

The `run` action is being replaced with a multi-step explicit flow. The LLM
reasons about what *capability* it needs, then searches, selects, and dispatches.

### New LLM action set

| Action | Purpose |
|--------|---------|
| `search_tools` | LLM outputs a capability description; system does embedding search on full vector index |
| `get_server_docs` | LLM picks a server from results; system returns full tool list + param schemas |
| `install_server` | LLM installs an available (not yet installed) server from registry |
| `configure_server` | LLM sets config values on an installed server (`dmcp config set`) |
| `dispatch` | Unchanged — LLM dispatches concrete tool calls after seeing SERVER_DOCS |

### Key principle

The LLM must derive the capability from the user's request:
```
User: "check my python version"
LLM:  → "to do this I need to execute a shell command"
      → {"action": "search_tools", "capability": "execute shell commands"}
```

NOT keyword pass-through:
```
WRONG: {"action": "search_tools", "capability": "check python version"}
```

### Files to change

| File | Change |
|------|--------|
| `jarvis/config.py` | Lower `EMBEDDING_SEARCH_THRESHOLD` to 3; rewrite `LLM_ROOT_PROMPT` |
| `jarvis/core/command_parser.py` | Add 4 new actions; remove `run` and `find_tools` |
| `jarvis/runtime/root_actions.py` | Add 4 new handlers; remove `_run_handle` |
| `jarvis/runtime/root_context.py` | Add `format_search_results()`, `format_server_docs()` |
| `jarvis/dispatch/adapter.py` | Add `search_tools()`, `get_server_docs()`, `configure_server()` |
| `jarvis/dispatch/tool_discovery.py` | **Delete** (keyword discovery removed entirely) |
| `jarvis/dispatch/dmcp_registry.py` | Remove `search_servers`, `_local_installed_servers`, `list_visible_servers` |

---

## Planned Architectural Changes

### 1. deps/rust/ submodule reorganization

Move `dispatch`, `dmcp`, `contextor` to:
```
deps/
  rust/
    dispatch/
    dmcp/
    contextor/
```

### 2. pyproject.toml Rust integration

- `jarvis-ai` — Python runtime only
- `jarvis-ai[tools]` — also builds Rust binaries via `cargo build --release`

---

## Security / Threat Model Notes

### Context Bloat (Threat #7)

- **Cause**: as conversation grows, early security constraints get diluted in attention weight
- **Mitigation (planned)**: persistent constraint register prepended to every prompt by the daemon

### 4-Tier Action Policy

| Tier | Behaviour |
|------|-----------|
| SAFE | Run silently |
| ELEVATED | Run + audit log entry |
| DANGEROUS | Block until explicit user confirmation |
| FORBIDDEN | Hard block unconditionally |

`confirm_modal.py` + `confirmation_manager.py` implement the DANGEROUS gate.
`socket_security.py` hardens the confirmation channel.
`input_guard.py` catches prompt injection before LLM.

---

## Focus & Priorities

1. **Tool Discovery Redesign** — replace `run` with `search_tools` / `get_server_docs` / `dispatch`
2. **Security wiring** — wire the 4 security files that exist but aren't called yet
3. **mcp-registry** — add real servers beyond calculator/hello examples
4. **jarvisos** — lower priority; minimal base (custom kernel + TUI daemon)

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

## Quick File Reference

| Path | Purpose |
|------|---------|
| `jarvis/main.py` | Entry point |
| `jarvis/tui/app.py` | Textual app shell |
| `jarvis/tui/lifecycle.py` | App startup/shutdown, callback registration |
| `jarvis/tui/confirm_modal.py` | Tool confirmation modal (UNWIRED) |
| `jarvis/tui/settings_modal.py` | Config viewer modal (UNWIRED) |
| `jarvis/core/confirmation_manager.py` | Multi-channel confirmation gate |
| `jarvis/core/socket_security.py` | Socket hardening (UNWIRED) |
| `jarvis/core/input_guard.py` | Prompt-injection scanner (UNWIRED) |
| `jarvis/core/command_parser.py` | LLM response parser + action registry |
| `jarvis/dispatch/adapter.py` | Python wrapper for Rust dispatch binary |
| `jarvis/dispatch/discovery.py` | Embedding search via dmcp vector index |
| `jarvis/dispatch/dmcp_registry.py` | dmcp CLI wrappers (install, tools, config) |
| `jarvis/dispatch/tool_discovery.py` | Keyword fallback (SCHEDULED FOR DELETION) |
| `jarvis/dispatch/event_merger.py` | Merges voice/CLI/socket/dispatch events |
| `jarvis/dispatch/goal_manager.py` | Long-horizon goal tracking with timers |
| `jarvis/runtime/root_actions.py` | ROOT-mode LLM response action handlers |
| `jarvis/runtime/root_context.py` | Context assembly for ROOT-mode prompts |
| `docs/tool-discovery-redesign.md` | Full spec for the new discovery workflow |
| `docs/SECURITY-ARCHITECTURE.md` | Threat model vs OpenClaw CVEs |
