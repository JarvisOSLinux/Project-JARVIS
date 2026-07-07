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
4. Feeds events from `EventMerger` into `runtime/events.py::handle_event`, one `asyncio.create_task` per event

`ComponentFactory` decides whether to enable voice, TUI, contextor, etc. based on config and available hardware. All subsystems are optional — the daemon degrades gracefully (e.g. no mic → text-only, no contextor binary → memory disabled).

### Concurrent goal execution (Project-JARVIS#154)

Each merged event (`USER_INPUT`, `DISPATCH_SIGNAL`, `CONFIRMATION_RESPONSE`) is dispatched as its own `asyncio.Task` in `Jarvis.run()` rather than awaited inline — tracked via `runtime/events.py::track_event_task`, which logs (rather than silently swallows, or takes down the whole daemon) any unhandled exception from a goal's handler. On graceful shutdown, `run()` awaits every still-in-flight task before tearing down the rest of the daemon.

This lets a second goal (e.g. a wake-word-triggered "Hey Jarvis, do B" while goal A is still running) get a real LLM turn as soon as the LLM is free, instead of waiting for goal A's *entire* multi-round dispatch subchain to conclude. The one thing that must stay serialized is the LLM itself: `LLM` (`jarvis/llm/chat.py`) has a single mutable `chat_history`/`_mode` — two goals calling it "at the same time" would interleave into the same conversation, which is wrong regardless of thread-safety. `runtime/llm_bridge.py::ask_llm()` is the single choke point for every live LLM call; it acquires `app.llm_lock` (an `asyncio.Lock`) and asserts the caller's required `mode` atomically with the actual provider call, so a goal's turn can never run under a sibling goal's mode no matter how the event loop interleaves them. Every call site passes `mode="root"` explicitly (the only mode any live code path uses today — `dispatch_flow.py`'s legacy "dispatch" mode subchain is dead code, unreachable since ROOT began using `search_tools`/`get_server_docs`/`dispatch` directly).

Concretely: goal A calls the LLM (brief, lock held), then spends most of its time inside `app.dispatch.send_tasks()`/`wait_task()` waiting on a real MCP tool call (lock released) — during that window, goal B's task can acquire the lock and complete its own LLM turn, dispatch its own tools, and so on. Goals only ever block each other for the LLM inference instants themselves, never for the (usually much longer) tool-execution time — this is turn-level interleaving with one serialized LLM ("one brain, many hands"), not parallel LLM inference, matching the concurrency model already established for voice barge-in (#142).

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
| `base.py` | `STTProvider`, `TTSProvider`, `ActivationProvider`, `EchoCanceller` ABCs |
| `chime.py` | Wake-word earcon: path validation + best-effort playback |
| `stt/` | Vosk STT provider |
| `tts/` | Piper TTS provider |
| `activation/` | Wake-word detection (Vosk-based) |
| `aec/` | Acoustic echo cancellation provider (WebRTC-backed) |

Voice runs in a background thread (`voice_activation_thread.py` in `runtime/`). When a wake word fires: the daemon unconditionally broadcasts `{"type": "wake_word_detected"}` over the GUI socket, transitions to `VoiceState.WOKEN`, plays the wake chime (blocking — see below), then opens the STT capture window and injects a `VOICE_INPUT` event into `EventMerger` once an utterance completes. If the user says nothing within `VOICE_ACTIVATION_TIMEOUT` seconds, capture is abandoned and control returns to wake-word mode without processing anything; the timeout is disabled the moment speech is detected, so mid-sentence pauses never cut a command off early.

### Concurrent goals + barge-in

Wake-word listening restarts the moment capture ends — not after the full LLM/dispatch/TTS turn — in both the daemon path (`voice_activation_thread.py`) and the standalone `VoiceManager` path (`manager.py`). Saying "Hey Jarvis, do B" while A is still being worked on captures and queues B via `EventMerger`. Since #154, B doesn't wait for A's entire turn to conclude — each queued input is dispatched as its own task (see "Concurrent goal execution" above), so B gets a real LLM turn (and `GoalManager.add_goal()` creates a second concurrent root goal) as soon as the LLM is free, typically while A is still mid-dispatch waiting on its own tools. Goal A's task results arrive later as dispatch signals and interleave back into the same queue. When new input arrives while goals are already active, the PROCESSING broadcast carries `meta: {"concurrent_goals": N}`.

A wake word detected **while TTS is speaking** barges in: the voice thread calls `OutputManager.stop_speaking()`, which sets a stop flag checked between synthesis chunks in `PiperTTS.say()` (`stream.abort()` discards already-buffered audio), then the WOKEN broadcast carries `meta: {"barge_in": true}` and capture proceeds normally. The mic is live during TTS, so JARVIS can hear its own voice (including its own wake word) — acoustic echo cancellation (below) is the fix.

Config: `WAKE_WORDS`, `VOICE_ACTIVATION_SENSITIVITY`, `VOICE_ACTIVATION_TIMEOUT`, `WAKE_CHIME_PATH`, `NOISE_GATE_RMS_THRESHOLD`, `VOSK_MODEL_PATH`, `TTS_MODEL_ONNX`/`TTS_MODEL_JSON` — all in `~/.config/jarvis/jarvis.conf`.

### Noise gate (`jarvis/voice/audio.py::passes_noise_gate`)

Raw int16 PCM chunks are RMS-gated in both `VoskSTT` (`stt/vosk_stt.py`) and `VoskActivation` (`activation/vosk_activation.py`) *before* they ever reach `recognizer.AcceptWaveform()` — a chunk quieter than `NOISE_GATE_RMS_THRESHOLD` never becomes a Vosk hypothesis at all, rather than being filtered after the fact. Deliberately amplitude-based, not confidence- or word-based: Vosk is never configured for word-level confidence scoring, and a hardcoded noise-word denylist isn't reliable.

Wake-word matching (`VoskActivation._check_for_wake_word`) only runs on Vosk's *finalized* results now — partial hypotheses (Vosk's least reliable output) are never checked, removing a source of false-positive wake triggers.

