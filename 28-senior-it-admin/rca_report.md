# Root Cause Analysis: FISMA Log-Shipper Dropout Incident
**Incident ID:** INC-2024-08342  
**Date of Incident:** Last Tuesday, 22:00–02:00 UTC  
**Report Date:** Current Date  
**Author:** Senior IT Admin (L2/L3)  
**Severity:** P2 (High) — Compliance Logging Gap

---

## Executive Summary
The FISMA-compliant log-shipping pipeline experienced a 4-hour service degradation on Tuesday 22:00-02:00 UTC, resulting in complete log ingestion loss across the audit cluster. Root cause: unplanned memory exhaustion on the primary log-forwarder node (logship-prod-01) triggered by a runaway buffer pool allocator in Fluent Bit v1.8.3, exacerbated by missing alert thresholds on heap memory utilization.

---

## Incident Timeline

| Time (UTC) | Event |
|-----------|-------|
| 21:47 | Scheduled log rotation begins on logship-prod-01; buffer pool size increases to 1.2GB |
| 22:03 | First memory spike detected (manual check only; no alert fired); process reaches 85% heap |
| 22:15 | OOMKiller activates on logship-prod-01; Fluent Bit process terminated |
| 22:17 | Failover to secondary shipper (logship-prod-02) initiated by systemd restart policy |
| 22:18–23:44 | Secondary node handles load; primary node in restart loop (crash → restart → crash) |
| 23:45 | Monitoring team reviews dashboards; discovers zero log ingestion to SIEM since 22:15 |
| 23:52 | Incident declared; L3 on-call engaged |
| 00:15 | Root cause identified: buffer pool leak in Fluent Bit 1.8.3 release notes |
| 00:47 | Fluent Bit upgraded to 1.8.4 on both nodes |
| 01:15 | Log ingestion resumes; backpressure normalizes |
| 02:00 | All systems nominal; buffer pool stable at 240MB; alert thresholds configured |

---

## Root Cause Analysis

### Primary Cause: Buffer Pool Memory Leak
Fluent Bit v1.8.3 introduced a defect in the `io_buffer_pool.c` allocator that fails to return pre-allocated buffers to the pool during high-throughput log rotation events. Each buffer (512KB) remains orphaned in the heap until process exhaustion.

**Evidence:**
- Heap memory growth pattern: linear increase of ~50MB/min after 21:47 log rotation
- Process memory at incident time: 1.84GB (container limit: 2GB)
- Fluent Bit 1.8.3 release notes (fetched 00:15): "Known Issue—Buffer pool leak during concurrent writes; fixed in 1.8.4"
- Post-upgrade memory profile: stable at 240MB after 60 minutes

### Contributing Factors

1. **Missing Memory Utilization Alerts**
   - No threshold monitoring for Fluent Bit heap usage >80%
   - Escalation should have triggered at ~1.6GB
   - Gap time: 28 minutes between spike and human detection

2. **Insufficient Version Validation**
   - 1.8.3 deployed 3 weeks ago without heap-stress testing in pre-prod
   - No staged rollout; directly to production (2 nodes)
   - Change ticket failed to reference release notes

3. **Restart Loop Prevention Not Configured**
   - No `StartLimitBurst=2` / `StartLimitIntervalSec=300` in systemd unit
   - Process restarted 47 times in 87 minutes instead of failing fast to secondary

---

## Supporting Evidence

### Artifact 1: Memory Usage Graph (Reconstructed)
```
Heap Utilization (logship-prod-01, 21:47–02:00 UTC):
22:00 │                                          ╱╲
      │                                         ╱  ╲
22:30 │                              ╱╱╱╱╱╱╱╱╱    ╲
      │                         ╱╱╱╱╱                ╲
23:00 │                    ╱╱╱╱╱                      ╲___OOMKill
      │               ╱╱╱╱╱                               │
23:30 │          ╱╱╱╱╱                                    │ (restart loop)
      │     ╱╱╱╱╱                                         │
00:00 │╱╱╱╱╱                                              ╲___upgrade to 1.8.4
      │                                                    ╲
00:30 │                                                     ╲___stable
      └─────────────────────────────────────────────────────────
        Memory: 240MB → 1.84GB peak
```

### Artifact 2: systemd Journal Extract (logship-prod-01)
```
Oct 10 22:03:45 logship-prod-01 fluent-bit[4521]: [warn] 
  Buffer pool utilization: 78% (1596MB/2GB)
Oct 10 22:15:33 logship-prod-01 kernel: Out of memory: 
  Kill process 4521 (fluent-bit) score 823 or sacrifice child
Oct 10 22:15:34 logship-prod-01 systemd[1]: fluent-bit.service: 
  Main process exited, code=killed, status=9/KILL
Oct 10 22:15:35 logship-prod-01 systemd[1]: fluent-bit.service: 
  Unit entered failed state. Automatic restart in 3 sec.
Oct 10 22:15:38 logship-prod-01 systemd[1]: Started Fluent Bit log forwarder.
Oct 10 22:15:39 logship-prod-01 fluent-bit[4587]: [error] 
  Cannot allocate memory (buffer pool prealloc failed)
  (repeated 47 times until 23:44)
```

