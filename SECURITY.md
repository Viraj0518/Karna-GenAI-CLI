# Security Policy

## Supported versions

Karna / Nellie is pre-1.0. Security fixes land on the latest minor release only.

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a vulnerability

**Please do not open a public GitHub issue for a vulnerability.**

Preferred channels, in order:

1. **GitHub private security advisory** — go to the repository's **Security** tab → **Report a vulnerability**. This is the fastest path and lets us coordinate a fix in private.
2. **Email** — send details to `virajsharma8547@gmail.com` with subject `[karna-security]`. PGP available on request.

When reporting, please include:

- A clear description of the vulnerability and its impact.
- Steps to reproduce (minimal repro script preferred).
- Affected version(s) / commit SHA.
- Your name / handle if you'd like credit in the advisory.

## Response SLA

- **Acknowledgement**: within **2 business days**.
- **Initial assessment and severity rating**: within **5 business days**.
- **Fix or mitigation plan communicated to reporter**: within **14 days** for high/critical, **30 days** for medium, **best-effort** for low.
- **Public disclosure**: coordinated with the reporter. Default embargo is 90 days or until a patched release is available, whichever comes first.

We follow [CVSS 3.1](https://www.first.org/cvss/) for severity and will request a CVE via GitHub when applicable.

## Scope

In scope:

- The `karna` Python package and the `nellie` CLI.
- Sandboxing, auth, and guard logic under `karna/security/`, `karna/auth/`, `karna/agents/safety.py`.
- Supply-chain (dependencies pinned in `pyproject.toml`, release workflow).

Out of scope:

- Third-party MCP servers you install yourself.
- Vulnerabilities requiring a pre-compromised local machine (e.g. attacker already has shell).
- Rate-limit / denial-of-service against a local dev machine.

## Safe harbor

We will not pursue or support legal action against researchers who:

- Make a good-faith effort to avoid privacy violations, destruction of data, and service interruption.
- Give us reasonable time to respond before public disclosure.
- Do not exfiltrate data beyond what is necessary to demonstrate the issue.

Thank you for helping keep Karna safe.