`VoskSTT`'s `phrase_timeout` parameter was removed — it was accepted by the constructor and set by `component_factory.py`, but never actually read anywhere in `_process_loop`; only `silence_timeout` (pause-based endpointing) was ever wired up.

### Acoustic echo cancellation (`jarvis/voice/aec/`, Project-JARVIS#143)

`WebRtcAEC` (`aec/webrtc_aec.py`) wraps `aec-audio-processing`, which vendors
freedesktop.org's `webrtc-audio-processing` — the same AEC engine behind
PipeWire's own `module-echo-cancel` — via a SWIG binding, rather than a
bespoke DSP filter. It's an optional native-compiled extra
(`project-jarvis[voice-aec]`), gated by `AEC_ENABLED` (default `false`).

`ComponentFactory.create_echo_canceller_optional()` builds one shared
instance (matching the TTS provider's own sample rate for the reference
signal) after TTS is constructed, and hands it to `PiperTTS` (as
`echo_canceller`, feeding `feed_reference()` with every synthesized chunk)
and to `VoskSTT`/`VoskActivation` (as `echo_canceller`, calling `process()`
on the raw mic chunk before the noise gate in `_process_loop`/`_listen_loop`).
A single instance must be shared across all three — the mic-side cancellation
and the TTS-side reference feed only make sense against the same adaptive
filter state. A failure in `process()`/`feed_reference()` is caught and
logged, falling back to raw audio — AEC is a quality improvement, not a
dependency STT/TTS should ever break on.

Two non-obvious constraints, found only by exercising the library, not from
its docs:

- `aec-audio-processing`'s `process_reverse_stream` computes frame size from
  the *forward* stream's configured rate regardless of what
  `set_reverse_stream_format` declares (confirmed by reading its C++ source).
  `WebRtcAEC` therefore always frames both streams at `sample_rate` (16kHz,
  matching Vosk) and resamples the TTS reference down from its own rate
  before framing, rather than trusting the reverse-rate field to do it.
- WebRTC's internal echo-path tracking needs forward/reverse `process_stream`/
  `process_reverse_stream` calls interleaved roughly one-for-one. Buffering
  each side's producer chunks (VoskSTT's 250ms mic callbacks; Piper's
  independently-sized TTS chunks) and draining each side's queue in an
  uninterrupted burst measured ~6dB attenuation on synthetic echo — buffering
  both sides and draining one matched frame pair at a time (`_drain_locked`)
  recovered ~30-50dB, matching true lockstep. See
  `tests/test_echo_cancellation.py` for the measurements.

`AEC_STREAM_DELAY_MS` (expected speaker → air → mic round-trip delay) is the
single most sensitive tuning parameter — a value far from the real acoustic
path measurably degrades cancellation even with correct interleaving, and
there's no universally-correct default; it needs empirical measurement per
device.

