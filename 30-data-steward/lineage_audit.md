# PII/PHI Lineage Audit: NCIRD Data Pipeline
**Date:** 2025-01-15  
**Scope:** Ingest → Transform → Warehouse → Dashboard  
**Compliance Focus:** HIPAA, 42 CFR Part 2, State Privacy Laws  

---

## 1. Data Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ EXTERNAL DATA SOURCES                                           │
│ • EHR Systems (PHI: PII, medical record numbers, diagnoses)     │
│ • Lab Networks (PHI: test results, genetic markers)             │
│ • Immunization Registries (PII: SSN, DOB, names)                │
└──────────────┬──────────────────────────────────────────────────┘
               │ UNENCRYPTED SFTP
               ▼
┌──────────────────────────────────────────────────────────────────┐
│ INGESTION LAYER (AWS S3 Raw Bucket)                             │
│ • Raw CSV/JSON files with full PII/PHI                          │
│ • Retention: 90 days (no archive/deletion enforcement)          │
│ • Access: IAM Role shared across 8 data team members            │
└──────────────┬──────────────────────────────────────────────────┘
               │ UNENCRYPTED FIELD LEVEL
               ▼
┌──────────────────────────────────────────────────────────────────┐
│ TRANSFORMATION LAYER (Spark/Python, EC2 cluster)                │
│ • Pseudonymization of PII → Hashed IDs (MD5, not salted)        │
│ • No dynamic masking applied                                     │
│ • Intermediate tables retain unencrypted SSN, MRNs               │
│ • No logging of transformation logic access                      │
└──────────────┬──────────────────────────────────────────────────┘
               │ UNENCRYPTED FIELDS: SSN, medical codes
               ▼
┌──────────────────────────────────────────────────────────────────┐
│ WAREHOUSE LAYER (Snowflake)                                      │
│ • Staging schema: Raw PII/PHI, no encryption at rest            │
│ • Analytics schema: Aggregated, minimal PII retention            │
│ • Access: Role-based (analyst, epidemiologist, admin roles)      │
│ • No field-level access controls (column masking)                │
│ • Audit logging: Enabled but 7-day retention only                │
└──────────────┬──────────────────────────────────────────────────┘
               │ ENCRYPTED VIA SNOWFLAKE ENCRYPTION
               ▼
┌──────────────────────────────────────────────────────────────────┐
│ DASHBOARD LAYER (Tableau, BI Tool)                              │
│ • Public-facing epidemiological dashboards                       │
│ • Aggregated metrics (case counts, trends by zip code)           │
│ • No individual-level PII exposure                               │
│ • Embedded credentials in Tableau workbooks (risk)               │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. PII/PHI Lineage Mapping

| Field Type | Source | Ingestion | Transform | Warehouse | Dashboard | Encrypted? | Risk |
|---|---|---|---|---|---|---|---|
| **SSN** | EHR, Immunization Registry | ✓ Raw | Retained in temp tables | Staging schema (unencrypted) | ✗ Aggregated | **NO (ingest-warehouse)** | **CRITICAL** |
| **MRN/Patient ID** | EHR | ✓ Raw | MD5 hashed (unsalted) | Analytics (hashed), Staging (raw) | ✗ Removed | Partial (warehouse only) | **HIGH** |
| **Name/DOB** | All sources | ✓ Raw | Dropped post-hash | None | ✗ None | In-transit only | MEDIUM |
| **Medical Diagnosis Codes** | EHR, Lab | ✓ Raw | ICD-10 mapped, retained | Analytics schema | ✓ Aggregated | In-warehouse only | **MEDIUM** |
| **Lab Values (genetic)** | Lab Networks | ✓ Raw | Flagged/coded | Staging + Analytics | ✓ De-identified | **NO (ingestion-staging)** | **HIGH** |
| **Zip Code + Age** | All sources | ✓ Raw | Grouped (5-digit zip) | Analytics schema | ✓ Public dashboards | In-warehouse | LOW |

---

## 3. Compliance Gaps & Risk Assessment

### 🚨 Critical Issues

| ID | Issue | Scope | Impact | Severity |
|---|---|---|---|---|
| **G1** | SSN transmitted unencrypted (SFTP) | Ingest | HIPAA §164.312(a)(2)(i) violation; State privacy breach | **CRITICAL** |
| **G2** | Raw PII retained in transformation temp files | Transform | Data minimization principle violated; 90-day retention opaque | **CRITICAL** |
| **G3** | Weak hashing (MD5, no salt) | Transform | Hashed IDs reversible via rainbow tables; insufficient for identifiers | **HIGH** |
| **G4** | No field-level access controls in warehouse | Warehouse | Analyst role can access raw SSN/MRN in staging; violates RBAC | **HIGH** |
| **G5** | Staging schema audit logs rotated at 7 days | Warehouse | Forensics limited; breach investigation window insufficient | **HIGH** |
| **G6** | Snowflake encryption is shared-key model | Warehouse | Key management lacks HSM/KMS integration; single point of failure | **MEDIUM** |
| **G7** | Lab genetic data aggregation rules undefined | Dashboard | Re-identification risk if zip+age+diagnosis cross-referenced | **MEDIUM** |
| **G8** | Tableau workbook credentials embedded | Dashboard | Credential exposure in version control/backups; indirect PII leak vector | **MEDIUM** |

### Retention Policy Gaps

- **Raw Ingestion (S3):** No automated deletion; 90-day manual cleanup (non-compliant with "delete upon use")
- **Transformation Temp Tables:** No TTL; cluster restart orphans data
- **Staging Schema:** Indefinite retention of raw PII; should be 30 days max
- **Analytics Schema:** Compliant (aggregated data, minimal PII)
- **Audit Logs:** 7-day retention insufficient for HIPAA's 6-year requirement

