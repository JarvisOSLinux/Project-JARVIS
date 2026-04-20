# Contributing to Project JARVIS

Thanks for contributing. This guide keeps contributions clean, reviewable, and production-minded.

## Development Setup

1. Fork and clone the repository.
2. Create and activate a Python virtual environment.
3. Install dependencies:
   - Minimal: `pip install -e .`
   - Full voice support: `pip install -e ".[voice]"`
   - Dev tooling: `pip install -e ".[dev]"`
4. Copy environment template:
   - `cp jarvis/.env.example jarvis/.env`

## Branching and Commits

- Create focused branches from `main`.
- Keep commits small and meaningful.
- Use clear commit messages that describe intent.
- Avoid mixing unrelated changes in one PR.

## Pull Request Process

1. Ensure your branch is up to date with `main`.
2. Run tests and quality checks locally.
3. Update docs for behavior changes.
4. Open a PR using the repository template.
5. Respond to review feedback with small follow-up commits.

## Code Quality Expectations

- Follow `docs/engineering-standards.md`.
- Add tests for new behavior and bug fixes.
- Keep files/modules focused and avoid unnecessary complexity.
- Preserve backward behavior unless the PR explicitly changes it.

## Testing Guidance

- Add or update tests for every non-trivial change.
- Prefer deterministic tests with minimal global state coupling.
- Mock only at external boundaries when practical.

## Reporting Issues

- Use issue templates for bugs and features.
- Include environment, reproduction steps, expected behavior, and actual behavior.
- For security-sensitive issues, follow `SECURITY.md` instead of opening a public issue.

## Documentation Changes

- Update README/docs when commands, config, or architecture behavior changes.
- Keep examples copy-paste friendly.

## Review Checklist (Self-Review)

- [ ] Scope is focused and intentional.
- [ ] Tests cover the changed behavior.
- [ ] Docs are updated if needed.
- [ ] No secrets or sensitive values are added.
- [ ] No accidental debug code or dead code remains.
