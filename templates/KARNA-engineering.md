# Karna LLC — IT / Engineering Team

## Company

Karna LLC is a public health consulting firm. Top-10 CDC contractor. CIO-SP3 contract vehicle. HIPAA/FedRAMP/FISMA compliance required.

## Team

IT & Software Engineering — system lifecycle development, HIPAA-secured platforms, systems integration, infrastructure support for federal health agencies.

## Tech Stack

- Python (Django, FastAPI, Flask)
- JavaScript/TypeScript (React, Node.js)
- PostgreSQL, SQL Server
- Docker, Kubernetes
- AWS GovCloud / Azure Government
- Terraform (infrastructure as code)
- GitHub Actions (CI/CD)
- Splunk / ELK (logging)

## Compliance

- HIPAA — encrypt PHI at rest (AES-256) and in transit (TLS 1.2+)
- FedRAMP — cloud services must be FedRAMP authorized
- FISMA — follow NIST 800-53 controls
- 508 — all web apps must be Section 508 accessible
- FIPS 140-2 — cryptographic modules must be validated

## Conventions

- Branch naming: `feature/JIRA-123-description`, `bugfix/`, `hotfix/`
- PR required for all changes to main
- Code review by at least one peer
- All commits signed
- Test coverage minimum 80%
- No secrets in code — use environment variables or vault

## Rules for Nellie

- Never generate code that stores PHI in plaintext
- Always use parameterized queries (no string concatenation for SQL)
- Flag any code that makes HTTP calls to non-HTTPS endpoints
- Audit logging required for all PHI access
- Never disable TLS verification
- Container images must be scanned for vulnerabilities before deployment
