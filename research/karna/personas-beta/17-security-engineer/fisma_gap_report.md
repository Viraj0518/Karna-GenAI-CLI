# FISMA-Moderate Self-Assessment Gap Report
## Karna Analytics Stack

**Organization:** Karna  
**System:** Analytics Platform (Postgres On-Premise, Python ETL, Shared File Drop, Internal Dashboards)  
**Classification:** FISMA-Moderate  
**Assessment Date:** 2025-01-24  
**Scope:** Data storage, ETL pipeline, file sharing, and BI layer

---

## Executive Summary

This self-assessment identifies 8 critical gaps against NIST 800-53 controls. The highest-priority gaps are:

• **AC-2 Account Management**: No centralized identity provisioning/deprovisioning; shared credentials for ETL service accounts create orphaned access post-termination.

• **SC-7 Boundary Protection**: Postgres instance lacks network segmentation; shared file drop has no DLP controls, enabling data exfiltration.

• **AU-12 Audit Logging**: ETL pipeline generates no structured audit logs; dashboard access is not logged; Postgres audit disabled by default.

---

## Gap Remediation Matrix

| Control ID | Control Name | Current State | Gap | Remediation | Effort |
|---|---|---|---|---|---|
| AC-2 | Account Management | Service accounts manually managed; no audit trail for creation/deletion | No automated provisioning/deprovisioning; shared passwords; no SOD enforcement | Implement identity & access management (IAM) with role-based provisioning; enforce unique service account credentials; automated deprovisioning on role change | L |
| AC-3 | Access Control | ETL runs with single "etl" OS user; dashboards allow read-all by default | Insufficient privilege separation; no attribute-based access control (ABAC) | Deploy RBAC framework; segment ETL into separate service accounts per data domain; implement dashboard row-level security (RLS) in BI tool | M |
| AC-6 | Least Privilege | Dashboard and ETL service accounts have elevated Postgres permissions | Excessive database privileges; risk of lateral movement if credentials compromised | Audit and restrict Postgres roles to minimum required (read-only for dashboards, INSERT/UPDATE only for ETL on target tables) | S |
| AU-12 | Audit Logging | No centralized audit log collection; Postgres logging disabled; Python ETL writes only to stdout/stderr | No forensic trail for compliance or incident response; cannot detect unauthorized access | Enable Postgres query logging (log_statement='all') and ship logs to centralized SIEM; add structured logging to ETL (JSON format, timestamps, user context); instrument dashboard audit trail in application layer | M |
| CA-7 | Continuous Monitoring | Manual quarterly security reviews; no real-time alerting on anomalous DB queries or file access | No proactive threat detection; compliance gaps discovered post-hoc | Deploy host-based intrusion detection (HIDS) on Postgres server; implement database activity monitoring (DAM); configure file drop DLP alerts on sensitive data patterns (PII, financial data) | M |
| SC-7 | Boundary Protection | Postgres accessible from internal network; shared file drop mounted on all user workstations without encryption | No network segmentation; file drop lacks DLP, encryption, or access logging | Implement network micro-segmentation (firewall rules restrict Postgres to ETL/BI only); encrypt file drop at rest and in transit (SMB encryption); mandate VPN access for all file drop mounts; deploy DLP agent to intercept sensitive data copies | L |
| SC-28 | Protection of Information at Rest | Postgres data stored on unencrypted filesystem; shared file drop lacks encryption | Sensitive analytics (financial, operational metrics) vulnerable if storage compromised | Enable Postgres transparent data encryption (TDE) or full-disk encryption (LUKS) on storage volumes; encrypt shared file drop with AES-256 (NTFS EFS or third-party agent) | M |
| SI-4 | Information System Monitoring | No centralized logging; no alerting on failed ETL jobs or unusual database connection patterns | Cannot detect data pipeline failures or intrusion attempts; compliance reporting manual | Implement centralized log aggregation (ELK/Splunk); configure alerting on ETL job failures, failed DB login attempts, and after-hours access; daily automated compliance dashboard | M |

---

## Detailed Remediation Roadmap

### Phase 1: Critical (Weeks 1–4)
1. **AC-2 / AC-6**: Audit and restrict Postgres service account permissions; enforce unique credentials per ETL component.
2. **SC-7**: Enable Postgres firewall rules; encrypt file drop in transit (SMB signing).
3. **AU-12**: Enable Postgres query logging to file; forward to centralized log store.

### Phase 2: High (Weeks 5–12)
1. **AC-3**: Implement row-level security in BI dashboards; separate ETL service accounts by data lineage.
2. **SC-28**: Deploy disk encryption (LUKS) on Postgres storage volume.
3. **SI-4**: Integrate Postgres audit logs into SIEM; configure alerting rules.

### Phase 3: Medium (Weeks 13–20)
1. **CA-7**: Deploy database activity monitoring (DAM) solution for real-time query inspection.
2. **SC-7**: Implement full DLP on shared file drop; enforce VPN + device compliance for access.

---

## Compliance Mapping

| NIST 800-53 Control Family | Coverage | Status |
|---|---|---|
| Access Control (AC) | AC-2, AC-3, AC-6 | Partial |
| Audit & Accountability (AU) | AU-12 | Deficient |
| Security Assessment & Authorization (CA) | CA-7 | Minimal |
| System & Communications Protection (SC) | SC-7, SC-28 | Partial |
| System & Information Integrity (SI) | SI-4 | Deficient |

---

## References

- NIST SP 800-53 Rev. 5: Security and Privacy Controls for Federal Information Systems
- NIST SP 800-171: Protecting Controlled Unclassified Information in Nonfederal Systems and Organizations
- FedRAMP Security Assessment Framework

---

**Report Prepared By:** Security Engineering Team  
**Next Review Date:** 2025-04-24 (90 days)
