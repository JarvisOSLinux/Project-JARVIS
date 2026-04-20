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
- [ ] Define one canonical local quality command (example: `make check` or script).
- [ ] Define one canonical local auto-fix command (example: `make fix`).
- [ ] Document both in `README.md`.

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
- [ ] Audit `jarvis/main.py` responsibilities.
- [ ] Split into focused modules/services (example targets):
  - [ ] Runtime lifecycle/startup
  - [ ] Input routing
  - [ ] Dispatch interaction
  - [ ] Confirmation flow
  - [ ] Socket/event handling
- [ ] Keep behavior unchanged while refactoring (no feature coupling).

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
