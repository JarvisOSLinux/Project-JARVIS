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
  jarvis/llm/               — provider-agnostic LLM layer (Ollama local + OpenAI-compatible API providers, ProviderPool failover)
  jarvis/voice/             — STT (Vosk default, optional faster-whisper for utterances) + TTS (Piper)
  jarvis/sessions/          — session persistence
  jarvis/runtime/           — event loop, action handlers
  jarvis/contextor/         — adapter + embeddings for the Rust contextor memory binary
  jarvis/platform/          — OS abstraction (Linux/macOS/Windows: data dirs, IPC hardening + accept-time peer auth, askpass/elevation, sidecar resolution)
  jarvis/server/            — opt-in network surfaces, both off by default: OpenAI-compatible HTTP (`openai_compat.py`) and `jarvis web` phone gateway over Tailscale (`web_gateway.py` + `web/` page)

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

### TLA (Threat Level Access) — 4-tier policy

Behaviour in `smart` mode, per `confirmation_manager.should_confirm`:

| Tier | Behaviour |
|------|-----------|
| SAFE | Runs without prompting |
| ELEVATED | Blocked pending user confirmation |
| DANGEROUS | Blocked pending user confirmation |
| FORBIDDEN | Refused before execution in every mode; never prompted (#250) |

There is no separate audit log. FORBIDDEN is refused in `dispatch_flow.dispatch_send`
ahead of the confirmation gate, so `allow_all` cannot bypass it. Only a manifest
`threat_level: "forbidden"` produces it today; the whole batch is refused and
there is no override. `allow_all` still bypasses every other tier.

### Confirmation Modes

`CONFIRMATION_MODE` in config controls the confirmation gate:

| Mode | Behaviour |
|------|-----------|
| `allow_all` | No prompts, everything auto-approved (bypasses all tiers) |
| `smart` | Ask when the TLA classifier rates the call >= ELEVATED: max(host floor for always-dangerous tools, manifest-declared `threat_level` / legacy `confirmation_required`, dangerous-payload scan of params, privileged-command prefix scan of params — per-OS elevation-implying verbs like `pacman`/`systemctl stop`/`reg delete`, #208, registry-tier floor — ELEVATED unless the server's install-time tier is `official`/`community` AND the tool is manifest-declared, #223) |
| `ask_all` | Confirm every tool call |

### Confirmation Channels

The `ConfirmationManager` (core) is interface-agnostic. It routes confirmation
requests to the best available channel:

1. **TUI modal** — `ConfirmModal` in Textual (Y/N inline)
2. **Desktop notification** — `notify-send` with Allow/Deny actions
3. **Socket** — JSON over output socket for external clients
4. **CLI prompt** — stdin `[y/N]` fallback

Pending confirmations are tracked in a queryable list
(`ConfirmationManager.list_pending()`), reviewable anytime via `jarvis
confirm` (CLI) or `list_confirmations`/`approve_confirmation`/
`deny_confirmation`/`partial_approve_confirmation`/
`approve_all_confirmations` (GUI socket) rather than expiring on a clock.
`CONFIRMATION_TIMEOUT` defaults to `0` (disabled); set it > 0 to restore
auto-deny for unattended/headless setups. The list outlives any single
channel — dismissing a desktop toast is never a deny (#185) — **and it
survives a daemon restart** (#224): the daemon's manager is built with a
store path (`$JARVIS_DATA_DIR/confirmations.json`, wired in
`component_factory`), mirrors `_pending` to it atomically on every mutation,
and reloads it at construction. Restored entries keep their `created_at` (so
the CLI shows true age) and `session_id`, fire no notification on any channel,
and arm no timeout task — they are a review queue for `jarvis confirm` / the GUI
socket, resolvable through the same `resolve()` path as a live request. A
missing, unreadable, or corrupt store logs a warning and starts empty rather
than blocking startup; that fails closed, since the gated tasks were never
dispatched. The list is still not partitioned by session (each entry records
a `session_id` for display and for re-scoping the resume dispatch). Because
goals are archived at shutdown (#146), a confirmation approved after a
restart dispatches with its goal gone: goal-linking logs and no-ops, the
tasks still run. A `ConfirmationManager()` built with no store path is
memory-only and performs no disk I/O at all (the test suite's default).

**`jarvis confirm` also works while the daemon is off.** It prefers the input
socket; when the endpoint is absent or the connection is refused it reads the
same store file directly (rendered by the same listing code, plus a `daemon
offline — decisions queue and apply at next start` line and any decisions
already queued). `approve`/`deny`/`approve <id> 0,2`/`approve-all` then append
the exact socket-protocol message a live client would have sent to
`$JARVIS_DATA_DIR/confirmation_decisions.json` (atomic write, one decision per
id, last writer wins); an id absent from the store is refused rather than
queued. At startup `lifecycle.apply_queued_confirmation_decisions()` — wired
right after `set_event_injector`, before and regardless of any socket — replays
each message through `io.apply_confirmation_decision()`, the same translation a
live client's message gets, so per-task indices, approve-all expansion, the GUI
broadcast and the goal-gone resume path behave identically. Unknown or
already-resolved ids warn-skip inside `resolve()`; the queue file is deleted
only after the whole batch is injected (re-applying is a harmless warn-skip, so
a crash mid-apply is safe), and a corrupt queue is preserved as
`confirmation_decisions.json.bad` instead of being dropped. **Security:** the
queue file's protection is the same same-user filesystem boundary the local
socket model already relies on — the daemon trusts a queued decision exactly as
far as it trusts a local same-user socket client, no further. On Windows the
file's inherited ACL is the analogue, best-effort like the socket hardening
there.

### Socket Security

`socket_security.py` hardens the three IPC endpoints under `JARVIS_DATA_DIR`
(platform data dir, e.g. `~/.jarvis/`): `input.sock`, `output.sock`, and
`jarvis.sock` (GUI) — overridable via `JARVIS_INPUT_SOCKET` /
`JARVIS_OUTPUT_SOCKET` / `JARVIS_GUI_SOCKET`:
- `harden_socket_path()` — platform-delegated (0600 on Linux/macOS, localhost TCP on Windows)
- pre-connect ownership check — clients call `platform.ipc_verify_owner()`
  directly (`cli.py`); the `verify_socket_ownership()` wrapper delegates to
  the same check but has **no callers** — cite the platform call, not the wrapper
- `warn_if_allow_all()` — logs warning when confirmation is disabled

### Output-Provenance Boundary (Prompt Injection)

dispatch wraps every tool output in a per-task 128-bit CSPRNG-nonce-keyed tag
(`[hash=<H>] <<H>>...</<H>>`); the daemon verifies EXIT bodies via
`jarvis/dispatch/boundary.py:verify_boundary()` and treats missing or
mismatched wrappers as untrusted (Threat #2 mitigation, #165).

### Sudo Management

`jarvis sudo enable/disable` (`jarvis/core/sudo_manager.py`, #158) manages a
password-required, visudo-validated `/etc/sudoers.d/jarvis` drop-in (atomic
install) — Linux only; macOS/Windows report "not granted" rather than managing
sudoers.

Shell servers are installed from the MCP registry, not bundled here.

**Elevation is decided by dmcp scope, not by this daemon.** A system-scope
server is spawned under pkexec/polkit and already runs as root — which is why
the ROOT prompt rule says never to prefix `sudo`. The password prompt is
polkit's, raised by dmcp's re-exec, and it is deliberately outside this
daemon's reach (a flag JARVIS could write would be a self-escalation path).

`BasePlatform` still carries a `sudo -A` askpass surface from the bundled
shell server that was retired in `357aeca` — `find_askpass()`,
`askpass_helpers()`, `elevate()`, `grant_privilege()`/`revoke_privilege()`/
`is_privilege_granted()`, `open_command()`. **None of these has a caller
outside `jarvis/platform/` today** (verified by grep, including tests). They
are retained per-OS scaffolding, not a live mechanism; do not describe them as
the boundary.

`privileged_prefixes()` is the one method of that surface with a live caller:
`threat_level.py`'s fifth `classify()` input (#208) folds it in as a
raise-only DANGEROUS floor — a command-ish parameter naming an
elevation-implying subsystem verb (`pacman`, `systemctl stop`, `reg delete`,
…) raises even when the tool name is unknown to the host floor and the string
trips no payload-scan signature. It no longer has anything to do with
elevation *execution* (that's dmcp scope, above); it is pure TLA
classification signal now.

The IPC half of the platform layer *is* live: `ipc_verify_peer()` (accept-time
peer credential check, `runtime/io.py`) and `system_ipc_candidates()`
(`cli.py`). `resolve_sidecar()` is exercised only by tests today, but is the
mechanism a packaged install needs — keep it until packaging lands.

---

## Packaging

### Install Extras

| Extra | What it adds |
|-------|-------------|
| `project-jarvis` | Core daemon (text I/O, LLM, dispatch) |
| `project-jarvis[tui]` | Textual TUI (`jarvis tui`) |
| `project-jarvis[voice-input]` | Vosk STT only |
| `project-jarvis[voice-output]` | Piper TTS only |
| `project-jarvis[voice]` | Vosk STT + Piper TTS |
| `project-jarvis[voice-whisper]` | faster-whisper STT for utterances (opt-in, includes voice-input; not in `voice` or `all`) |
| `project-jarvis[voice-aec]` | Voice + acoustic echo cancellation (native-compiled, barge-in fix) |
| `project-jarvis[dev]` | pytest, black, isort, flake8, mypy, pre-commit |
| `project-jarvis[docs]` | Documentation tools (sphinx) |
| `project-jarvis[all]` | tui + voice-aec + dev + docs |

### Rust Dependencies

Submodules under `deps/rust/` — dispatch, dmcp, contextor. Build with
`cargo build --release` in each directory. Binaries must be on PATH.

## LLM Providers

Shipped (#78): `ProviderPool` (`jarvis/llm/provider_pool.py`) is built from
`providers.json` at startup (`ComponentFactory._build_provider_pool()`), walks
providers in priority order, and fails over with per-error cooldowns
(429 → 60 s, 402 → 1 h, 401/403 → permanent, 5xx/timeout → 30 s) plus
automatic restore when a cooldown expires.

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
| `jarvis/tui/chat_view.py` | The chat as lines and folding blocks (#271): commands with their output, JARVIS's actions; browse mode |
| `jarvis/tui/lifecycle.py` | App startup/shutdown, callback registration |
| `jarvis/tui/confirm_modal.py` | Tool confirmation modal |
| `jarvis/tui/setup_form.py` | The setup form (#254), shared by `jarvis setups fill` (inline) and the TUI (modal) |
| `jarvis/tui/setups_view.py` | TUI list of pending setups (F3 / `/setups`) |
| `jarvis/tui/tasks_view.py` | What JARVIS is doing now (F4 / `/tasks`): requests, running tasks with output, timers (#267) |
| `jarvis/tui/config_modal.py` | Tabbed config/provider settings modal (F2 / /settings) |
| `jarvis/tui/provider_modal.py` | Provider add/edit modal |
| `jarvis/core/confirmation_manager.py` | Multi-channel confirmation gate; pending list persisted to `$JARVIS_DATA_DIR/confirmations.json` when built with a store path (#224) |
| `jarvis/core/setup_queue.py` | Pending server setups (#254): `setups.json` (daemon-owned, survives restarts) + the offline completion queue; stores what a server needs, never values |
| `jarvis/core/constraint_store.py` | Standing path-prefix deny constraints (#214), enforced in `dispatch_flow` ahead of the confirmation mode |
| `jarvis/core/socket_security.py` | Socket hardening |
| `jarvis/server/web_gateway.py` | `jarvis web` (#266): loopback WebSocket gateway, Tailscale identity + role allowlist, audit log, relay to `jarvis.sock`; page in `jarvis/server/web/` is jarvis-app's UI over `transport.js`; see `docs/web-gateway.md` |
| `jarvis/core/sudo_manager.py` | `jarvis sudo` — sudoers drop-in management (#158) |
| `jarvis/dispatch/boundary.py` | Output-provenance boundary verification (#165) |
| `jarvis/core/command_parser.py` | LLM response parser + action registry |
| `jarvis/core/voice_state.py` | `VoiceState` — formal voice/response session state machine |
| `jarvis/voice/stt/__init__.py` | `create_stt()` — `STT_PROVIDER` → provider class, with the one logged Vosk fallback (#138) |
| `jarvis/voice/stt/whisper_stt.py` | `WhisperSTT` — faster-whisper utterance transcription (lazy import, `is_available()`/`ensure_model()` never raise) |
| `jarvis/voice/chime.py` | Wake-word earcon: path validation + best-effort playback |
| `jarvis/voice/audio.py` | Audio device detection + `passes_noise_gate` RMS filter |
| `jarvis/voice/aec/webrtc_aec.py` | `WebRtcAEC` — acoustic echo cancellation (barge-in fix, #143) |
| `jarvis/runtime/io.py` | Input/GUI/output sockets — confirmation queries, session CRUD, settings + provider CRUD, client labeling/`list_clients`/`shutdown_request`, status/work requests and activity/action pushes; the protocol is `docs/gui-protocol.md` |
| `jarvis/runtime/lifecycle.py` | Signal handlers, startup service wiring, `shutdown_request`'s safe save-then-exit sequence |
| `jarvis/dispatch/goal_manager.py` | `GoalManager` — goal tree + on-disk archive (`archive_all()` for shutdown) |
| `jarvis/core/providers.py` | Provider pool CRUD (`providers.json`), shared by CLI, TUI, and the GUI socket |
| `jarvis/dispatch/adapter.py` | Python wrapper for Rust dispatch binary |
| `jarvis/dispatch/discovery.py` | Embedding search via dmcp vector index |
| `jarvis/dispatch/dmcp_registry.py` | dmcp CLI wrappers (install, tools, config) |
| `jarvis/dispatch/event_merger.py` | Merges voice/CLI/socket/dispatch events |
| `jarvis/runtime/root_actions.py` | ROOT-mode LLM response action handlers |
| `jarvis/runtime/action_events.py` | Start/update/end events for what JARVIS does (search, docs, install, tool runs, confirmations), for front ends (#271) |
| `jarvis/runtime/root_context.py` | Context assembly for ROOT-mode prompts |
| `jarvis/runtime/work.py` | `collect_work()`: requests, running tasks and timers, what waits on the user, as plain data (#267) |
| `jarvis/runtime/health.py` | `/status` health check (#272): sections of checks as plain data, fast by default, `full` adds registry updates |
| `jarvis/runtime/llm_bridge.py` | `ask_llm()` — the sole locked/mode-atomic LLM call site |
| `jarvis/runtime/events.py` | Event routing + per-event task tracking (concurrent goals) |

---

## Changelog — corrected claims

*2026-10-23:* helper processes no longer write to the terminal, and failed shell results read as printed. `dispatch serve` was started with the MCP SDK's default `errlog`, the terminal's stderr, so in the TUI its warnings ("WARN MCP task failed pid=2 …") were drawn over the screen until Textual repainted (Yakup's "something was wrong with the screen for a moment"); contextor's stderr was a pipe nobody read, which blocks it once full. `JarvisLogger.child_stderr(name)` gives each its own `dispatch.log` / `contextor.log` beside the JARVIS log (owner-only, rotated once at 5 MB); with file logging off it is the terminal, except in the TUI (`apply_tui_root_mitigation` marks the screen as owned), where it is discarded. A failed call's output, `Error: dmcp invocation failed: {json}`, now reads as its stdout, stderr and exit code like a successful one, and an `execute_script` block shows the script as typed with its timeout. Found when pacman, run without `--noconfirm`, asked "Do you want to continue? [Y/n]" with no one to answer; the model retried with `--noconfirm` on its own.

*2026-10-23:* activity and action events are opt-in per GUI client. #286 pushed them to every client, and jarvis-app turns any message type it does not know into `window.alert("JARVIS error: unknown type …")`, so every step JARVIS took would have opened an alert there. A client now gets them only when its `hello` carries `"subscribe": ["activity", "action"]` (`io.SUBSCRIBABLE`; the subscriptions are dropped on disconnect, and `broadcast_to_gui_clients` takes an optional writer list). `jarvis web` subscribes for the phone. Rule for later additions: a new pushed type is opt-in.

*2026-10-23:* a pass over the TUI's keys (#267, phase 1). F1-F4 opened their window but a second press did nothing: they are priority bindings on the app, so the window's own binding for the same key (help's F1) never saw it; each now closes what it opened (`_close_if_showing`). Ctrl+I, in the footer as "Input", arrives as Tab in most terminals; it is hidden, and Esc goes back to the input from the sidebar or the chat, where it had left nothing focused. C in the setups list (F3) cancelled at once, telling the waiting request to give up; it now asks for a second C, like Ctrl+D for sessions. Esc in the settings window threw away unsaved edits silently; the first Esc now names them ("Not saved: … Ctrl+S saves; Esc again discards."), and `_save_settings` and the check share `_edited()`. `docs/tui-overview.md` lists F3, F4, browse mode and the new commands.

*2026-10-23:* the phone page shows what JARVIS is doing (#267, #271). `jarvis/server/web/` gets the activity line above the input (every role), comments as a dashed bubble apart from answers, and, for trusted and owner (the only roles the gateway relays them to), action blocks as `<details>` that fold like the TUI's (once something newer arrives, unless the user opened or closed one) and a "What JARVIS is doing" panel (⏱) that polls `list_work` every 2 s while open, with a Status button for `get_status`. Everything from the daemon is set with `textContent`. Both side panels got a ✕ (at phone width they cover the screen and had no way out) and Esc closes them; a guest no longer tries `switch_session` on connect, which the gateway refused with an error banner. `scripts/stub_daemon.py` plays a scripted turn on "run" (comment, activity, a search and a tool block with live output, the answer) and answers `get_status`/`list_work`, so `scripts/run-web.sh --stub` shows it all without a model. Checked in the browser at 375x812 as owner and as guest.

*2026-10-23:* the TUI's new controls reach socket clients (#267, phase 2), and the protocol is written down in `docs/gui-protocol.md`. Activity and action events are pushed to every GUI client (`io.on_gui_activity`, `io.on_gui_action`, wired in `lifecycle` beside `_on_gui_output`); `get_status` answers with the `/status` sections as data (`full` adds the registry check), `list_work` with what F4 shows, `move_provider` reorders like `/providers move`. A `response` now says `comment: true` or `command: true`, and a comment no longer sets the state to `idle`: JARVIS is still working after it, and every client showed it as done. `jarvis web` relays `activity` to every role and `action`, `status` and `work` only to trusted and owner, since action blocks carry tool parameters and raw output; `relayable()` takes the role, and the relay follows the role the allowlist gives at the phone's latest message. Stopping is still #270.

*2026-10-23:* the TUI shows what JARVIS is doing now (#267, phase 1): F4 or `/tasks`. Nothing did: Ctrl+Q listed running work only when quitting. `jarvis/runtime/work.py` (`collect_work`) returns plain data, for socket clients next: the active requests and how long ago they came in, how many turns wait on the model, each running task from dispatch's status with the title, parameters and start time its action block recorded (a task JARVIS did not start still shows, without a duration), the request it belongs to and its latest output (dispatch's provenance-wrapped tail, unwrapped for display), timers with when they fire, and the confirmations and setups waiting. `jarvis/tui/tasks_view.py` refreshes it every second. It has no stop buttons: what stopping a request or a task does is #270, parked for Yakup and Toufic to specify; Ctrl+Q still stops everything when quitting. The activity line points to F4 while tasks run.

*2026-10-23:* the TUI chat is messages and folding blocks (#271). It was one append-only `RichLog`, so a long output pushed the conversation out of sight, and what JARVIS ran showed only as passing "... Dispatch signal (pid 7)" lines. Every action now starts and ends as an event (`jarvis/runtime/action_events.py`, delivered by `OutputManager.emit_action`, never spoken): a search with what it found, reading a server's tools, an install, update, uninstall or configure with its outcome (`waiting` when a setup form is pending), each dispatched tool with its parameters (a shell command as typed), its live output from REMIND and its output and status from EXIT/KILL, and a confirmation with what was decided. Tool output is unwrapped from the provenance boundary for the reader; output that failed verification keeps its warning. Only an action that started can end, so a timer's EXIT or a confirmation restored after a restart draws nothing. `jarvis/tui/chat_view.py` draws them: a typed slash command is a block that holds its output (`session_reply` marks engine replies `command: True`), a block folds to one line once something newer arrives unless you folded or opened it yourself, and a long one scrolls inside 16 lines. Ctrl+L or `/commands` browses (Up/Down, Enter/Space, Esc back to typing; a click on a title folds too); `/commands collapse|expand` does them all. The activity narrative moved from the chat to one line above the input, which says how many tasks are still running once JARVIS has answered. Socket clients do not get the action events yet (#267 phase 2).

*2026-10-23:* input that arrives before the engine starts is kept. The TUI says "Ready." while the engine is still syncing its tool index (7 s on the reference machine), and `EventMerger` dropped anything injected before `start()` with only a log line, so a first message typed quickly was ignored. User input, confirmation decisions and setup results are now held (up to the queue size) and queued at `start()`, in order. Found by driving the real TUI and engine headless.

*2026-10-22:* a finished task's result reaches ROOT once, and JARVIS logs to a file by default. On the reference machine "run the updates" got the same summary twice, 16 s apart. The long pacman task had fired a reminder; when `EventMerger._listen_signals` saw that REMIND for the first time beside the task's EXIT, it queued a merged REMIND+EXIT event but left the EXIT unseen, so the same snapshot queued the EXIT again as its own event: two ROOT turns, one result. The merged EXIT is now marked seen, and a reminder whose EXIT was already delivered is dropped. `tests/test_one_turn_per_result.py` drives the real poll loop; 4 of its 5 cases fail on the old code. The duplicate could not be confirmed from a log because `LOG_FILE` defaulted to empty (jarvis.log had not changed in two months): it now defaults to `$JARVIS_DATA_DIR/jarvis.log`, rotated at 5 MB with 3 backups, every file 0600 (`_PrivateRotatingFileHandler`); `LOG_FILE=""` or `off` turns it off. Secrets were already scrubbed before any handler (#242).

*2026-10-22:* `/context` breaks the last prompt down by part (#272). It showed prompt, reply and total tokens. Every ROOT call now records `LLM.last_breakdown` (`jarvis/llm/context_breakdown.py`): characters for the instructions and the rolling conversation summary (split at the summary marker in the system prompt), the exchanges kept in full, and this turn's labelled blocks (tool docs, search results, memories, goals, task results, the user's message, other). Only known labels start a block, so a tool's own upper-case output line stays inside its block. `/context` scales the parts to the provider's reported prompt tokens (or estimates at four characters a token, and says so), shows each part's share of `LLM_CONTEXT_WINDOW` and the free space when that is set, and says how many exchanges stay in full before older ones are folded into the summary. On the reference prompt the instructions alone are about 6,700 tokens.

*2026-10-22:* `/status` is a health check (#272). It showed provider, model and session. `jarvis/runtime/health.py` collects sections of checks (state ok/warn/fail/off and a one-line reason) as plain data, so socket clients can get the same dicts later (#267): each LLM provider (in use, ready, cooling down with time left and why, quota used up, key rejected), dispatch, dmcp (where it is, servers installed), memory, search (semantic, or keyword with the reason), voice (output mode, text-to-speech, listening, spoken comments), safety (confirmation mode, passwordless sudo), work (active requests, running tasks and timers, confirmations and setups waiting) and the session (context share when `LLM_CONTEXT_WINDOW` is set). Blocking checks run off the event loop with short timeouts; on the reference machine the whole check took 0.1 s. `/status full` adds the one check that goes online, registry updates (0.6 s). Sign-ins are left out: they belong to servers, not JARVIS (Yakup's decision). The voice checks tell a missing `sounddevice`/STT package apart from a missing device: on the reference machine `OUTPUT_MODE=voice` while `sounddevice` was not installed, so replies were silently shown as text; status now says so and names the extra to install. Dispatch reports no task start time, so running tasks show no duration.

*2026-10-22:* `jarvis web`, phone access over Tailscale (#266, host side). A separate opt-in process (`JARVIS_WEB_ENABLED`) bound to `127.0.0.1`, reached through `tailscale serve`, never Funnel. Identity is the `Tailscale-User-Login` header checked against `web_allowlist.json` (guest/trusted/owner) on every message; `Host` must end in `.ts.net` and `Origin` must equal `Host`. Each role has a narrow send list (no settings, providers, setups, shutdown, `approve_all_confirmations`), the daemon's provider/settings/setup broadcasts are never forwarded, a session is refused or ended while the host is in `allow_all`, and state-changing requests go to `web_audit.jsonl`. The page is jarvis-app's chat, sessions drawer and permission panel behind a `transport.js` connection switch, with a phone layout and an always-visible stop button; the stop button only ends the stream display (`stop_stream`), real stop and activity events are #267 phase 2. Tested against a fake daemon socket (`tests/test_web_gateway.py`); not yet run against a real tailnet, so V1-V6 in `docs/web-gateway.md` are open, and the claim that `tailscale serve` strips a forged identity header is unverified.

*2026-10-22:* JARVIS says what it is about to do (#273). Asked to do something, it went silent until it had an answer. Any ROOT action except `respond` may carry an optional `comment`, one short sentence of intent; `root_actions._say_comment` sends it through `output_manager.handle_response` like a reply (shown in every front end, saved to the session, spoken in voice mode) before the action runs, clipped to 300 characters on one line. `VOICE_SPEAK_COMMENTS` (Settings → Voice, on by default) silences only the speaking; the chat still shows it. Both ROOT prompts ask for it and forbid stating a result before a signal does. Probed on the reference model (`nemotron-3-ultra:cloud`) before shipping: the first step of a request carried a comment in 3 of 3 runs ("Let me find a calendar tool to check your schedule for this week."), later steps only sometimes, greetings never, and every reply stayed valid JSON.

*2026-10-22:* TUI feedback round 1 (#269). `/switch` changed the session inside JARVIS but only picking a session in the sidebar reloaded the chat, so the screen showed one session while JARVIS was in another; `SessionManager.on_switch` now announces every move (`switch`, `new_session`, deleting the current session; not the implicit session a first message starts, whose conversation simply goes on), and the TUI reloads the chat in one place (`session_sidebar.show_session`). Sessions are no longer auto-named after a slash command (the reply to `/switch 110` titled the session it switched to "/switch 110"). `/rename [<id>] <title>` renames the session whose id starts with `<id>` when exactly one does, else the current one, and says what to do when there is none. Every typed line is echoed, TUI-only commands included. Suggestions carry each command's arguments (`/rename [<id>] <title>`), and Tab completion adds a space while still cycling. The settings window draws full-width section dividers and gives its scrolling list only the space left, so the buttons are never clipped. Unknown slash commands keep going to the model, as Claude Code does (Yakup's decision).

*2026-10-18:* FORBIDDEN is an unconditional block (#250, mcp-registry#88). `dispatch_send` classifies every task before sending any and refuses the batch if one is FORBIDDEN, in `smart`, `ask_all` and `allow_all` alike, with a structured error telling the model not to retry or route around it. Placed beside the constraint check because `should_confirm`, which `allow_all` skips, is the wrong layer. Host floor, payload scan, privileged-command scan and tier floor top out at DANGEROUS, so only a manifest declaration changes behavior. Confirmations persisted before this change are not re-checked on resume.

*2026-10-18:* the setup form draws typed settings (mcp-registry#115). A setting declaring `type` gets a switch (`boolean`, stored in its own spelling from `values`, e.g. `1`/`0`), a select (`choice`), a checklist (`choice` with `multiple`, joined by `separator` in the order of `choices`), or a whole-number box (`integer`, with `min`/`max`); untyped settings and every secret stay text boxes. `setup_queue.value_error` mirrors the registry validator and dmcp's `config set` check: the form shows `Fix: Port must be at most 65535` and holds the review, and the plain prompts say on/off, list the choices and ask again. An unset switch reads as off and a value spelled another way (`True`) is read as what it means, so an untouched switch writes nothing. `configure_server` stores a model proposal in the setting's own spelling (`as_setting_value`: "yes" becomes `1` for a 1/0 switch) and leaves one its type refuses for the user, telling the model why. Verified in a pseudo-terminal: `jarvis setups edit microsoft` with Space on the read-only switch writes exactly `MS365_READ_ONLY=true`.

*2026-10-18:* `jarvis setups edit <server>` opens the setup form for an installed server (#254, phase 3). The server is found through `dmcp list --json` by id or part of its id or name; the form shows every declared setting with its current value from the installed manifest, secrets only as already set, and saves only what changed, through `dmcp config set --stdin`, mirroring plain values to ParamsStore. A server still waiting for setup opens that instead, so its goal resumes. Verified in a pseudo-terminal against a sandbox: changing one setting writes exactly that one, and the stored password never reaches the screen.

*2026-10-18:* `configure_server` no longer writes settings; the user reviews them in the setup form (#254, phase 3; decided by Yakup). The model names settings, and JARVIS queues them as a pending setup of kind `configure` with only those fields, the same queue an install uses: the TUI opens the form at once, elsewhere `jarvis setups fill <n>`, and the model is told `CONFIGURE_PENDING` and never asks for values in chat. A non-secret value the model proposes prefills its field marked "suggested by JARVIS" and is saved unless changed back (the form now compares with what the server runs with, its current value or default, not with the prefill); a placeholder is no proposal; a secret from the model is dropped by the handler and again by `SetupQueue.add`. Values already set need no form. Saving resumes the goal with `CONFIGURE_RESULT` listing plain values and secrets only as set; closing the form gives `CONFIGURE_CANCELLED`. A request for a server with an entry already pending joins it (one form, the id the user was given still works, the goal already waiting resumes), where phase 1 replaced it. Removed with the blocking path: `_collect_config_securely`, `config_modal_callback`, the TUI's `_open_config_modal`, the Tk dialog (`jarvis/ui/`), and the dead `_run_dispatch_subchain_legacy` with the three helpers only it used.

*2026-10-17:* one setup form for the CLI and the TUI (#254, phase 2). `jarvis/tui/setup_form.py` holds `SetupForm`, a Textual widget with every setting of one server on screen: Tab, Shift+Tab and the arrow keys move between fields; current values and defaults are filled in; secrets are masked (Ctrl+R shows one); a review step lists what will be saved, secrets as dots, before anything is returned. `jarvis setups fill` runs it as an inline app under the prompt (Textual itself falls back to full-screen on Windows) and keeps the plain prompts when Textual is missing or input or output is not a terminal. The TUI shows the same widget in `SetupFormScreen`, which replaces `ServerConfigModal`, and lists pending setups in `setups_view.py` (F3 or `/setups`; Enter fills one in, C cancels it), with a count in the status bar fed by `SetupQueue.on_change`. `setup_queue.form_spec` builds what the form shows, for both front ends and the plain prompts: dmcp's current config wins over the values mirrored in ParamsStore, and a secret is never prefilled, only marked as set. The form returns each changed field and each required field that was unset, never an untouched default (the server applies its own; writing one would pin it). It writes nothing itself, and the old modal's habit of saving keystrokes to the params file is gone with it. Verified in a real pseudo-terminal against a sandbox and a stand-in dmcp: the form draws inline, the password never reaches the screen, values reach dmcp over stdin, and the params file gets only the non-secret ones.

*2026-10-17:* `configure_server` sets only the settings a server declares. It passed any key the model named to `dmcp config set`, and dmcp passes every stored setting to the server as an environment variable of that name, so a prompt injection could set `NODE_OPTIONS` (code execution in any Node server) with no confirmation prompt; two installed servers on the reference machine already carried settings the model had made up. An undeclared key is now dropped, and the model is told it is not a setting and which ones are. Keys are normalized the way the adapter writes them (`--api-key` → `API_KEY`) before anything is classified: checked under the raw name, a secret given as a flag was treated as an unknown plain key and stored from the model's value, bypassing the #242 rule. With no readable manifest nothing is set (`CONFIGURE_ERROR`). dmcp enforces the same rule on its side (dmcp#77).

*2026-10-17:* after a server's sign-in succeeds, the model is shown the server's real tools. A wrapper for a hosted server (Notion) can list only login/status/logout before the sign-in, since the hosted server refuses to list without a token; the model learned that list at install and, once signed in, told the user the server "only provides authentication tools". `dispatch_flow.note_dispatched_tools` records each PID's server and tool on the app (on the direct path and on the confirmation-resume path in `on_confirmation_response`, since `login` is elevated and always confirmed), and `root_handlers._tools_after_login` turns an EXIT marked `200` of the tool the manifest names as `login` into `TOOLS_AFTER_SIGN_IN` with fresh SERVER_DOCS in that turn's context. A failed sign-in, another tool, or an EXIT without an explicit `200` adds nothing.

*2026-10-15:* installing a server no longer blocks on a config form (#254, phase 1). A required setting that is unset becomes a *pending setup* in `core/setup_queue.py`, stored in `$JARVIS_DATA_DIR/setups.json` like the confirmation queue; the model is told `SETUP_PENDING` and never asks for the values in chat. A server with only optional settings installs straight away on its defaults. `jarvis setups` lists, `jarvis setups fill <id|server>` prompts in the terminal (secrets hidden, masked in the review) and writes each value with `dmcp config set --stdin`, then sends `setup_completed` (id only) over the socket; `root_actions.on_setup_result` runs the setup script and resumes with `SETUP_COMPLETED` + `INSTALL_RESULT` and the goal's original request. With the daemon off, the CLI runs `dmcp setup` itself and queues the completion in `setup_completions.json`, applied at startup. In the TUI the existing form opens for a new pending setup without blocking the engine; closing it leaves the setup pending. `set_server_config` now sends values over stdin too: as an argument a secret was readable from the process list and was logged by `run_dmcp` on failure (needs dmcp's `config set --stdin`, dmcp#76). `install` no longer uses `_collect_config_securely`; `configure_server` still does (phase 3). Verified live: install CalDAV through `jarvis chat`, fill it from a second terminal, the daemon runs setup and the model resumes; the password is in dmcp's config only.

*2026-10-14:* the `sign_in` action is gone (#229, design v3). The 2026-09-29 design had dmcp hold the accounts and JARVIS run `dmcp login`; v3 moves sign-in into each server instead. A server that works in the user's account ships a `login` tool, through a per-server wrapper in mcp-registry when the upstream has none. The model calls `login` like any other tool. Its manifest marks it `blocking`, so dispatch arms a reminder by itself, and the sign-in link the wrapper prints to stderr reaches the model in REMIND. The wrapper keeps the tokens; no tool result carries them. This reverts #244: `dispatch/sign_in.py`, the SIGN_IN_NEEDED hint (`_failure_hints` is `_config_hint_for_signals` again), the install filter on account-filled keys, and `tests/test_sign_in.py`. The secret scrubbing and the secure config form from #242 stay.

*2026-09-28:* secrets no longer travel through the conversation (#242). Outside the TUI, the designed way to configure a server's API key was for the user to type it into the chat: the `configure_server` prompt told the model to ask, and the placeholder guard sent it back to ask again. The message was then logged at INFO, added to the goal tree (archived to `goal_archive.jsonl`), stored raw by `contextor.auto_store_prompt` — searched on every later prompt — sent to the model, echoed in its `configure_server` output, and written to `jarvis_params.toml` as a second plaintext copy. On the reference machine a GitHub token was in four files and an API key in the goal archive, after sessions had been deleted repeatedly. Now: `core/secret_scrubber.py` redacts well-known credential formats at the input funnel (`on_user_input`, `sync_ask`) before anything is stored or sent, and as a filter on every JARVIS logger and the file handler, which also covers `runtime/io.py` logging the first 80 characters of socket input (enough for a whole GitHub token) before the funnel runs. `configure_server` never takes a `sensitive` value from the model, even a real-looking one; naming the key opens `_collect_config_securely` in `root_actions.py` — the TUI modal, otherwise the Tk dialog in `ui/config_prompt.py`, which masked secrets already but was reachable only from the dead `_run_dispatch_subchain_legacy`. Install outside the TUI now collects config through the same form instead of skipping it. SERVER_DOCS and CONFIG_HINT share `configure_call_hint`, which shows secret keys empty. `ParamsStore` stores no sensitive value, and its TOML headers are now quoted: unquoted, TOML read every reverse-DNS server id as nested tables, `_load` stringified them, and the next save wrote invalid TOML, so the store had never round-tripped a single server. Also fixed: `on_user_input` called `output_manager.display`, which does not exist, so with no model configured every message raised instead of telling the user — a test asserted that `display` was called, passing against a bare `Mock`. Tests touching the output manager now spec it.

*2026-09-21:* `privileged_prefixes()` is folded into `classify()` as a fifth, raise-only floor (#208 resolution). Previously it and the rest of the retired-shellmcp elevation surface (`find_askpass`/`elevate`/`grant_privilege`/etc.) had zero callers outside `jarvis/platform/`; the design question #208 left open — fold the per-OS prefix table into TLA classification, or retire it as superseded by dmcp scope-based elevation — was decided in favor of folding, since it catches command-ish parameters under unknown tool names that evade both the host-name floor and the (deliberately narrow) payload-scan regexes. Same DANGEROUS severity as the payload scan. `resolve_sidecar()`/`sidecar_search_dirs()` stay as documented below (kept, not dropped) — that box was already resolved when this section's "keep it until packaging lands" line landed in `a8ffcf1`.

*2026-09-01:* `jarvis confirm` is no longer socket-only (#224 follow-up). The Confirmation Channels paragraph described a review queue that survives a restart, but the only way to review it exited 1 whenever `_find_ipc_endpoint()` found nothing or `ipc_connect` was refused — so the queue was unreachable in exactly the window it was built for, between restarts. `_cmd_confirm` now falls back to the store file for listing (the renderer was extracted to `_print_confirmation_list` so live and offline output is the same code, not a lookalike) and appends decisions to `$JARVIS_DATA_DIR/confirmation_decisions.json`. `_find_ipc_endpoint(quiet=True)` suppresses only the not-found message: a failed ownership check still prints its error loudly before the fallback runs, because a socket that is not ours is a security signal rather than a stopped daemon — but the review itself continues offline, since the queue is a same-user file our own daemon will read. The four decision branches of `io._handle_confirmation_query` were extracted to `io.apply_confirmation_decision()`, which the startup replay calls, so there is one translation from wire protocol to `CONFIRMATION_RESPONSE` events rather than a second copy that can drift. Verified red-then-green in `tests/test_confirmation_offline_queue.py` (20 tests): offline list from the store on an absent endpoint and on a refused connect, each protocol shape queued exactly, replacement per id, unknown id refused without writing, a reachable daemon writing nothing at all, startup replay resuming through `root_handlers.on_confirmation_response` with the queue file cleared and the summary logged, a stale id warn-skipping while the file still clears, and a corrupt queue quarantined as `.bad` without breaking startup.

*2026-09-01:* the pending-confirmations list is no longer memory-only (#224). The Confirmation Channels paragraph above said it "lives only in the daemon's memory ... does NOT survive a daemon restart ... Tracked as a gap, not a design decision" — the gap is now closed. `ConfirmationManager.__init__` takes an optional `store_path`; the daemon's instance gets `$JARVIS_DATA_DIR/confirmations.json` from `component_factory.create_confirmation_manager()`, and `_pending` is mirrored there (atomic tmp + `os.replace`, modelled on `constraint_store.py`) after the request that adds an entry and after the `resolve()` that pops it — the only two mutation points, so every channel and the auto-deny timer flow through them. Restore happens in the constructor: entries come back with `created_at`/`session_id` intact, fire no notification, and arm no timeout task regardless of `CONFIRMATION_TIMEOUT` (a clock started before the restart would fire on an arbitrary remainder), so a restored item is a review-queue entry for `jarvis confirm`/GUI. A corrupt or unreadable store warns and starts empty. Approving a restored entry resumes through `root_handlers.on_confirmation_response` as before, except the owning goal was archived at shutdown (#146) — `link_tasks`/`link_dispatch_fingerprint`/`link_dispatch_servers` were already no-ops for a missing goal; the resume path now logs that once instead of leaving "dispatched unlinked" invisible. A bare `ConfirmationManager()` is unchanged and writes nothing, which is what every existing test uses.

*2026-08-15:* socket-security bullet corrected — `verify_socket_ownership()` has no callers; the live pre-connect ownership check is `platform.ipc_verify_owner()` called directly from `cli.py`. Key Files gains `jarvis/core/constraint_store.py` (#214), which was shipped but undocumented here.

*2026-08-17:* the registry trust tier scales the TLA gate (#223). `get_tool_metadata` stamps `registry_tier` (install-time `trustStatus` from the local manifest; `unknown` when absent/unreadable/legacy) and `registry_declared` unconditionally — server-scope provenance the server's own output can never supply — and `classify()` floors a tool at ELEVATED unless the tier is `official`/`community` AND the tool is declared. The 2026-08-07 merge below is no longer purely additive: the manifest now OVERWRITES a runtime-carried `threat_level`/`confirmation_required`, because `tools/list` cannot legitimately carry them and a runtime value is injection. An unreadable manifest now floors at ELEVATED (was: fall back to host floor + payload scan). dmcp normalizes the recorded tier at install and stamps `unknown` on connect/URL installs (attacker-supplied `trustStatus` no longer persists).

*2026-08-07:* the TLA gate now sees a manifest's per-tool `threat_level` at runtime. `get_tool_metadata` (`jarvis/runtime/dispatch_flow.py`) fed the confirmation gate only the live MCP `tools/list` view (name/description/inputSchema) from `list_server_tools`; `threat_level` and the legacy `confirmation_required` are REGISTRY-manifest fields absent from that protocol output, so `threat_level._declared()` always read SAFE and **every manifest declaration was inert** — the registry audit's 215/215 threat levels were decorative, and a dangerous tool whose bare NAME is not in the host floor (blender's `execute_blender_code`, the whole filesystem server) escaped confirmation in `smart` mode. `get_tool_metadata` now merges the manifest's per-tool declaration onto the runtime metadata, sourced from the **local** `get_server_manifest` (no network) and cached per server_id (`_THREAT_DECL_BY_SERVER`, modelled on `_STATEFUL_BY_SERVER`). The merge is additive — the runtime tool dict stays, a declaration never clobbers a field the runtime tool provides — and fail-safe but LOUD: an unreadable manifest (or a tool absent from a readable one) falls back to today's host-floor + payload-scan gate and logs a WARNING rather than swallowing it, because an unreadable manifest reopens the unknown-name gap for that whole server; a manifest read never crashes a dispatch. Verified red-then-green in `tests/test_threat_level_manifest_merge.py` (13 tests): the load-bearing case — server `blender`, tool `execute_blender_code`, params `{"code":"print(1)"}` (host floor SAFE, no payload match) with a manifest declaring `dangerous` — is SAFE/not-confirmed against the unfixed `get_tool_metadata` and DANGEROUS/confirmed after the merge; plus caching (one manifest read across repeated dispatches), retry-not-pinned on a failed read, the warning on an unreadable manifest, and a manifest-`safe` tool staying unconfirmed.

*2026-07-27:* STT is no longer Vosk-only (#138) — `STT_PROVIDER=faster-whisper` swaps the utterance transcriber for faster-whisper (`voice-whisper` extra, weights auto-downloaded under `MODELS_DIR/whisper`), degrading to Vosk once, logged, when the package or model is missing. Wake-word activation is unchanged and still Vosk. Verified by unit tests against a mocked `faster_whisper`; not yet exercised on real audio hardware.

*2026-07-22:* extras renamed to `project-jarvis` and completed (voice-input/voice-output/docs); tier table rewritten to match `should_confirm` (ELEVATED confirms, no audit log, no unconditional FORBIDDEN — `allow_all` bypasses all tiers); `smart` mode described as the TLA classifier (max of host floor, manifest level, payload scan); LLM layer documented as provider-agnostic with the shipped failover pool (#78 moved out of Planned Work); socket security corrected to the three `JARVIS_DATA_DIR` endpoints; added Output-Provenance Boundary (#165), Sudo Management (#158), `jarvis/contextor/`, `jarvis/platform/`, and `shellmcp/`.
