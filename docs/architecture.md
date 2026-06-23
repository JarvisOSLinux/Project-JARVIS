# Project JARVIS — Architecture Reference

Comprehensive guide to every subsystem. Cross-referenced with `CLAUDE.md` for build/test commands.

---

## System Overview

```
User Input (TUI / CLI / voice / socket)
          │
          ▼
    EventMerger          ← merges all input streams into one async queue
          │
          ▼
    ROOT LLM loop        ← main.py / runtime/dispatch_flow.py
      │  decides action: respond / search_tools / dispatch / store / recall …
      │
      ├─ respond           → output to TUI/socket/TTS
      ├─ search_tools      → dispatch/discovery.py (embedding search on dmcp index)
      ├─ get_server_docs   → dmcp_registry.get_server_docs()
      ├─ install_server    → dmcp_registry.install()
      ├─ configure_server  → dmcp_registry.configure()
      ├─ dispatch ─────────→ Rust dispatch binary (parallel MCP tool calls)
      │                         signals (INIT/EXIT/REMIND) loop back to EventMerger
      └─ store/recall/…   → Rust contextor binary (vector memory)
```

---

## Daemon Core

**Entry point:** `jarvis/main.py`

`ComponentFactory` (`jarvis/core/component_factory.py`) wires all subsystems together at startup. The main loop:

1. Registers signal handlers (`lifecycle.py`)
2. Starts IPC socket listeners (`runtime/io.py`)
3. Enters `asyncio` event loop
4. Feeds events from `EventMerger` into `dispatch_flow.py` indefinitely

`ComponentFactory` decides whether to enable voice, TUI, contextor, etc. based on config and available hardware. All subsystems are optional — the daemon degrades gracefully (e.g. no mic → text-only, no contextor binary → memory disabled).

---

## LLM Layer (`jarvis/llm/`)

| File | Role |
|------|------|
| `provider_pool.py` | Priority-ordered pool with per-provider cooldown and automatic failover |
| `base.py` | `BaseLLMProvider` ABC — `chat(messages) -> str`, token tracking |
| `providers/ollama.py` | Ollama client; lazy init, auto-start, auto-pull |
| `chat.py` | Streaming/retry wrapper used by ROOT and DISPATCH LLM calls |
| `context_manager.py` | Trims conversation history to stay within context window |

**Provider configuration:** `~/.config/jarvis/providers.json` (managed by `jarvis /providers add` or F2 → Providers tab in TUI). The legacy single-provider `.env` approach was removed in v1.0.0.

**Ollama auto-start:** When `OLLAMA_AUTO_START=true` (default), `ollama.py` attempts `systemctl --user start ollama` → `systemctl start ollama` → direct `Popen("ollama serve")` before the first request. Rate-limited to one attempt per 60 seconds per host. Skipped for remote Ollama instances.

**Lazy init:** `OllamaProvider` defers `ollama.Client()` creation until the first `chat()` call. The pool constructs providers at config-load time without opening connections.

---

## Runtime (`jarvis/runtime/`)

The runtime translates ROOT LLM JSON actions into side effects.

| File | Role |
|------|------|
| `dispatch_flow.py` | Main event loop: dequeues events, calls ROOT LLM, routes actions |
| `root_actions.py` | Handler for every ROOT action: respond, search_tools, dispatch, store, … |
| `root_context.py` | Assembles the context object (goals, memories, search results, etc.) injected into each ROOT LLM call |
| `root_handlers.py` | Thin glue between dispatch signals (INIT/EXIT/REMIND) and root_actions |
| `io.py` | Async IPC server for `jarvis send` and output-broadcast clients (delegates socket creation to `platform`) |
| `lifecycle.py` | Signal handlers (SIGTERM/SIGINT), graceful shutdown |
| `goal_updates.py` | Applies `goal_updates` from ROOT response to the goal manager |
| `llm_bridge.py` | Wraps LLM provider pool, injects ROOT prompt, handles JSON extraction |
| `sync_ask.py` | Synchronous `jarvis ask` — one-shot query without the full event loop |

**ROOT LLM prompt:** Defined in `Config.LLM_ROOT_PROMPT` (`config.py`). Two variants — with and without contextor — selected by `ComponentFactory`. The prompt includes OS info, current date, and a strict JSON schema for all actions.

**Context assembly:** `root_context.py` collects active goals (capped by `MAX_GOALS_IN_CONTEXT`), RAG memories, pending dispatch results, confirmation state, and MCP search results. All injected as a structured block into each ROOT call.

