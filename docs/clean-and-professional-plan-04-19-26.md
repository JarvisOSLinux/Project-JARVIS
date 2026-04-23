# Clean and Professional Plan (04-19-26)

This document is the working cleanup and professionalization roadmap for Project JARVIS.
Use it as the single source of truth for cleanup tasks, sequencing, and completion criteria.

## Goals

- Keep the codebase maintainable as features grow.
- Improve reliability through automated quality checks.
- Standardize engineering practices across modules.
- Reduce technical debt in high-risk/high-change areas.
- Make onboarding and contribution clearer for future work.

## How to Use This Plan

- Work from top to bottom by phase.
- Prefer small, reviewable PRs (one objective per PR).
- Mark checklist items complete only when acceptance criteria are met.
- If scope changes, append notes under the relevant task (do not silently rewrite history).

## Current Baseline (04-19-26)

- Architecture is strong and modular in many areas.
- Several key files are becoming large and multi-responsibility.
- CI and quality automation are not yet strict enough.
- Integration testing can be expanded to reduce regression risk.

---

## Phase 1 - Foundation and Quality Gates (Highest Priority)

Target: stabilize day-to-day quality and prevent regressions.

### 1.1 CI Pipeline
- [x] Add GitHub Actions workflow for:
  - [x] Lint/format checks
  - [x] Type checks
  - [x] Test run (`pytest`)
- [x] Ensure workflow runs on PR + push to main branch.
- [x] Add status badges to `README.md` (optional but recommended).

Acceptance criteria:
- Every PR gets automatic pass/fail quality feedback.
- CI fails on lint/type/test failures.

### 1.2 Local Developer Quality Commands
- [x] Define one canonical local quality command (example: `make check` or script).
- [x] Define one canonical local auto-fix command (example: `make fix`).
- [x] Document both in `README.md`.

Acceptance criteria:
- A contributor can run one command to validate project quality locally.

### 1.3 Engineering Standards Document
- [x] Create `docs/engineering-standards.md` with:
  - [x] Naming conventions
  - [x] Logging conventions
  - [x] Error handling rules
  - [x] Test expectations by change type
  - [x] PR size and review guidelines

Acceptance criteria:
- New changes can be evaluated against explicit written standards.

---

## Phase 2 - Architecture Cleanup and Module Boundaries

Target: reduce complexity in core runtime paths.

### 2.1 Main Runtime Decomposition

**Note (04-21-26):** This checklist tracks *where code lives* (maintainability), not an inventory of bugs. Large `main.py` was a single file doing many jobs; splitting it does not imply the project had “that many issues.”

- [x] Audit `jarvis/main.py` responsibilities.
- [x] Split into focused modules/services (example targets):
  - [x] Runtime lifecycle/startup (`jarvis/runtime/lifecycle.py`, voice thread, stop/shutdown)
  - [x] Input routing (`events.py`, `session_commands.py`, `stdin_is_tty`, `sync_ask` / voice callback)
  - [x] Dispatch interaction (`dispatch_flow.py`)
  - [x] Confirmation flow (`root_handlers.py` + existing confirmation wiring)
  - [x] Socket/event handling (`io.py`, `events.py`)
- [x] Keep behavior unchanged while refactoring (no feature coupling).

Acceptance criteria:
- Main orchestration logic is easier to navigate and test.
- Core file size and cognitive load are materially reduced.

### 2.2 TUI Structure Review
- [ ] Audit `jarvis/tui/app.py` for separable concerns:
  - [ ] UI state model
  - [ ] Rendering/layout
  - [ ] command/event handlers
- [ ] Extract repeated logic into helper modules/classes.

Acceptance criteria:
- TUI behavior remains the same.
- File-level complexity and duplication are reduced.

---

## Phase 3 - Configuration and State Management Hardening

Target: make configuration predictable and safer in long-running sessions/tests.

### 3.1 Config Refactor Plan
- [ ] Review `jarvis/config.py` for import-time mutable globals.
- [ ] Introduce a clearer runtime config object pattern.
- [ ] Define explicit reload/update boundaries.

Acceptance criteria:
- Config behavior is deterministic and test-friendly.
- Runtime mutation side effects are minimized and documented.

### 3.2 Environment and Secret Hygiene
- [ ] Validate `.env` expectations and defaults.
- [ ] Ensure sensitive values are never logged.
- [ ] Add/update docs for required env vars and safe defaults.

