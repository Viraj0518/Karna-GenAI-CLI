# Karna LLC — Nellie Project Instructions

> Copy this file to your project root as `KARNA.md`. Nellie reads it on every session start.

## Company

Karna LLC is a public health consulting firm headquartered in Atlanta, GA. Subsidiary of Celerian Group (BlueCross BlueShield of SC). ~120 employees. Top-10 CDC contractor.

## NAICS Codes

- 541611 — Administrative Management and General Management Consulting
- 541690 — Other Scientific and Technical Consulting
- 541720 — R&D in Social Sciences and Humanities
- 541910 — Marketing Research and Public Opinion Polling
- 518210 — Data Processing, Hosting, and Related Services
- 541512 — Computer Systems Design Services

## Contract Vehicles

- CIO-SP3 Small Business (GWAC) — Contract #75N98120D001239
- SPARC IDIQ (via Celerian Group) — CMS modernization
- CDC NCIRD ICRA IDIQ — Immunization consulting and research
- CDC OADC BPA — Communications services

## Primary Clients

CDC (12+ centers), CMS, Defense Health Agency, NIOSH, state health departments

## Compliance Requirements

- HIPAA — all PHI must be handled per 45 CFR 164
- FedRAMP — cloud services must meet FedRAMP requirements
- FAR/DFARS — federal acquisition regulations apply to all contract work
- Human subjects research — 45 CFR 46 (Common Rule)
- FISMA — federal information security standards

## Rules for Nellie

- Never store or log PHI (patient names, MRNs, SSNs, DOBs) in any output
- Never commit credentials, API keys, or tokens to git
- Always use HIPAA-compliant tools when handling health data
- Default to the most conservative interpretation of data handling requirements
- When in doubt about compliance, flag it — don't proceed silently

## Team-Specific Section

<!-- Replace this section with your team's specifics -->

### [Your Team Name]

**Tech stack:** [e.g., Python, R, SAS, PostgreSQL]
**Key repos:** [list your team's repositories]
**Conventions:** [coding standards, branch naming, PR process]
**Data sources:** [what databases/APIs your team uses]
**Deliverable formats:** [CDC reports, MMWR-style, dashboards, etc.]