### Wake chime (`jarvis/voice/chime.py`)

A short, pre-rendered earcon (not TTS) plays the instant a wake word fires, so activation is never silent — important for a headless `jarvis.service` with no terminal/GUI attached. `WAKE_CHIME_PATH` defaults to a bundled two-tone WAV (`jarvis/assets/wake_chime.wav`, synthesized offline, no external asset dependency) and can be overridden to any readable WAV file.

`validate_chime_path()` checks the file exists, is readable, and parses as a valid WAV — used both before playback (so a bad path just skips the chime with a logged warning, never crashes the wake-word thread) and before persisting a client-supplied override. `play_chime()` is entirely best-effort: missing `sounddevice`, no output device, or any playback error is caught and logged, never raised.

`WAKE_CHIME_PATH` is writable by any connected GUI-socket client via a single-purpose `{"type": "set_wake_chime_path", "path": "..."}` message (`jarvis.runtime.io._handle_set_wake_chime_path`) — validated the same way, persisted via the same `.env`/`jarvis.conf` write path the TUI settings modal and provider config already use, and broadcasts `{"type": "config_updated", "key": "WAKE_CHIME_PATH", "value": "..."}` to every connected client on success (or `{"type": "config_error", ...}` to the requester only on failure). This is deliberately a single dedicated message, not a generic "set any config key" — a generic writer over an unauthenticated local socket would let any connected client silently rewrite security-relevant settings like `CONFIRMATION_MODE`.

`{"type": "reset_wake_chime_path"}` restores `WAKE_CHIME_PATH` to `Config.DEFAULT_WAKE_CHIME_PATH` (the bundled two-tone WAV) — the settings-panel counterpart to a custom-path write, for a "restore default" action.

### GUI socket settings + provider CRUD (jarvisos-app#12)

`jarvis/runtime/io.py` exposes the same settings surface the TUI's config
modal (`jarvis/tui/config_modal.py`) already has in-process, so `jarvisos-app`
can build an equivalent settings panel without a special-cased protocol:

- `{"type": "get_settings"}` → `{"type": "settings", "confirmation_mode": ..., "wake_chime_path": ...}` — a small, explicit whitelist (matching `set_wake_chime_path`'s existing rationale: no generic arbitrary-key reader/writer over the socket).
- `{"type": "set_confirmation_mode", "mode": "smart"|"ask_all"|"allow_all"}` → validated the same way as `set_wake_chime_path`, live-applies via `Config.CONFIRMATION_MODE` (read fresh by `ConfirmationManager` on every check, so this takes effect immediately, no restart) and persists via `_update_env_setting`. Broadcasts `config_updated`/replies `config_error` with the exact same shape `set_wake_chime_path` uses.
- `{"type": "list_providers"}` → replies `{"type": "provider_list", "providers": [...]}` (requester only, a read query).
- `{"type": "add_provider", "ptype": ..., "model": ..., "name": ..., "url": ..., "api_key": ..., "temperature": ...}`, `{"type": "edit_provider", "name": ..., "fields": {...}}`, `{"type": "remove_provider", "name": ...}` — thin wrappers around `jarvis/core/providers.py`'s existing CRUD (already shared between CLI and TUI). A successful mutation broadcasts the refreshed `provider_list` to every GUI client (providers.json is shared daemon state, same rationale as session CRUD's `_reply_or_broadcast`); a `ValueError` (unknown type, missing required field, duplicate name, not found) replies `provider_error` to the requester only.
- **Known limitation, not introduced by this protocol**: there is no live `ProviderPool` reload. Like the TUI's own provider modal, a provider add/edit/remove only takes effect for the *next* daemon start — `create_llm()` builds the pool once from `providers.json` at startup. Any settings-panel UI built on this should say so.

### Connected-clients query + graceful shutdown (Project-JARVIS#146)

`app._gui_clients` changed from a bare `set[StreamWriter]` to `dict[StreamWriter, str]` (writer → label), so a client can identify itself and every other connected client can see who's attached before requesting a shutdown:

