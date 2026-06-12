# Contributing to Project JARVIS

Thanks for contributing. This project is stewarded by the **JarvisOSLinux Organization** and its Governing Board. All contributions are welcome — whether you're an individual, a community member, or contributing on behalf of a company.

This guide covers both paths:

- **Individual contributors** — the standard open-source flow (fork, PR, DCO sign-off)
- **Commercial licensees** — additional obligations under the Sovereign Commons Commercial License (SCCL v1)

If you're just here to fix a bug or add a feature, you only need to follow the individual contributor sections.

---

## Development Setup

1. Fork and clone the repository.
2. Create and activate a Python virtual environment.
3. Install dependencies:
   - Minimal: `pip install -e .`
   - Full voice support: `pip install -e ".[voice]"`
   - TUI: `pip install -e ".[tui]"`
   - Dev tooling: `pip install -e ".[dev]"`
   - Everything: `pip install -e ".[all]"`
4. Copy environment template:
   - `cp jarvis/.env.example jarvis/.env`

---

## Individual Contributors

### Licensing

All community contributions are made under the **GNU Affero General Public License v3 (AGPLv3)**. By submitting a contribution, you agree that your work will be licensed under AGPLv3.

### Developer Certificate of Origin (DCO)

We use the [Developer Certificate of Origin](https://developercertificate.org/) to verify that contributors have the right to submit their work. Every commit must include a `Signed-off-by` line:

```
Signed-off-by: Your Name <your.email@example.com>
```

Git can add this automatically with the `-s` flag:

```bash
git commit -s -m "Fix memory leak in dispatch adapter"
```

Commits without a valid `Signed-off-by` line will not be merged.

### Code of Conduct

All contributors must follow the project's Code of Conduct. Respectful, constructive interaction is expected in all project spaces — issues, PRs, discussions, and chat.

---

## Commercial Licensees

If you are contributing on behalf of a company operating under the **Sovereign Commons Commercial License (SCCL v1)**, the following additional requirements apply:

### Contributor License Agreement (CLA)

Commercial licensees must sign the project's CLA before contributions can be accepted. The CLA covers intellectual property assignment for contributions made under SCCL. Contact the Organization for the current CLA.

### Contribution-Back Requirement

Under SCCL Article 5, any modifications, patches, improvements, or derivative works developed by or on behalf of a commercial licensee must be submitted to the Organization within **ninety (90) days** of internal deployment or use, whichever is earlier.

### What You Must Contribute Back

- Changes to the Software itself (source code, tooling, build scripts)

### What You Don't Need to Contribute Back

- Proprietary business logic, data, or configurations that are separate from and do not modify the Software's codebase (SCCL Article 5.4)

### Submission

Contributions from commercial licensees follow the same PR process as individual contributors, but must reference the CLA and include the commercial entity name in the PR description.

---

## Branching and Commits

- Create focused branches from `main`.
- Keep commits small and meaningful.
- Use clear, imperative commit messages that describe intent.
- Avoid mixing unrelated changes in one PR.

## Pull Request Process

1. Ensure your branch is up to date with `main`.
2. Run tests and quality checks locally: `make check`
3. Update docs for behavior changes.
4. Open a PR using the repository template.
5. Respond to review feedback with small follow-up commits.

## Code Quality Expectations

- Follow `docs/engineering-standards.md`.
- Add tests for new behavior and bug fixes.
- Keep files/modules focused and avoid unnecessary complexity.
- Preserve backward behavior unless the PR explicitly changes it.
- Python: Black formatting, type hints on public functions.
- Rust: `cargo fmt` + `cargo clippy` clean before pushing.

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
- [ ] All commits are signed off (`Signed-off-by`).
