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
  jarvis/server/            — opt-in OpenAI-compatible HTTP endpoint (`openai_compat.py`, off by default)

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
| FORBIDDEN | Blocked pending user confirmation |

There is no separate audit log and no unconditional FORBIDDEN block:
`allow_all` bypasses ALL tiers including FORBIDDEN (documented risk).

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
| `jarvis/tui/lifecycle.py` | App startup/shutdown, callback registration |
| `jarvis/tui/confirm_modal.py` | Tool confirmation modal |
| `jarvis/tui/config_modal.py` | Tabbed config/provider settings modal (F2 / /settings) |
| `jarvis/tui/provider_modal.py` | Provider add/edit modal |
| `jarvis/core/confirmation_manager.py` | Multi-channel confirmation gate; pending list persisted to `$JARVIS_DATA_DIR/confirmations.json` when built with a store path (#224) |
| `jarvis/core/constraint_store.py` | Standing path-prefix deny constraints (#214), enforced in `dispatch_flow` ahead of the confirmation mode |
| `jarvis/core/socket_security.py` | Socket hardening |
| `jarvis/core/sudo_manager.py` | `jarvis sudo` — sudoers drop-in management (#158) |
| `jarvis/dispatch/boundary.py` | Output-provenance boundary verification (#165) |
| `jarvis/core/command_parser.py` | LLM response parser + action registry |
| `jarvis/core/voice_state.py` | `VoiceState` — formal voice/response session state machine |
| `jarvis/voice/stt/__init__.py` | `create_stt()` — `STT_PROVIDER` → provider class, with the one logged Vosk fallback (#138) |
| `jarvis/voice/stt/whisper_stt.py` | `WhisperSTT` — faster-whisper utterance transcription (lazy import, `is_available()`/`ensure_model()` never raise) |
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
| `jarvis/dispatch/sign_in.py` | Sign-in via `dmcp login` (#229): `credential_required` parsing, SIGN_IN_NEEDED hints, device-code display to the user only |
| `jarvis/dispatch/event_merger.py` | Merges voice/CLI/socket/dispatch events |
| `jarvis/runtime/root_actions.py` | ROOT-mode LLM response action handlers |
| `jarvis/runtime/root_context.py` | Context assembly for ROOT-mode prompts |
| `jarvis/runtime/llm_bridge.py` | `ask_llm()` — the sole locked/mode-atomic LLM call site |
| `jarvis/runtime/events.py` | Event routing + per-event task tracking (concurrent goals) |

---

## Changelog — corrected claims

*2026-09-29:* servers that work in the user's account sign in instead of taking a pasted token (#229, JARVIS half; the registry and dmcp halves are mcp-registry#104 and dmcp#69).
- **What dmcp does now.** A call whose server lacks its declared account is refused before spawn, and the error text carries a `credential_required: {json}` line.
- **The hint.** `root_handlers._failure_hints` replaces `_config_hint_for_signals`. It makes one manifest read per failing server and gives either SIGN_IN_NEEDED (the server declares `credentials`) or CONFIG_HINT (it does not), never both. An auth error from an account server, such as a revoked token, also gets SIGN_IN_NEEDED.
- **The line is a trigger, not an authority.** dmcp relays server stderr, so a server can print a look-alike. The server comes from the PID map, and the provider must be declared in that server's installed manifest.
- **The `sign_in` action.** It runs `dmcp login <provider> --for <server> --json` (`dispatch/sign_in.run_sign_in`). The device code and URL go to the user through `output_manager.handle_response` only: not the model's context, the session history or memory. The model sees `SIGN_IN_RESULT` with the account name, "declined", "expired", or `SIGN_IN_ERROR`. One sign-in per (server, provider) at a time.
- **Install and docs.** Install no longer asks, in the secure form, for keys an account fills, and it ends with SIGN_IN_NEEDED. `get_server_docs` adds an `ACCOUNT:` line, and its "requires configuration" hint leaves out account-filled keys.
- **Verification.** `tests/test_sign_in.py` has 19 tests. Mutation checks on the claim checks, the hint split, the install filter, a code leak to the model, and the undeclared-provider guard each turn a test red. A contract run of `run_sign_in` against the real dmcp branch binary and its fake OAuth provider went through: refused call (exit 3), parsed claim, code shown to the user, `signed_in`, then the account delivered on the next call.

*2026-09-28:* secrets no longer travel through the conversation (#242). Outside the TUI, the designed way to configure a server's API key was for the user to type it into the chat: the `configure_server` prompt told the model to ask, and the placeholder guard sent it back to ask again. The message was then logged at INFO, added to the goal tree (archived to `goal_archive.jsonl`), stored raw by `contextor.auto_store_prompt` — searched on every later prompt — sent to the model, echoed in its `configure_server` output, and written to `jarvis_params.toml` as a second plaintext copy. On the reference machine a GitHub token was in four files and an API key in the goal archive, after sessions had been deleted repeatedly. Now: `core/secret_scrubber.py` redacts well-known credential formats at the input funnel (`on_user_input`, `sync_ask`) before anything is stored or sent, and as a filter on every JARVIS logger and the file handler, which also covers `runtime/io.py` logging the first 80 characters of socket input (enough for a whole GitHub token) before the funnel runs. `configure_server` never takes a `sensitive` value from the model, even a real-looking one; naming the key opens `_collect_config_securely` in `root_actions.py` — the TUI modal, otherwise the Tk dialog in `ui/config_prompt.py`, which masked secrets already but was reachable only from the dead `_run_dispatch_subchain_legacy`. Install outside the TUI now collects config through the same form instead of skipping it. SERVER_DOCS and CONFIG_HINT share `configure_call_hint`, which shows secret keys empty. `ParamsStore` stores no sensitive value, and its TOML headers are now quoted: unquoted, TOML read every reverse-DNS server id as nested tables, `_load` stringified them, and the next save wrote invalid TOML, so the store had never round-tripped a single server. Also fixed: `on_user_input` called `output_manager.display`, which does not exist, so with no model configured every message raised instead of telling the user — a test asserted that `display` was called, passing against a bare `Mock`. Tests touching the output manager now spec it.

*2026-09-21:* `privileged_prefixes()` is folded into `classify()` as a fifth, raise-only floor (#208 resolution). Previously it and the rest of the retired-shellmcp elevation surface (`find_askpass`/`elevate`/`grant_privilege`/etc.) had zero callers outside `jarvis/platform/`; the design question #208 left open — fold the per-OS prefix table into TLA classification, or retire it as superseded by dmcp scope-based elevation — was decided in favor of folding, since it catches command-ish parameters under unknown tool names that evade both the host-name floor and the (deliberately narrow) payload-scan regexes. Same DANGEROUS severity as the payload scan. `resolve_sidecar()`/`sidecar_search_dirs()` stay as documented below (kept, not dropped) — that box was already resolved when this section's "keep it until packaging lands" line landed in `a8ffcf1`.

*2026-09-01:* `jarvis confirm` is no longer socket-only (#224 follow-up). The Confirmation Channels paragraph described a review queue that survives a restart, but the only way to review it exited 1 whenever `_find_ipc_endpoint()` found nothing or `ipc_connect` was refused — so the queue was unreachable in exactly the window it was built for, between restarts. `_cmd_confirm` now falls back to the store file for listing (the renderer was extracted to `_print_confirmation_list` so live and offline output is the same code, not a lookalike) and appends decisions to `$JARVIS_DATA_DIR/confirmation_decisions.json`. `_find_ipc_endpoint(quiet=True)` suppresses only the not-found message: a failed ownership check still prints its error loudly before the fallback runs, because a socket that is not ours is a security signal rather than a stopped daemon — but the review itself continues offline, since the queue is a same-user file our own daemon will read. The four decision branches of `io._handle_confirmation_query` were extracted to `io.apply_confirmation_decision()`, which the startup replay calls, so there is one translation from wire protocol to `CONFIRMATION_RESPONSE` events rather than a second copy that can drift. Verified red-then-green in `tests/test_confirmation_offline_queue.py` (20 tests): offline list from the store on an absent endpoint and on a refused connect, each protocol shape queued exactly, replacement per id, unknown id refused without writing, a reachable daemon writing nothing at all, startup replay resuming through `root_handlers.on_confirmation_response` with the queue file cleared and the summary logged, a stale id warn-skipping while the file still clears, and a corrupt queue quarantined as `.bad` without breaking startup.

*2026-09-01:* the pending-confirmations list is no longer memory-only (#224). The Confirmation Channels paragraph above said it "lives only in the daemon's memory ... does NOT survive a daemon restart ... Tracked as a gap, not a design decision" — the gap is now closed. `ConfirmationManager.__init__` takes an optional `store_path`; the daemon's instance gets `$JARVIS_DATA_DIR/confirmations.json` from `component_factory.create_confirmation_manager()`, and `_pending` is mirrored there (atomic tmp + `os.replace`, modelled on `constraint_store.py`) after the request that adds an entry and after the `resolve()` that pops it — the only two mutation points, so every channel and the auto-deny timer flow through them. Restore happens in the constructor: entries come back with `created_at`/`session_id` intact, fire no notification, and arm no timeout task regardless of `CONFIRMATION_TIMEOUT` (a clock started before the restart would fire on an arbitrary remainder), so a restored item is a review-queue entry for `jarvis confirm`/GUI. A corrupt or unreadable store warns and starts empty. Approving a restored entry resumes through `root_handlers.on_confirmation_response` as before, except the owning goal was archived at shutdown (#146) — `link_tasks`/`link_dispatch_fingerprint`/`link_dispatch_servers` were already no-ops for a missing goal; the resume path now logs that once instead of leaving "dispatched unlinked" invisible. A bare `ConfirmationManager()` is unchanged and writes nothing, which is what every existing test uses.

*2026-08-15:* socket-security bullet corrected — `verify_socket_ownership()` has no callers; the live pre-connect ownership check is `platform.ipc_verify_owner()` called directly from `cli.py`. Key Files gains `jarvis/core/constraint_store.py` (#214), which was shipped but undocumented here.

*2026-08-17:* the registry trust tier scales the TLA gate (#223). `get_tool_metadata` stamps `registry_tier` (install-time `trustStatus` from the local manifest; `unknown` when absent/unreadable/legacy) and `registry_declared` unconditionally — server-scope provenance the server's own output can never supply — and `classify()` floors a tool at ELEVATED unless the tier is `official`/`community` AND the tool is declared. The 2026-08-07 merge below is no longer purely additive: the manifest now OVERWRITES a runtime-carried `threat_level`/`confirmation_required`, because `tools/list` cannot legitimately carry them and a runtime value is injection. An unreadable manifest now floors at ELEVATED (was: fall back to host floor + payload scan). dmcp normalizes the recorded tier at install and stamps `unknown` on connect/URL installs (attacker-supplied `trustStatus` no longer persists).

*2026-08-07:* the TLA gate now sees a manifest's per-tool `threat_level` at runtime. `get_tool_metadata` (`jarvis/runtime/dispatch_flow.py`) fed the confirmation gate only the live MCP `tools/list` view (name/description/inputSchema) from `list_server_tools`; `threat_level` and the legacy `confirmation_required` are REGISTRY-manifest fields absent from that protocol output, so `threat_level._declared()` always read SAFE and **every manifest declaration was inert** — the registry audit's 215/215 threat levels were decorative, and a dangerous tool whose bare NAME is not in the host floor (blender's `execute_blender_code`, the whole filesystem server) escaped confirmation in `smart` mode. `get_tool_metadata` now merges the manifest's per-tool declaration onto the runtime metadata, sourced from the **local** `get_server_manifest` (no network) and cached per server_id (`_THREAT_DECL_BY_SERVER`, modelled on `_STATEFUL_BY_SERVER`). The merge is additive — the runtime tool dict stays, a declaration never clobbers a field the runtime tool provides — and fail-safe but LOUD: an unreadable manifest (or a tool absent from a readable one) falls back to today's host-floor + payload-scan gate and logs a WARNING rather than swallowing it, because an unreadable manifest reopens the unknown-name gap for that whole server; a manifest read never crashes a dispatch. Verified red-then-green in `tests/test_threat_level_manifest_merge.py` (13 tests): the load-bearing case — server `blender`, tool `execute_blender_code`, params `{"code":"print(1)"}` (host floor SAFE, no payload match) with a manifest declaring `dangerous` — is SAFE/not-confirmed against the unfixed `get_tool_metadata` and DANGEROUS/confirmed after the merge; plus caching (one manifest read across repeated dispatches), retry-not-pinned on a failed read, the warning on an unreadable manifest, and a manifest-`safe` tool staying unconfirmed.

*2026-07-27:* STT is no longer Vosk-only (#138) — `STT_PROVIDER=faster-whisper` swaps the utterance transcriber for faster-whisper (`voice-whisper` extra, weights auto-downloaded under `MODELS_DIR/whisper`), degrading to Vosk once, logged, when the package or model is missing. Wake-word activation is unchanged and still Vosk. Verified by unit tests against a mocked `faster_whisper`; not yet exercised on real audio hardware.

*2026-07-22:* extras renamed to `project-jarvis` and completed (voice-input/voice-output/docs); tier table rewritten to match `should_confirm` (ELEVATED confirms, no audit log, no unconditional FORBIDDEN — `allow_all` bypasses all tiers); `smart` mode described as the TLA classifier (max of host floor, manifest level, payload scan); LLM layer documented as provider-agnostic with the shipped failover pool (#78 moved out of Planned Work); socket security corrected to the three `JARVIS_DATA_DIR` endpoints; added Output-Provenance Boundary (#165), Sudo Management (#158), `jarvis/contextor/`, `jarvis/platform/`, and `shellmcp/`.