---

## Dispatch Interface (`jarvis/dispatch/`)

The Python dispatch layer bridges the ROOT LLM to the Rust `dispatch` binary.

| File | Role |
|------|------|
| `adapter.py` | Spawns/communicates with Rust `dispatch` binary over stdio JSON-RPC |
| `discovery.py` | Embedding-based tool search; queries `dmcp` vector index |
| `dmcp_registry.py` | `dmcp` CLI wrappers: install, uninstall, list-tools, get-docs, configure |
| `event_merger.py` | `asyncio.Queue`-backed merger of voice/CLI/socket/dispatch/TUI events |
| `goal_manager.py` | In-memory goal tracker; persists active goals across ROOT turns |
| `transport.py` | Low-level subprocess I/O with the dispatch binary |

**Two things named "dispatch":** The Python `jarvis/dispatch/` is the adapter; the Rust `deps/rust/dispatch` binary is the engine. See `CLAUDE.md § Dispatch` for the distinction.

**Tool discovery flow:**
1. ROOT emits `search_tools` with a capability description
2. `discovery.py` hits `dmcp`'s vector index for semantic matches
3. ROOT calls `get_server_docs` on an installed server → gets full tool schemas
4. ROOT calls `dispatch` → `adapter.py` sends to Rust engine → parallel MCP calls
5. Signals (INIT/EXIT/REMIND/KILL) flow back through `event_merger.py`

**Embedding threshold:** Vector search auto-enables when ≥ `EMBEDDING_SEARCH_THRESHOLD` (default 3) servers are indexed. Below threshold, keyword fallback is used.

---

## Core (`jarvis/core/`)

| File | Role |
|------|------|
| `confirmation_manager.py` | Non-blocking TLA confirmation gate; routes to TUI modal / desktop notification / socket / CLI |
| `socket_security.py` | IPC hardening — delegates to `platform` for ownership check + permission enforcement |
| `command_parser.py` | Parses ROOT LLM JSON; validates schema; extracts action + params |
| `component_factory.py` | Wires all subsystems; selects config variants at startup |
| `logger.py` | Structured logging with optional color, file output |
| `output_manager.py` | Dispatches output to TUI, TTS, socket clients |
| `params_store.py` | Persistent key-value store for runtime params (e.g. output mode) |
| `providers.py` | Provider pool config load/save; `~/.config/jarvis/providers.json` I/O |
| `system_info.py` | OS/shell detection injected into ROOT prompt |

**Confirmation system:** 4-tier policy (SAFE/ELEVATED/DANGEROUS/FORBIDDEN) is enforced in `jarvis_policy.c` in the kernel; at the daemon level, `ConfirmationManager` handles tool-level confirmation per `CONFIRMATION_MODE`. Channels in priority order: TUI modal > desktop notification (`notify-send` on Linux, `osascript` on macOS) > output socket > CLI stdin.

---

## TUI (`jarvis/tui/`)