Acceptance criteria:
- Startup/config failures are clear and actionable.
- No obvious secret-leak logging paths.

---

## Phase 4 - Testing Maturity Upgrade

Target: increase confidence in real runtime behavior.

### 4.1 Test Strategy Clarification
- [ ] Add `docs/testing-strategy.md` that defines:
  - [ ] Unit vs integration vs end-to-end boundaries
  - [ ] Mocking policy (when allowed vs discouraged)
  - [ ] Required tests by change type

Acceptance criteria:
- Team has a consistent testing policy for new changes.

### 4.2 Critical Path Integration Tests
- [ ] Identify top 5 critical workflows.
- [ ] Add higher-fidelity integration tests for each.
- [ ] Reduce over-mocking where practical.

Acceptance criteria:
- Regressions in critical runtime flows are caught automatically.

### 4.3 Smoke Tests
- [ ] Add lightweight smoke checks for:
  - [ ] CLI startup path
  - [ ] Core orchestrator initialization
  - [ ] Optional TUI bootstrap (where feasible)

Acceptance criteria:
- Basic boot regressions are detected quickly.

---

## Phase 5 - Security and Operational Professionalization

Target: harden high-risk capabilities and improve operational safety.

### 5.1 Command Execution Safety Review
- [ ] Audit shell execution paths and privilege-related code.
- [ ] Add stricter command allow/deny rules where needed.
- [ ] Add explicit confirmation/audit logs for sensitive actions.

Acceptance criteria:
- Risky command paths are constrained and auditable.

### 5.2 Observability and Failure Diagnostics
- [ ] Standardize structured logging in critical paths.
- [ ] Ensure major failures provide actionable error context.
- [ ] Add quick troubleshooting section to docs.

Acceptance criteria:
- Failures are easier to diagnose without deep code tracing.

---

## Work Cadence (Recommended)

- Weekly: complete 2-4 checklist items.
- End of week: summarize what changed and what moved to next week.
- Keep PRs small and objective-based.

## Definition of Done for This Initiative

- CI quality gates are active and required.
- Core high-complexity modules are decomposed.
- Config/state behavior is deterministic and documented.
- Testing strategy is written and critical paths are covered.
- Security-sensitive execution paths are reviewed and hardened.
- Engineering standards are documented and followed by default.

## Progress Log

Use this section to record completed milestones.

