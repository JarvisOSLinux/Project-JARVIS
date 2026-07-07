# CLAUDE.md — Project JARVIS

## What This Is

AI assistant daemon with LLM orchestration, MCP tool dispatch, and multiple
interface layers (TUI, CLI, voice, socket). The LLM reasons about what tools
it needs, discovers them via semantic search, and executes them in parallel
through a Rust orchestrator.

## Ecosystem Overview

```
Project-JARVIS (Python)     — daemon, LLM orchestration, interfaces
  jarvis/dispatch/          — Python adapter wrapping the Rust dispatch binary
  jarvis/core/              — security, confirmation, logging, I/O
  jarvis/tui/               — Textual TUI (decomposed into ~12 modules)
  jarvis/llm/               — LLM client (Ollama)
  jarvis/voice/             — STT (Vosk) + TTS (Piper)
  jarvis/sessions/          — session persistence
  jarvis/runtime/           — event loop, action handlers

deps/rust/dispatch          — Rust signal-driven MCP task orchestrator
deps/rust/dmcp              — Rust MCP server manager (package manager for MCP)
deps/rust/contextor         — Rust vector-based long-term memory store
mcp-registry                — JSON registry of installable MCP servers
jarvisos-app                — Desktop GUI (Rust + CXX-Qt + Qt6/QML)
jarvisos                    — AI-native Linux distro (Arch base + custom kernel)
```

### Dispatch: Python wrapper vs Rust engine

Two things share the name "dispatch":

| Layer | Location | Language | Role |
|-------|----------|----------|------|
| Interface | `jarvis/dispatch/` | Python | Wraps the Rust binary, translates Python calls to MCP JSON-RPC, surfaces signals to the event loop |
| Engine | `deps/rust/dispatch` (submodule) | Rust/Tokio | Spawns MCP tool calls in parallel, tracks PIDs, fires INIT/EXIT/REMIND/WAIT/KILL signals |

One-liner: **Python dispatch wraps Rust dispatch.** The Python package
docstring (`jarvis/dispatch/__init__.py`) documents this boundary.

---

## Tool Discovery Flow

The LLM uses a multi-step flow to find and invoke tools:

1. **`search_tools`** — LLM derives a capability from the user's request,
   system does embedding search on the vector index
2. **`get_server_docs`** — LLM picks a server from results, system returns
   full tool list + param schemas
3. **`install_server`** / **`configure_server`** — if the server isn't installed
   or needs configuration
4. **`dispatch`** — LLM dispatches concrete tool calls for parallel execution

Key: the LLM derives *capability*, not keywords. "check my python version" →
`{"action": "search_tools", "capability": "execute shell commands"}`.

---

## Security Architecture

### 4-Tier Action Policy

| Tier | Behaviour |
|------|-----------|
| SAFE | Run silently |
| ELEVATED | Run + audit log entry |
| DANGEROUS | Block until explicit user confirmation |
| FORBIDDEN | Hard block unconditionally |

### Confirmation Modes

`CONFIRMATION_MODE` in config controls the confirmation gate:

| Mode | Behaviour |
|------|-----------|
| `allow_all` | No prompts, everything auto-approved |
| `smart` | Only ask when tool declares `confirmation_required: true` |
| `ask_all` | Confirm every tool call |

### Confirmation Channels

The `ConfirmationManager` (core) is interface-agnostic. It routes confirmation
requests to the best available channel:

1. **TUI modal** — `ConfirmModal` in Textual (Y/N inline)
2. **Desktop notification** — `notify-send` with Allow/Deny actions
3. **Socket** — JSON over output socket for external clients
4. **CLI prompt** — stdin `[y/N]` fallback

Pending confirmations are tracked in a persistent, queryable list
(`ConfirmationManager.list_pending()`), reviewable anytime via `jarvis
confirmations` (CLI) or `list_confirmations`/`approve_confirmation`/
`deny_confirmation`/`approve_all_confirmations` (GUI socket) rather than
expiring on a clock. `CONFIRMATION_TIMEOUT` defaults to `0` (disabled);
set it > 0 to restore auto-deny for unattended/headless setups.

### Socket Security