Built on [Textual](https://textual.textualize.io/). All platform-agnostic.

| File | Role |
|------|------|
| `app.py` | `JarvisApp` — Textual app shell, screen composition |
| `lifecycle.py` | Startup: registers callbacks (output, confirmation, TUI-specific commands) |
| `actions.py` | Textual action handlers for slash commands and keyboard shortcuts |
| `output.py` | Scrollable output widget with Markdown rendering |
| `status_bar.py` | Bottom bar: model, token usage (`ctx: X/Y`), mode indicator |
| `config_modal.py` | Tabbed settings modal (F2 / `/settings` / `/providers`) — config + providers tabs |
| `provider_modal.py` | Provider add/edit form pushed from config_modal |
| `server_config_modal.py` | MCP server configuration form |
| `confirm_modal.py` | Tool confirmation dialog (`[Y/n]`) |
| `help_screen.py` | `/help` overlay |
| `session_sidebar.py` | Session list sidebar |
| `local_input.py` | Text input widget |
| `slash_commands_doc.py` | Slash command registry shown in help |

**Key TUI commands:** `/providers` (or F2 → Providers tab), `/settings` (or F2 → Config tab), `/rename`, `/sessions`, `/voice`, `/text`, `/help`.

---

## Voice (`jarvis/voice/`)

| File | Role |
|------|------|
| `manager.py` | `VoiceManager` — coordinates STT, TTS, wake-word detection |
| `audio.py` | Shared audio device init (PyAudio / sounddevice) |
| `base.py` | `BaseSTT`, `BaseTTS` ABCs |
| `stt/` | Vosk STT provider |
| `tts/` | Piper TTS provider |
| `activation/` | Wake-word detection (Vosk-based) |

Voice runs in a background thread (`voice_activation_thread.py` in `runtime/`). When a wake word fires, it injects a `VOICE_INPUT` event into `EventMerger`. TTS output is triggered by `output_manager.py` after ROOT responds.

Config: `WAKE_WORDS`, `VOICE_ACTIVATION_SENSITIVITY`, `VOSK_MODEL_PATH`, `TTS_MODEL_ONNX`/`TTS_MODEL_JSON` — all in `~/.config/jarvis/jarvis.conf`.

---

## Sessions (`jarvis/sessions/`)

| File | Role |
|------|------|
| `manager.py` | Creates/loads/renames sessions; rolling summary on session close |
| `model.py` | `Session` dataclass: id, name, messages, summary |

Sessions persist conversation history to `JARVIS_DATA_DIR/sessions/`. The rolling summary keeps context compact across long sessions. `MAX_GOALS_IN_CONTEXT` (default 20) caps the number of active goals injected into the ROOT prompt.

---

## Platform Abstraction (`jarvis/platform/`)

Added in v1.0.0. Auto-detects OS at import time.

| File | Role |
|------|------|
| `__init__.py` | `current` singleton — `LinuxPlatform`, `MacOSPlatform`, or `WindowsPlatform` |
| `base.py` | `PlatformBase` ABC: `create_ipc_server`, `ipc_connect`, `ipc_secure`, `ipc_verify_owner`, `ipc_cleanup`, `config_dir`, `data_dir`, `notify`, `has_notifications`, `start_service`, `install_signal_handlers` |
| `linux.py` | `AF_UNIX`, XDG paths, `notify-send`, `systemctl` |
| `macos.py` | `AF_UNIX`, `~/Library/Application Support/`, `osascript`, `launchctl` |
| `windows.py` | TCP `127.0.0.1` + port lockfile, `%APPDATA%`, PowerShell toast, direct spawn |

All consumers (`runtime/io.py`, `cli.py`, `core/socket_security.py`, `core/confirmation_manager.py`, `llm/providers/ollama.py`, `config.py`, `runtime/lifecycle.py`) go through `from ..platform import current as platform`.

---

## Security Architecture

See `docs/SECURITY-ARCHITECTURE.md` for the full threat model and CVE context.

**Layers:**

1. **Kernel** — `jarvis_policy.c` in `linux-jarvisos` enforces 4-tier action policy (SAFE / ELEVATED / DANGEROUS / FORBIDDEN) with rate limiting via `/dev/jarvis` and `/sys/class/misc/jarvis/policy/`
2. **Daemon** — `ConfirmationManager` gates tool-level actions; `CONFIRMATION_MODE` controls when prompts appear
3. **IPC** — Unix sockets hardened to `0600` (Linux/macOS); ownership verified before connecting (`socket_security.py`)
4. **PolicyKit** — `jarvis-jarvis.rules` grants `jarvis` user privilege escalation for specific `dmcp` operations (see `packages/polkit/`)

**Threat taxonomy** (from research, `docs/research.md`): 7 threats documented during live operation, including "forgetful context" (#7) — LLM silently dropping security constraints mid-session. Active mitigations in progress: persistent constraint register in daemon, GPG verification for `dmcp` server manifests, path-based rules in `jarvis_policy.c` for `/etc`/`/usr`/`/boot` writes.

---

## Tool Discovery in Detail

```
ROOT: search_tools(capability="web search")
  → discovery.py: dmcp vector-search → ranked server list
  → ROOT sees SEARCH_RESULTS

ROOT: get_server_docs(server_id="brave-search")
  → dmcp_registry.get_server_docs() → tool schemas
  → ROOT sees SERVER_DOCS

ROOT: dispatch([{server: "brave-search", tool: "search", params: {query: "..."}}])
  → adapter.py → Rust dispatch binary → MCP JSON-RPC to brave-search server
  → INIT signal (PID) → ROOT loop continues
  → EXIT signal (output) → ROOT calls respond
```

The LLM derives a **capability** (domain/service), not keywords or implementation details. The registry (`mcp-registry`) contains installable servers covering web, GitHub, email, databases, home automation, and more — the LLM should search the registry rather than falling back to shell commands.