### Artifact 3: Version and Deployment Data
- **Current Prod Version:** 1.8.3 (deployed 3 weeks ago, Sept 19)
- **Fixed Version:** 1.8.4 (released Sept 29, in security bulletin S-2024-1847)
- **Change Ticket:** CHG-198432 (missing link to release notes; no pre-prod validation documented)
- **Deployment Method:** Direct push; no canary period

### Artifact 4: Configuration Gaps (Observed at 00:52)
- **Missing in /etc/systemd/system/fluent-bit.service:**
  ```
  # NOT present:
  StartLimitBurst=2
  StartLimitIntervalSec=300
  # Should fail cleanly after 2 restarts in 300 sec, not loop 47x
  ```
- **Missing in Prometheus scrape config:**
  ```
  # NOT present:
  - alert: FluentBitHeapUsage
    expr: fluent_bit_output_errors_total > 1000
    # No memory-specific alert
  ```

---

## Corrective Actions

### Immediate (Completed as of 02:00 UTC)
1. ✅ **Upgraded Fluent Bit** from 1.8.3 → 1.8.4 on logship-prod-01 and logship-prod-02
2. ✅ **Restarted services** and confirmed buffer pool stability (240MB steady-state)
3. ✅ **Verified log backlog recovery** — all logs from 22:15–02:00 re-ingested from secondary queue

### Short-term (48–72 hours)
1. **Add Prometheus alerting:**
   - Alert on `fluent_bit_mem_usage > 1.6GB` (threshold 80% of 2GB limit)
   - Alert on `fluent_bit_restart_count > 5` (in 10min window)
   - Severity: P3 (warning); escalate to P2 if >1.8GB

2. **Configure systemd safety limiters:**
   - Add `StartLimitBurst=2` and `StartLimitIntervalSec=300` to systemd unit
   - Fail-fast prevents restart loops; manual remediation required

3. **Backtest 1.8.4 in pre-prod:**
   - Run 72-hour soak test with production traffic replay
   - Validate memory stability under log rotation events

### Medium-term (2 weeks)
1. **Implement staged rollout policy:**
   - Canary: 1 node, 24-hour observation
   - Then: secondary node, 24-hour observation
   - Then: primary nodes

2. **Enhance change management:**
   - Link change tickets to upstream release notes
   - Require security bulletin review for minor version bumps
   - Mandate pre-prod stress testing for components in critical path

3. **Compliance audit:**
   - Calculate SLA impact: 4-hour logging gap violates FISMA continuous monitoring requirement
   - Document mitigation in audit trail (secondary shipper captured tail after 22:17)
   - File report with compliance team for Q4 audit

### Long-term (1 month)
1. **Upgrade to Fluent Bit 2.0+** (when stable; has improved memory pooling)
2. **Deploy memory profiling in pre-prod CI/CD** — automatic regression detection on heap growth
3. **Evaluate log-forwarder redundancy** — consider 3-node cluster with quorum instead of 2-node active-passive

---

## Impact Assessment

| Dimension | Details |
|-----------|---------|
| **Logs Lost** | 0 bytes (secondary shipper buffered incoming logs during primary downtime) |
| **Logs Delayed** | ~2.1M events (245MB) — re-ingested within 90 minutes post-upgrade |
| **FISMA Gap** | 4 hours audit log delivery latency; within SLA window but flagged for compliance |
| **MTTR** | 73 minutes (detection: 23 min, diagnosis: 32 min, mitigation: 18 min) |
| **User Impact** | None — FISMA monitoring is internal infrastructure; no customer-facing services affected |

---

## Lessons Learned

1. **Version pinning ≠ safety:** Even minor version bumps can introduce regressions; test upstream changes before production deployment.
2. **Alerts close gaps faster than logs:** 28-minute detection delay was primary MTTR driver; memory-threshold alerts would have halved incident duration.
3. **Restart loops mask failures:** Systemd auto-restart policies need circuit-breaker logic; 47 restart cycles obscured the root issue for external observers.

---

## Sign-Off

- **Investigated by:** Senior IT Admin (L2/L3)
- **Reviewed by:** Infrastructure Team Lead
- **Approved by:** Security & Compliance Officer
- **Date:** Current Date
- **Status:** Closed (corrective actions tracked via tickets INC-2024-08342-CA-001 through -005)

---

## Appendix: Ticket References

- **INC-2024-08342:** Primary incident ticket
- **CHG-202847:** Fluent Bit 1.8.4 upgrade (completed)
- **INC-2024-08342-CA-001:** Add Prometheus memory/restart alerts
- **INC-2024-08342-CA-002:** Configure systemd circuit-breaker limits
- **INC-2024-08342-CA-003:** Staged rollout policy documentation
- **INC-2024-08342-CA-004:** Pre-prod soak test (1.8.4 validation)
- **INC-2024-08342-CA-005:** FISMA compliance gap report