`socket_security.py` hardens the IPC socket (`/tmp/jarvis.sock`):
- `harden_socket_path()` — sets 0600 permissions
- `verify_socket_ownership()` — checks UID before connecting
- `warn_if_allow_all()` — logs warning when confirmation is disabled

---

## Packaging

### Install Extras

| Extra | What it adds |
|-------|-------------|
| `jarvis-ai` | Core daemon (text I/O, LLM, dispatch) |
| `jarvis-ai[tui]` | Textual TUI (`jarvis tui`) |
| `jarvis-ai[voice]` | Vosk STT + Piper TTS |
| `jarvis-ai[voice-aec]` | Voice + acoustic echo cancellation (native-compiled, barge-in fix) |
| `jarvis-ai[dev]` | pytest, black, isort, flake8, mypy, pre-commit |
| `jarvis-ai[all]` | Everything above |

### Rust Dependencies

Submodules under `deps/rust/` — dispatch, dmcp, contextor. Build with
`cargo build --release` in each directory. Binaries must be on PATH.

## Planned Work

### API Recycling (#78)

Provider failover pool with priority ordering and automatic cooldown/recovery.

---

## Build & Test

```bash
pip install -e ".[all]"     # Install with all extras
make check                  # Format + lint + typecheck + tests
```

## Conventions

- **Python**: Black formatting, type hints on public functions
- **Rust**: `cargo fmt` + `cargo clippy` clean before pushing
- **Commit messages**: imperative mood
- **No comments** explaining what code does; only non-obvious WHY
- **No backwards-compat shims**: delete unused code outright
- Prefer editing existing files over creating new ones

---

## Key Files

| Path | Purpose |
|------|---------|
| `jarvis/main.py` | Daemon entry point |
| `jarvis/cli.py` | CLI entry point |
| `jarvis/config.py` | Global configuration |
| `jarvis/tui/app.py` | Textual app shell |
| `jarvis/tui/lifecycle.py` | App startup/shutdown, callback registration |
| `jarvis/tui/confirm_modal.py` | Tool confirmation modal |
| `jarvis/tui/config_modal.py` | Tabbed config/provider settings modal (F2 / /settings) |
| `jarvis/tui/provider_modal.py` | Provider add/edit modal |
| `jarvis/core/confirmation_manager.py` | Multi-channel confirmation gate |
| `jarvis/core/socket_security.py` | Socket hardening |
| `jarvis/core/command_parser.py` | LLM response parser + action registry |
| `jarvis/core/voice_state.py` | `VoiceState` — formal voice/response session state machine |
| `jarvis/voice/chime.py` | Wake-word earcon: path validation + best-effort playback |
| `jarvis/voice/audio.py` | Audio device detection + `passes_noise_gate` RMS filter |
| `jarvis/voice/aec/webrtc_aec.py` | `WebRtcAEC` — acoustic echo cancellation (barge-in fix, #143) |
| `jarvis/runtime/io.py` | Input/GUI/output sockets — confirmation queries, session CRUD, settings + provider CRUD, client labeling/`list_clients`/`shutdown_request` |
| `jarvis/runtime/lifecycle.py` | Signal handlers, startup service wiring, `shutdown_request`'s safe save-then-exit sequence |
| `jarvis/dispatch/goal_manager.py` | `GoalManager` — goal tree + on-disk archive (`archive_all()` for shutdown) |
| `jarvis/core/providers.py` | Provider pool CRUD (`providers.json`), shared by CLI, TUI, and the GUI socket |
| `jarvis/dispatch/adapter.py` | Python wrapper for Rust dispatch binary |
| `jarvis/dispatch/discovery.py` | Embedding search via dmcp vector index |
| `jarvis/dispatch/dmcp_registry.py` | dmcp CLI wrappers (install, tools, config) |
| `jarvis/dispatch/event_merger.py` | Merges voice/CLI/socket/dispatch events |
| `jarvis/runtime/root_actions.py` | ROOT-mode LLM response action handlers |
| `jarvis/runtime/root_context.py` | Context assembly for ROOT-mode prompts |
| `jarvis/runtime/llm_bridge.py` | `ask_llm()` — the sole locked/mode-atomic LLM call site |
| `jarvis/runtime/events.py` | Event routing + per-event task tracking (concurrent goals) |
