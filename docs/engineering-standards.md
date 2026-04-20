# Engineering Standards

This document defines the baseline engineering standards for Project JARVIS.
The goal is consistency, maintainability, and reliable delivery.

## 1) Core Principles

- Prioritize clarity over cleverness.
- Keep modules focused on one responsibility.
- Prefer explicit behavior over hidden side effects.
- Preserve user-facing behavior during refactors unless intentionally changed.
- Add tests for behavior, not implementation details.

## 2) Python Code Style

- Follow PEP 8 and project formatter/linter defaults.
- Use descriptive names for modules, functions, variables, and classes.
- Keep functions small and composable.
- Avoid deeply nested conditionals; use guard clauses and early returns.
- Add type hints for public functions and important internal boundaries.

## 3) Naming Conventions

- Modules/files: `snake_case`.
- Functions/variables: `snake_case`.
- Classes: `PascalCase`.
- Constants: `UPPER_SNAKE_CASE`.
- Private/internal helpers: prefix with `_` when appropriate.

## 4) Imports and Dependencies

- Group imports as standard library, third-party, then local modules.
- Remove unused imports and dead dependencies.
- Prefer dependency injection at boundaries over global singleton coupling.
- Avoid circular dependencies by extracting shared interfaces/utilities.

## 5) Logging Standards

- Use structured, contextual logging where possible.
- `DEBUG`: detailed troubleshooting data.
- `INFO`: lifecycle and normal operational milestones.
- `WARNING`: recoverable unexpected behavior.
- `ERROR`: failed operations requiring attention.
- Never log secrets, tokens, API keys, or raw credentials.

## 6) Error Handling

- Fail fast on invalid configuration and missing critical dependencies.
- Raise specific exceptions instead of broad generic errors.
- Wrap external boundary calls (network, shell, subprocess, I/O) with clear error context.
- User-facing errors should be actionable and concise.
- Avoid silent `except` blocks.

## 7) Configuration and State

- Treat configuration as immutable during a runtime session unless explicitly reloaded.
- Avoid import-time side effects that mutate runtime behavior unexpectedly.
- Centralize environment variable parsing and validation.
- Document all required environment variables and defaults.

## 8) Testing Expectations

### Required by Change Type

- Bug fix: add or update at least one regression test.
- New feature: add unit tests and integration coverage for key flow.
- Refactor: ensure existing behavior is preserved via tests.
- Config/runtime changes: include tests for success and failure paths.

### Test Quality Rules

- Prefer behavior-driven assertions over implementation-specific assertions.
- Mock external systems only at process boundaries.
- Keep tests deterministic and isolated.
- Use clear test names (`test_<behavior>_<expected_outcome>`).

## 9) PR and Review Standards

- Keep PRs focused on one objective.
- Include concise rationale (why), not only what changed.
- Update docs when behavior or interfaces change.
- Include test evidence for all non-trivial changes.
- Avoid mixing refactors and new features unless necessary.

## 10) Definition of Ready (Before Coding)

- Problem statement is clear.
- Scope and out-of-scope are clear.
- Affected modules are identified.
- Test approach is identified.

## 11) Definition of Done (Before Merge)

- Code is readable and follows this standard.
- Lint/type/tests pass locally (and in CI once configured).
- Docs are updated where needed.
- No debug leftovers, temporary hacks, or commented-out legacy blocks.
- Risky paths include safety checks and meaningful logs.
