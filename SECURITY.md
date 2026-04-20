# Security Policy

Project JARVIS includes command execution and system integration capabilities. Please report security concerns responsibly.

## Supported Versions

Security fixes are applied to the latest active branch first.
Older versions may not receive patches.

## Reporting a Vulnerability

Do not create a public GitHub issue for potential vulnerabilities.

Please report privately with:

- Vulnerability description
- Impact and affected components
- Reproduction steps or proof of concept
- Suggested remediation (if available)

Use one of these channels:

- GitHub private security advisory (preferred)
- Direct maintainer contact (if advisory is unavailable)

## Response Targets

- Initial acknowledgment: within 72 hours
- Triage and severity assessment: within 7 days
- Mitigation plan or fix timeline: as soon as triage is complete

## Disclosure Policy

- Coordinate disclosure after a fix is available.
- Credit reporters who want attribution.
- Reserve the right to adjust timelines for high-risk vulnerabilities.

## Security Best Practices for Contributors

- Never commit secrets, credentials, or private keys.
- Avoid logging sensitive user data and environment secrets.
- Add explicit safeguards for shell or privileged operations.
- Prefer allowlist and confirmation checks for risky execution paths.
