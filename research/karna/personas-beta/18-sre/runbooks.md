# CDC Service Production Runbooks

## Runbook 1: Postgres DB Replication Lag

### Detect
- Monitoring alert: `pg_replication_lag_seconds > 30`
- Grafana dashboard shows standby/read-replica falling behind primary
- Oncall receives PagerDuty alert or checks Datadog metrics

### Diagnose
1. **Check replication status on primary:**
   ```bash
   psql -c "SELECT slot_name, restart_lsn, confirmed_flush_lsn FROM pg_replication_slots;"
   psql -c "SELECT now() - pg_postmaster_start_time() AS uptime, backend_xmin FROM pg_stat_replication;"
   ```
   Look for NULL `confirmed_flush_lsn` (slots not advancing) or missing replication processes.

2. **Check replica/standby health:**
   ```bash
   ssh standby-host "psql -c 'SELECT now() - pg_last_wal_receive_time() AS receive_lag, now() - pg_last_wal_replay_time() AS replay_lag;'"
   ```
   If replay_lag is high, the replica is lagging on applying WAL, not receiving it.

3. **Check network/disk I/O:**
   ```bash
   ssh primary-host "iostat -x 1 5 | grep sda"
   ssh standby-host "vmstat 1 5"
   ```
   High disk I/O wait or network latency confirms infrastructure bottleneck.

### Mitigate
1. If replica is stuck applying WAL: restart replica replication:
   ```bash
   ssh standby-host "systemctl restart postgresql"
   ```
2. If replication slots show backlog: check primary disk space:
   ```bash
   df -h /var/lib/postgresql
   ```
   If full, escalate to senior—data loss risk.
3. If network is the bottleneck: verify WAL archival isn't competing for bandwidth. Check backup logs.
4. Wait 5–10 minutes, re-check lag with `pg_stat_replication`. If lag drops to <5s, declare resolved.
5. If lag persists: failover read traffic to primary temporarily (update app connection strings), escalate to database team.

### Post-Incident
- Capture exact lag timeline from metrics
- Document which check identified root cause
- Check `pg_stat_statements` for slow queries on standby during incident
- Verify backup and archival success for the incident window
- Review replication slot configuration (is it too conservative?)

---

## Runbook 2: Ingest Pipeline Stalls

### Detect
- Monitoring alert: `ingest_messages_processed_total` counter flat for >5 minutes
- SQS queue depth growing (CloudWatch metric > threshold)
- Oncall checks `/healthz` endpoint: returns `pipeline_queue_depth: high`

### Diagnose
1. **Check worker process status:**
   ```bash
   ps aux | grep ingest-worker
   journalctl -u ingest-worker -n 50 --no-pager | tail -20
   ```
   Look for crash logs, OOM kills, or missing processes. Count active workers vs. expected.

2. **Check downstream service availability:**
   ```bash
   curl -s http://postgres-write-service:5432 -o /dev/null -w "%{http_code}"
   curl -s http://event-store:8080/healthz
   ```
   If 500 or timeout, downstream is degraded. Check if CDC is accepting writes.

3. **Check queue depth and deadletter:**
   ```bash
   aws sqs get-queue-attributes --queue-url https://sqs.region.amazonaws.com/account/ingest-queue --attribute-names ApproximateNumberOfMessages,ApproximateNumberOfMessagesNotVisible
   aws sqs get-queue-attributes --queue-url https://sqs.region.amazonaws.com/account/ingest-dlq --attribute-names ApproximateNumberOfMessages
   ```
   Large DLQ means messages are being rejected. High NotVisible means workers grabbed them but stalled.

### Mitigate
1. If workers crashed: restart the fleet:
   ```bash
   kubectl rollout restart deployment/ingest-worker -n production
   kubectl wait --for=condition=available --timeout=300s deployment/ingest-worker -n production
   ```
2. If downstream service is down: page on-call for that service. Meanwhile, drain queue to DLQ (temporary mitigation):
   ```bash
   kubectl scale deployment/ingest-worker --replicas=0 -n production
   ```
3. If DLQ is growing: halt processing, then replay selective batches after issue is fixed.
4. Monitor queue depth to return to baseline (<100 messages).

### Post-Incident
- Export worker pod logs for incident window from CloudWatch
- Check if OOMKilled: `kubectl describe pod <pod>` and adjust memory requests
- Verify downstream CDC write latency (p99) during incident
- Audit DLQ messages: do they show a pattern (e.g., all from one topic)?
- Review worker error budget and retry configuration

---

## Runbook 3: Auth Provider (PIV/Okta) Outage

### Detect
- Monitoring alert: `auth_request_failure_rate > 50%` or `auth_provider_latency_p99 > 10s`
- Oncall sees spike in HTTP 401/403 responses to API endpoints
- Okta/PIV status page reports incident
- User reports: "can't log in"

### Diagnose
1. **Check auth provider status:**
   ```bash
   curl -s https://status.okta.com/api/v2/summary.json | jq '.components[] | {name, status}'
   curl -I https://okta-tenant.okta.com/api/v1/meta/schemas/apps/okta
   ```
   If non-200, provider is down. Check public status page.

2. **Check local auth cache:**
   ```bash
   redis-cli -h auth-cache.internal INFO stats | grep keys
   redis-cli -h auth-cache.internal KEYS "auth_token:*" | wc -l
   ```
   If cache is populated, existing sessions can proceed. If empty, all users are blocked.

3. **Check service logs for auth errors:**
   ```bash
   kubectl logs -l app=api-gateway -n production --tail=100 | grep -i "okta\|auth\|401" | tail -20
   ```
   Look for timeout vs. rejection. Timeout suggests provider unreachable.

### Mitigate
1. If provider is down *and* cache is stale: enable "offline auth" mode (if available):
   ```bash
   kubectl set env deployment/api-gateway -n production AUTH_MODE=offline_cached
   ```
   This allows cached credentials to be used without calling Okta. **Warning: session tokens won't refresh.**

2. If offline mode is unavailable: gracefully degrade:
   ```bash
   kubectl patch service api-gateway -n production -p '{"spec":{"type":"ClusterIP"}}' --type merge
   ```
   Return HTTP 503 to users instead of 401, so they know it's infrastructure.

3. Monitor Okta status. When recovered, restart the service to clear stale cache:
   ```bash
   kubectl rollout restart deployment/api-gateway -n production
   ```
4. Verify `/login` returns 200 and users can authenticate again.

### Post-Incident
- Document exact outage window from provider status page
- Capture auth success/failure rate from metrics during incident
- Review cache TTL: was 5-minute cache sufficient?
- Check if offline auth mode was actually enabled—measure user impact
- Plan: implement circuit breaker for auth provider with faster fallback
- Consider adding local LDAP replica as secondary auth for future outages