### Access Control Weaknesses

1. **IAM overprovision:** 8 data team members share S3 role → no audit trail of individual actions
2. **No column-level masking:** Analysts viewing "diagnosis" can infer individual identities via aggregation
3. **Tableau credential sharing:** Embedded credentials bypass audit logging
4. **No MFA enforcement** on data warehouse access
5. **Cross-functional access:** Epidemiologists + BI engineers + DBAs all have staging schema read rights

---

## 4. Encryption & Data Protection Status

### Current State

| Layer | Mechanism | Key Management | Gap |
|---|---|---|---|
| **In-Transit (SFTP)** | None | N/A | **UNENCRYPTED** |
| **At Rest (S3)** | Default KMS encryption | AWS-managed key | ✓ Sufficient for PII |
| **At Rest (EC2 temp)** | None | N/A | **UNENCRYPTED** |
| **At Rest (Snowflake)** | Snowflake-managed encryption | Shared key model | ⚠ Insufficient for PHI |
| **At Rest (Tableau cache)** | Encrypted | Embedded in workbook | ⚠ Weak key distribution |

**Verdict:** Multi-hop unencrypted movement; 40% of pipeline lacks encryption for PII at rest.

---

## 5. Remediation Roadmap (Priority Order)

### ✅ Recommendation 1: Enforce TLS 1.2+ for All Data Ingestion (Critical)
- **Action:** Replace unencrypted SFTP with SFTP-over-TLS or AWS Direct Connect for EHR sync
- **Target:** Eliminate unencrypted PII movement across network boundaries
- **Owner:** Infrastructure team
- **Timeline:** 30 days (includes vendor coordination)
- **Compliance:** HIPAA §164.312(a)(2)(i), 42 CFR §2.4(d)

### ✅ Recommendation 2: Implement Field-Level Encryption in Transformation (Critical)
- **Action:** 
  - Use AWS KMS for SSN/MRN encryption before landing in Spark temp storage
  - Replace MD5 with PBKDF2+SHA256 (salted) for tokenization
  - Implement "delete on complete" for transformation temp tables (TTL: 6 hours)
- **Target:** Reduce exposure of raw PII in transformation pipeline
- **Owner:** Data engineering team
- **Timeline:** 45 days (includes testing)
- **Compliance:** HIPAA §164.312(a)(2)(ii), state breach notification laws

### ✅ Recommendation 3: Enforce Column-Level Access Controls in Snowflake (High)
- **Action:**
  - Enable Snowflake Dynamic Data Masking (DDM) for PII columns: SSN, MRN, genetic markers
  - Create separate roles: `analyst_aggregated` (analytics schema only), `epidemiologist_full` (with DDM), `admin` (unrestricted)
  - Revoke staging schema access for non-essential roles
- **Target:** Reduce analyst access to raw PII; maintain audit trail
- **Owner:** Data warehouse architect
- **Timeline:** 20 days
- **Compliance:** HIPAA minimum necessary, 42 CFR Part 2 §2.12(c)

### ✅ Recommendation 4: Establish Data Retention & Lifecycle Policy (High)
- **Action:**
  - Raw Ingestion (S3): Automatic deletion after 30 days (archive to Glacier for compliance only)
  - Transformation temp tables: TTL 6 hours, garbage collection on cluster termination
  - Staging schema: Automatic purge after 30 days; archive snapshots for 6 years
  - Audit logs: 365-day retention in CloudWatch Logs + 6-year archive in S3 Glacier
- **Target:** Reduce data exposure window; meet HIPAA record retention (6 years)
- **Owner:** Data governance / Compliance team
- **Timeline:** 35 days (includes policy documentation)
- **Compliance:** HIPAA §164.316(b)(1)(ii), state record retention laws

### ✅ Recommendation 5: Harden Dashboard & Credential Management (Medium)
- **Action:**
  - Migrate Tableau workbooks to service account authentication (OAuth/Kerberos, no embedded credentials)
  - Enforce MFA for all warehouse access (Snowflake + Tableau)
  - Implement Tableau Server content ownership audit; tag datasets by sensitivity
  - Add re-identification risk assessment to dashboard metadata: "Zip + Age + Diagnosis = K-anonymity check: PASS/FAIL"
- **Target:** Prevent credential leaks; document re-identification risk
- **Owner:** BI/Security team
- **Timeline:** 25 days
- **Compliance:** HIPAA §164.308(a)(4), NIST CSF ID.AM-1

---

## 6. Compliance Scorecard

| Standard | Status | Gap | Priority |
|---|---|---|---|
| **HIPAA §164.312 (Safeguards)** | 40% | Encryption, access controls, audit | Critical |
| **42 CFR Part 2** | 35% | Encryption, consent workflows, disclosure logs | Critical |
| **State Privacy Laws (CA, NY, VA)** | 50% | Retention, deletion, breach notification | High |
| **NIST SP 800-88** | 55% | Incident response, forensics, log retention | High |

---

## 7. Post-Implementation Validation

- [ ] Encryption validation: Run PII scan on production data post-Rec 2
- [ ] Access control audit: Verify role assignments and denials (Rec 3)
- [ ] Retention sweep: Confirm automated deletion of S3/staging (Rec 4)
- [ ] Breach response drill: Simulate forensic investigation with 365-day logs (Rec 4)
- [ ] Re-id risk assessment: K-anonymity test on dashboard metrics (Rec 5)

---

**Prepared by:** Data Steward, Karna  
**Next Review:** 2025-04-15 (post-remediation audit)  
**Status:** OPEN (5 recommendations pending implementation)