- 04-19-26: Plan created.
- 04-19-26: Added engineering standards document (`docs/engineering-standards.md`).
- 04-19-26: Added GitHub professionalism docs (`CONTRIBUTING.md`, `SECURITY.md`, PR template, issue templates).
- 04-19-26: Updated `README.md` with contribution, standards, security, and roadmap links.
- 04-19-26: Added CI workflow (`.github/workflows/ci.yml`) and CI badge in `README.md`.
- 04-19-26: CI baseline stabilized after first run (auto-formatted code with Black/isort and refined lint/type/test gates for deterministic passing checks).
- 04-19-26: Added root `Makefile` with `make check`, `make fix`, and `make test`; documented local quality commands in `README.md`.
- 04-19-26: Started Phase 2.1 refactor by extracting lifecycle/startup helpers inside `jarvis/main.py` to reduce `run()` complexity without behavior changes.
- 04-19-26: Created `jarvis/runtime/lifecycle.py` and moved startup/lifecycle orchestration helpers out of `jarvis/main.py`; behavior preserved and checks passing.
- 04-19-26: Created `jarvis/runtime/io.py` and extracted socket/broadcast runtime I/O handlers from `jarvis/main.py` into the runtime module.
- 04-19-26: Created `jarvis/runtime/events.py` and extracted event-routing/input-source helpers (`_handle_event`, `_await_user_input`, `_await_dispatch_signal`) into runtime module helpers.
- 04-21-26: Created `jarvis/runtime/root_actions.py` and extracted ROOT-mode action handling (`_act_on_root_response`) from `jarvis/main.py`.
- 04-21-26: Created `jarvis/runtime/dispatch_flow.py` and extracted DISPATCH subchain orchestration (`_run_dispatch_subchain`) from `jarvis/main.py`.
- 04-21-26: Continued `dispatch_flow` extraction by moving dispatch confirmation send/metadata helpers (`_dispatch_send`, `_get_tool_metadata`) out of `jarvis/main.py`.
- 04-21-26: Moved root-path task execution (`_dispatch_execute_tasks`) into `jarvis/runtime/dispatch_flow.py`.
- 04-21-26: Moved `_feed_root_summary` into `jarvis/runtime/root_actions.py` as `feed_root_summary`; `root_actions` now calls it directly (no `app._feed_root_summary` indirection).
- 04-21-26: Moved dispatch kill/defer helpers (`_do_kill`, `_do_defer`) into `jarvis/runtime/dispatch_flow.py` as `do_kill` / `do_defer`; `run_dispatch_subchain` calls them directly.
- 04-21-26: Created `jarvis/runtime/root_handlers.py` and moved `_on_user_input`, `_on_dispatch_signal`, and `_on_confirmation_response` out of `jarvis/main.py`; `events.handle_event` routes to these functions; `Jarvis` keeps thin delegate methods.
- 04-21-26: Added `jarvis/runtime/root_context.py` (`build_root_context`, `compact_payload_for_llm`) and `jarvis/runtime/goal_updates.py` (`apply_goal_updates`); runtime modules call them directly; `Jarvis` methods remain thin delegates. `feed_root_summary` now takes `logger` for consistent context logging.
- 04-21-26: Added `jarvis/runtime/session_commands.py` (`handle_slash_command`, `session_reply`); `root_handlers` calls it directly; `Jarvis` keeps thin `_handle_slash_command` / `_session_reply` delegates. Updated TUI slash-command doc pointers.
- 04-21-26: Added `jarvis/runtime/voice_activation_thread.py` (`run_voice_activation`, `process_voice_command_inject`); `lifecycle.start_runtime_services` starts the daemon thread with `target=run_voice_activation, args=(app, logger)`. Moved TTY stdin check to `lifecycle.stdin_is_tty()`; `Jarvis._has_stdin` delegates there; voice methods delegate to the voice module.
- 04-21-26: Added `jarvis/runtime/sync_ask.py` with `sync_ask` (synchronous one-shot CLI path) and `handle_voice_command` (inject vs `sync_ask`); `Jarvis.ask` / `_handle_voice_command` are thin delegates; dropped unused `Config` import from `main.py`.
- 04-21-26: Added `jarvis/runtime/llm_bridge.py` (`ask_llm_sync`, `ask_llm`); `root_handlers`, `root_actions`, `dispatch_flow`, and `sync_ask` call them directly; `Jarvis._ask_llm` / `_ask_llm_sync` remain thin delegates.
- 04-21-26: Added `lifecycle.request_stop` and `lifecycle.shutdown`; `run()` uses `partial(request_stop, self)` for signal handlers and `await shutdown(self, logger)` in `finally`; `Jarvis.stop` / `_shutdown` delegate.
- 04-21-26: Added `jarvis/runtime/output_hooks.py` (`emit_activity`, `persist_assistant_turn`, `get_embeddings`); `dispatch_flow`, `root_actions`, `root_handlers`, `llm_bridge`, and `sync_ask` call them directly; `Jarvis` keeps thin `_activity` / `_persist_assistant_turn` / `_get_embeddings` delegates. Marked Phase **2.1** checklist complete in this doc (main orchestration slice done; optional follow-up: constructor-only `Jarvis` or Phase **2.2** TUI).
- 04-21-26: Started dispatch adapter decomposition by adding `jarvis/dispatch/transport.py` (connect/disconnect, connection guard, timed MCP tool call helper, signal-window fetch); `DispatchAdapter` now delegates connection lifecycle + core transport calls (`send_tasks`, `kill_tasks`, `set_timer`, `get_signal_window`) while preserving behavior.
- 04-21-26: Continued dispatch adapter decomposition with `jarvis/dispatch/dmcp_registry.py` (`run_dmcp`, `search_servers`, `install_server`, `list_server_tools`); `DispatchAdapter` now delegates dmcp browse/install/tools methods and keeps `_run_dmcp` as a thin wrapper for remaining discovery paths.
- 04-21-26: Continued dispatch adapter decomposition with `jarvis/dispatch/discovery.py` (`server_count`, `normalize_count`, `embedding_spec`, `sync_index`, `browse_vector`, `browse_vectors_batch`, `index_server`, `auto_index_server`, `ensure_embedding_model`); `DispatchAdapter` now delegates vector/index lifecycle methods while preserving existing method signatures.
- 04-21-26: Added `jarvis/dispatch/tool_discovery.py` (`discover_tools`, `keyword_fallback`, `format_available_tools`); `DispatchAdapter` now delegates high-level task-to-tool discovery/formatting while retaining `select_discovery_mode` and the same public API.
