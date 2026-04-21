# Security Audit: New Tools & RAG Subsystem (2026-04-20)

## Executive Summary

**Date:** 2026-04-20  
**Scope:** 4 new tools (browser, database, comms, document) + RAG subsystem  
**Findings:** P0=1, P1=5, P2=4  
**Verdict:** 🔴 **BLOCK** — Do not merge without fixing P0 (SQL injection) and P1s.

---

## Per-File Findings

### 1. browser.py (8569 bytes)

**P1: DNS Rebinding Attack (line 162)**
- `is_safe_url()` validates hostname once at navigate time, but attacker flips DNS after initial check.
- No second DNS lookup after TCP connection established.
- Fix: Add request interception via `page.route()` to re-validate each request's resolved IP.

**P1: Redirect Chain to Metadata (line 167)**
- `page.goto(url)` silently follows 302/301 redirects. Safe URL can redirect to 169.254.169.254.
- Fix: Disable auto-redirect, manually validate each Location header.

**P2: IPv6-Mapped IPv4 Edge Case**
- `::ffff:127.0.0.1` correctly rejected by ipaddress stdlib. Safe but not explicitly normalized.
- No action needed.

---

### 2. database.py (17826 bytes)

**P0: SQL Injection (line 485)**
- `_do_query()` calls `self._connection.execute(sql)` without params tuple.
- Example: `sql="SELECT * FROM users WHERE id='1' OR '1'='1"` returns all users.
- Fix: Add `params` field to schema, extract from kwargs, pass as tuple.

**P1: Credential Leakage (line 440)**
- Exception messages include plaintext passwords from connection strings.
- Example: `Failed to connect: postgres://user:PASSWORD@internal.db/...`
- Fix: Scrub via `scrub_secrets(str(exc))`.

**P1: SSRF via PostgreSQL Host (line 183)**
- No validation of hostname in connection_string parameter.
- Attacker can supply: `connection_string="postgresql://169.254.169.254:5432/metadata"`
- Fix: Extract hostname, validate via `is_safe_url()`.

**P2: DELETE without WHERE in Write Mode**
- Read-only mode blocks mutations. Write mode allows unguarded deletes.
- Design choice; operator can override. Low priority.

---

### 3. comms.py (5624 bytes)

**P2: Message Size Bomb (lines 82-92)**
- No limit on message body size. Agent can write 1 GB message → disk exhaustion.
- Fix: Add `_MAX_MESSAGE_SIZE = 10_000_000` check.

**P2: Identity Trust Assumption**
- Agent identity loaded from config with no privilege check.
- Low risk; design assumption.

---

### 4. document.py (10469 bytes)

**P2: Macro File Extension Bypass**
- `.docm`, `.xlsm` blocked by check, but user can rename to bypass.
- openpyxl won't execute macros anyway (read-only mode).
- Low risk; runtime behavior is safe.

**P2: ZIP Bomb DoS (line 182)**
- `openpyxl.load_workbook()` decompresses entire ZIP into memory.
- 10 MB compressed → 5 GB uncompressed → OOM.
- Mitigated by output truncation (50K chars).

---

### 5. embedder.py + store.py (736 lines)

**P1: Telemetry Claim Misleading**
- README: "Zero telemetry. Nothing phones home, ever."
- Reality: `SentenceTransformerEmbedder` downloads ~384 MB model from Hugging Face on first use.
- Fix: Clarify in README.

**P2: Prompt Injection via Indexed Content**
- RAG retrieves text without sanitization. Malicious doc returns in context.
- Known limitation; document in guidelines.

---

## Ranked Fixes

### P0 (Critical)

**P0-1: SQL Injection (database.py:485)**
- Add `params` field to schema
- Change `execute(sql)` to `execute(sql, tuple(kwargs.get("params", [])))`

### P1 (High)

**P1-1: DNS Rebinding (browser.py:162)**
- Add request interception to re-validate each URL

**P1-2: Redirect Chain (browser.py:167)**
- Disable auto-redirect, manually validate Location headers

**P1-3: Credential Leakage (database.py:440)**
- Scrub exceptions via `scrub_secrets(str(exc))`

**P1-4: SSRF via Host (database.py:183)**
- Extract hostname, validate via `is_safe_url()`

**P1-5: Telemetry Claim (README.md)**
- Clarify that sentence-transformers downloads model on first use

### P2 (Medium)

- Message size limit (comms.py)
- Macro extension check (document.py)
- ZIP bomb documentation (document.py)
- Prompt injection guidelines (store.py)

---

## Test Outline

`tests/test_new_tools_security.py`:

1. `test_database_sql_injection_parameterized()` — params block injection
2. `test_database_no_credentials_in_error()` — scrub passwords
3. `test_database_ssrf_host_blocked()` — reject private IPs
4. `test_browser_dns_rebinding_defended()` — re-validate IP
5. `test_browser_redirect_metadata_blocked()` — block 302 to metadata
6. `test_comms_message_size_limit()` — enforce max size
7. `test_document_path_traversal_blocked()` — is_safe_path works
8. `test_rag_telemetry_documented()` — README accurate

---

## Summary

| Category | Count |
|----------|-------|
| P0 (Critical) | 1 |
| P1 (High) | 5 |
| P2 (Medium) | 4 |
| Files Audited | 6 |
| Est. Fix Time | 3-4 hours |

**Verdict:** 🔴 **BLOCK** until P0+P1 fixed.

---

*Audit: 2026-04-20*