- `{"type": "hello", "label": "jarvis-app"}` — sets the sender's label (blank/missing labels are ignored, not cleared). Unlabeled clients default to `runtime.io.DEFAULT_CLIENT_LABEL` ("unlabeled") from the moment they connect, so `list_clients` always has something to show even for a client that never identifies itself.
- `{"type": "list_clients"}` → replies `{"type": "client_list", "clients": [label, ...]}` — callable by any client, no restriction. Just labels (not writer identities or connection metadata) — enough for `jarvisos-app`#17 to show "N other clients are connected" before asking the user to confirm a shutdown.
- `{"type": "shutdown_request"}` — callable by any connected client, **no daemon-enforced confirmation gate**. The socket's trust boundary is already "same local user" (`socket_security.py`); checking with the user first is each client's own job (`jarvisos-app`#17 does this by querying `list_clients` and requiring explicit confirmation before ever sending this). The handler is a one-liner: `app.stop()` — the same `request_stop()` a SIGTERM/SIGINT signal handler calls, so both triggers converge on identical behavior rather than two parallel shutdown paths.

**The safe save-then-exit sequence**, run from `Jarvis.run()`'s `finally` block regardless of what triggered it (loop exit via `request_stop()`, whether from a signal or `shutdown_request`):

1. `lifecycle.broadcast_shutdown_notice()` broadcasts `{"type": "DAEMON_SHUTDOWN", "state": ..., "goals": [...], "session_id": ..., "timestamp": ...}` to every connected GUI client — purely informational, no negotiation, no single "owner." **Must run before any socket-task cancellation**: cancelling the GUI listener task clears `app._gui_clients` in its own cleanup, so broadcasting after that reaches nobody. `goals` is every still-PENDING/ACTIVE/DEFERRED goal via `GoalManager.get_active_goals()`.
2. Input/output/GUI socket listener tasks are cancelled, then `lifecycle.join_voice_thread_if_running()` blocks (bounded, 2s default) until the voice-activation thread's own loop notices `app._running is False` and exits — previously this daemon thread (`daemon=True`, never joined) was just left to die implicitly whenever the process happened to exit.
3. In-flight event-handler tasks are awaited (pre-existing behavior from #154).
4. `lifecycle.shutdown()` now calls `app.goals.archive_all()` **before** any other teardown — unlike `dismiss_completed()`/`dismiss_failed()` (which only ever handled terminal goals), this archives every goal regardless of status and clears in-memory state, so a PENDING/ACTIVE/DEFERRED goal that was mid-flight at shutdown has an on-disk record in `goal_archive.jsonl` instead of vanishing. Session/contextor writes need no explicit flush — `ContextorAdapter._send()` is already a synchronous round-trip per call, so `contextor.disconnect()` never races a pending write.

**Explicitly not a security boundary**: the issue text is deliberate about this — it's a cooperative shutdown mechanism for a well-behaved daemon and client, not protection against a compromised one.

### Voice/response session state machine (`jarvis/core/voice_state.py`)

`VoiceState` is the single source of truth for the daemon's voice + response lifecycle:

```
IDLE (wake-word listening)
  → WOKEN       (wake word fired, chime plays)
    → CAPTURING (STT active, subject to the silence timeout above)
      → IDLE       (nothing usable was said -- discarded)
      → PROCESSING (LLM + dispatch running; also entered directly for GUI/CLI/stdin text input)
        → SPEAKING (TTS playing the reply)
          → IDLE
```

Every transition broadcasts over the GUI socket as a structured event: `{"type": "state", "state": "<value>", "meta": {...}}`. `meta` is omitted unless the transition carries extra detail (currently only the CAPTURING → IDLE discard case, with `{"reason": "discard", "detail": "..."}`). `jarvis.runtime.io.set_gui_state(app, state, meta=None)` is the only place a transition should be broadcast from; `state` is a `VoiceState` member for anything in the diagram above, or the plain string `"listening"`/`"idle"` for the separate, orthogonal `start_listening`/`stop_listening` GUI toggle (whether the wake-word listener is enabled at all — not part of this state machine).

**Known limitation:** `OutputManager._output_voice()` still calls TTS synthesis/playback synchronously on the event loop thread (a pre-existing characteristic, not introduced by this state machine). SPEAKING and the following IDLE are broadcast at the logically correct points around that call, but because the call blocks the loop, delivery to socket clients can lag behind the exact moment TTS starts/stops, and queued events (including a second command captured mid-reply) wait until playback ends. Barge-in is the escape hatch: interrupting via the wake word stops playback and unblocks the loop almost immediately. Making TTS playback fully non-blocking is tracked separately.

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
