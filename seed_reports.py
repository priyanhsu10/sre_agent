#!/usr/bin/env python3
"""
Seed script: generates 10 diverse RCA reports and saves them to the database.
Run once to populate the dashboard with realistic sample data.
"""

import sys
from datetime import datetime, timedelta
from database.service import ReportDatabaseService
from database.models import get_session, InvestigationError
from models.report import RCAReport, InvestigationStep, CodeChange, LogEvidence, PossibleFix, RuledOutCategory
from models.hypothesis import FailureCategory, ConfidenceLevel
from models.tool_result import ToolName

db = ReportDatabaseService()

# ── Helpers ──────────────────────────────────────────────────────────────────

def dt(days_ago: float, hour: int = 10, minute: int = 0) -> datetime:
    base = datetime.utcnow() - timedelta(days=days_ago)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)

def step(n, reasoning, decision, tool=None, result=None):
    return InvestigationStep(
        step_number=n,
        reasoning=reasoning,
        decision=decision,
        tool_called=tool,
        result_summary=result,
        timestamp=datetime.utcnow()
    )

def fix(p, action, rationale, impact):
    return PossibleFix(priority=p, action=action, rationale=rationale, estimated_impact=impact)

def ruled_out(cat, reason, evidence):
    return RuledOutCategory(category=cat, reason=reason, evidence=evidence)

# ── Report Definitions ───────────────────────────────────────────────────────

reports = [

    # 1 — payment-service: DB connection pool exhausted (CRITICAL, prod)
    RCAReport(
        report_id="rca-payment-service-seed-001",
        generated_at=dt(0.1),
        app_name="payment-service",
        alert_time=dt(0.2, 14, 32),
        severity="critical",
        environment="prod",
        root_cause="Database connection pool exhausted. Max 100 connections reached. Recent commit increased transaction retry logic causing connections to be held longer than expected.",
        root_cause_category=FailureCategory.DB_CONNECTIVITY,
        confidence_level=ConfidenceLevel.CONFIRMED,
        is_code_change=True,
        initial_hypotheses=[
            "db_connectivity (91.50%)",
            "network_intra_service (35.00%)",
            "code_logic_error (28.00%)"
        ],
        investigation_steps=[
            step(1, "Top hypothesis is db_connectivity at 91.5%. Must check Loki logs first to confirm DB errors.", "CALL_TOOL", ToolName.LOKI, "Found 143 'connection pool exhausted' errors in 15 min window. Max pool size: 100."),
            step(2, "Loki confirmed DB pool exhaustion. Checking recent commits for connection handling changes.", "CALL_TOOL", ToolName.GIT_BLAME, "Found commit f3a8b1c: 'Increase transaction retry count to 5' by Alice Chen (3 days ago). Files: src/db/pool.py, src/payments/processor.py"),
            step(3, "Commit touches retry logic — high risk. Fetching Jira ticket for context.", "CALL_TOOL", ToolName.JIRA, "PAY-2341: Increase retry resilience for transient DB errors. Status: Merged. Labels: database, resilience."),
            step(4, "Evidence conclusive: retry increase holds connections 5x longer under load. Root cause confirmed.", "DONE"),
        ],
        code_changes=[
            CodeChange(commit_hash="f3a8b1c", author="Alice Chen", timestamp=dt(3, 11, 0),
                       message="Increase transaction retry count to 5 for resilience - PAY-2341",
                       files_changed=["src/db/pool.py", "src/payments/processor.py"],
                       jira_ticket="PAY-2341", risk_flags=["connection_pool_impact", "high_load_path"])
        ],
        log_evidence=LogEvidence(
            correlation_id="corr-a1b2c3", evidence_path="correlation_id",
            stack_traces=["org.hibernate.exception.JDBCConnectionException: Unable to acquire JDBC Connection"],
            key_log_lines=["ERROR: connection pool exhausted (100/100 used)", "WARN: getConnection() timed out after 5000ms"],
            slow_queries=["SELECT * FROM transactions WHERE status='pending' -- 4231ms"],
            total_error_count=143
        ),
        ruled_out_categories=[
            ruled_out(FailureCategory.DNS_FAILURE, "DNS queries resolving normally", "Loki shows no DNS error logs"),
            ruled_out(FailureCategory.CERTIFICATE_EXPIRY, "TLS handshakes succeeding", "No cert-related errors in logs"),
        ],
        possible_fixes=[
            fix(1, "Revert commit f3a8b1c to restore retry count to 1", "Directly eliminates root cause", "Immediate resolution of pool exhaustion"),
            fix(2, "Increase connection pool max-size to 200 as short-term relief", "Buys time while fix is validated", "Reduces exhaustion frequency"),
            fix(3, "Implement exponential backoff with jitter on retries", "Prevents thundering herd on DB", "Long-term resilience improvement"),
            fix(4, "Add pool utilisation alert at 80% threshold", "Early warning before exhaustion", "Proactive incident prevention"),
        ]
    ),

    # 2 — user-service: OOM / memory exhaustion (HIGH, prod)
    RCAReport(
        report_id="rca-user-service-seed-002",
        generated_at=dt(0.3),
        app_name="user-service",
        alert_time=dt(0.4, 9, 15),
        severity="high",
        environment="prod",
        root_cause="Java heap space exhausted due to unbounded cache growth. The in-memory session cache has no eviction policy — sessions accumulate until OOM.",
        root_cause_category=FailureCategory.MEMORY_RESOURCE_EXHAUSTION,
        confidence_level=ConfidenceLevel.CONFIRMED,
        is_code_change=False,
        initial_hypotheses=[
            "memory_resource_exhaustion (88.00%)",
            "code_logic_error (42.00%)",
            "db_connectivity (18.00%)"
        ],
        investigation_steps=[
            step(1, "Error messages contain OutOfMemoryError. Top hypothesis is memory exhaustion (88%). Checking Loki.", "CALL_TOOL", ToolName.LOKI, "GC overhead limit exceeded. Heap usage at 98.7%. 14 OOM events in 30 min. Session cache has 1.2M entries."),
            step(2, "Loki shows session cache bloat. Checking git for any cache config changes.", "CALL_TOOL", ToolName.GIT_BLAME, "No recent commits to cache config. Last change 45 days ago by Bob Smith."),
            step(3, "No recent code changes. This is a configuration/growth issue, not a deploy regression.", "DONE"),
        ],
        code_changes=[],
        log_evidence=LogEvidence(
            correlation_id=None, evidence_path="fingerprint_query",
            stack_traces=["java.lang.OutOfMemoryError: Java heap space\n  at java.util.HashMap.resize(HashMap.java:704)"],
            key_log_lines=["FATAL: JVM heap exhausted. GC cannot reclaim memory.", "WARN: session cache size = 1,247,891 entries"],
            slow_queries=[],
            total_error_count=14
        ),
        ruled_out_categories=[
            ruled_out(FailureCategory.DB_CONNECTIVITY, "DB connections healthy", "No connection errors in logs"),
            ruled_out(FailureCategory.DNS_FAILURE, "No DNS resolution errors", "DNS queries succeeding"),
        ],
        possible_fixes=[
            fix(1, "Restart user-service pods to recover heap immediately", "Clears in-memory cache", "Immediate service recovery"),
            fix(2, "Add LRU eviction policy with max 50,000 entries to session cache", "Prevents unbounded growth", "Permanent resolution"),
            fix(3, "Increase JVM heap from 2GB to 4GB as temporary measure", "Delays OOM while fix is deployed", "Short-term stability"),
            fix(4, "Add heap usage alert at 75% threshold", "Early warning of memory pressure", "Proactive monitoring"),
        ]
    ),

    # 3 — api-gateway: DNS failure (CRITICAL, prod)
    RCAReport(
        report_id="rca-api-gateway-seed-003",
        generated_at=dt(1.0),
        app_name="api-gateway",
        alert_time=dt(1.1, 16, 45),
        severity="critical",
        environment="prod",
        root_cause="Internal DNS resolution failing for service mesh entries. CoreDNS pod crashed due to OOM, causing all service-to-service calls to fail with NXDOMAIN.",
        root_cause_category=FailureCategory.DNS_FAILURE,
        confidence_level=ConfidenceLevel.CONFIRMED,
        is_code_change=False,
        initial_hypotheses=[
            "dns_failure (87.30%)",
            "network_intra_service (55.00%)",
            "dependency_failure (31.00%)"
        ],
        investigation_steps=[
            step(1, "Errors contain 'Name or service not known' and 'NXDOMAIN'. DNS failure hypothesis at 87.3%.", "CALL_TOOL", ToolName.LOKI, "220 DNS resolution failures/min. All internal service hostnames failing. CoreDNS pod shows OOM kill in kube-system namespace."),
            step(2, "CoreDNS OOM is the cause. Checking if any recent infra change triggered this.", "CALL_TOOL", ToolName.GIT_BLAME, "No application code changes. Infrastructure team deployed CoreDNS config 6 hours ago."),
            step(3, "DNS root cause confirmed. CoreDNS needs restart and resource limits review.", "DONE"),
        ],
        code_changes=[],
        log_evidence=LogEvidence(
            correlation_id="corr-dns-001", evidence_path="correlation_id",
            stack_traces=["java.net.UnknownHostException: payment-service.svc.cluster.local: Name or service not known"],
            key_log_lines=["ERROR: DNS lookup failed: payment-service.svc.cluster.local NXDOMAIN", "WARN: Upstream DNS timeout after 5000ms"],
            slow_queries=[],
            total_error_count=220
        ),
        ruled_out_categories=[
            ruled_out(FailureCategory.DB_CONNECTIVITY, "DB hosts resolve correctly via IP", "Direct IP connectivity confirmed"),
            ruled_out(FailureCategory.CERTIFICATE_EXPIRY, "Cert expiry not related to DNS failure", "TLS certs valid for 87 more days"),
        ],
        possible_fixes=[
            fix(1, "kubectl rollout restart deployment/coredns -n kube-system", "Restores DNS resolution immediately", "Immediate service restoration"),
            fix(2, "Increase CoreDNS memory limit from 128Mi to 512Mi", "Prevents future OOM kills", "Permanent stability fix"),
            fix(3, "Add CoreDNS pod restart alert and PodDisruptionBudget", "Ensures CoreDNS HA", "Resilience improvement"),
        ]
    ),

    # 4 — order-service: Code logic error / NPE (HIGH, staging)
    RCAReport(
        report_id="rca-order-service-seed-004",
        generated_at=dt(1.5),
        app_name="order-service",
        alert_time=dt(1.6, 11, 30),
        severity="high",
        environment="staging",
        root_cause="NullPointerException in order validation when optional promo_code field is absent. Recent refactor removed null-check guard in OrderValidator.validate().",
        root_cause_category=FailureCategory.CODE_LOGIC_ERROR,
        confidence_level=ConfidenceLevel.CONFIRMED,
        is_code_change=True,
        initial_hypotheses=[
            "code_logic_error (79.00%)",
            "config_drift (33.00%)",
            "db_connectivity (21.00%)"
        ],
        investigation_steps=[
            step(1, "NullPointerException with clear stack trace pointing to OrderValidator. Code error hypothesis at 79%.", "CALL_TOOL", ToolName.LOKI, "Stack trace: NullPointerException at OrderValidator.validate():L47. Occurs on all orders without promo_code."),
            step(2, "Line 47 of OrderValidator is the issue. Checking git blame for recent changes.", "CALL_TOOL", ToolName.GIT_BLAME, "Commit d9e7f2a: 'Refactor order validation pipeline' by Carol Wang (yesterday). Removed null-guard on promo_code field."),
            step(3, "Commit d9e7f2a removed the null check. Fetching Jira context.", "CALL_TOOL", ToolName.JIRA, "ORD-891: Refactor validation pipeline for performance. Status: Merged. Missing: promo_code null safety."),
            step(4, "Root cause confirmed — missing null check introduced in ORD-891 refactor.", "DONE"),
        ],
        code_changes=[
            CodeChange(commit_hash="d9e7f2a", author="Carol Wang", timestamp=dt(1, 15, 0),
                       message="Refactor order validation pipeline for performance - ORD-891",
                       files_changed=["src/validation/OrderValidator.java", "src/validation/RuleEngine.java"],
                       jira_ticket="ORD-891", risk_flags=["removed_null_check", "no_test_coverage"])
        ],
        log_evidence=LogEvidence(
            correlation_id="corr-b2c3d4", evidence_path="correlation_id",
            stack_traces=["java.lang.NullPointerException\n  at com.shop.order.OrderValidator.validate(OrderValidator.java:47)\n  at com.shop.order.OrderService.createOrder(OrderService.java:112)"],
            key_log_lines=["ERROR: Validation failed: promo_code is null", "ERROR: 500 Internal Server Error on POST /orders"],
            slow_queries=[],
            total_error_count=67
        ),
        ruled_out_categories=[
            ruled_out(FailureCategory.DB_CONNECTIVITY, "DB healthy, error occurs before DB call", "Stack trace shows error in validation layer"),
            ruled_out(FailureCategory.CONFIG_DRIFT, "Config unchanged", "No config changes in last 7 days"),
        ],
        possible_fixes=[
            fix(1, "Revert commit d9e7f2a immediately", "Restores working null-check", "Immediate error resolution"),
            fix(2, "Add null/empty guard: if (order.getPromoCode() != null) before line 47", "Targeted fix", "Safe permanent fix"),
            fix(3, "Add unit test for orders without promo_code", "Catches regression in CI", "Prevention of recurrence"),
        ]
    ),

    # 5 — notification-service: Dependency failure (MEDIUM, prod)
    RCAReport(
        report_id="rca-notification-service-seed-005",
        generated_at=dt(2.0),
        app_name="notification-service",
        alert_time=dt(2.1, 8, 0),
        severity="medium",
        environment="prod",
        root_cause="SendGrid API returning 503 Service Unavailable. Third-party email delivery degraded — confirmed via SendGrid status page. No action needed on our side.",
        root_cause_category=FailureCategory.DEPENDENCY_FAILURE,
        confidence_level=ConfidenceLevel.HIGH,
        is_code_change=False,
        initial_hypotheses=[
            "dependency_failure (76.00%)",
            "network_intra_service (44.00%)",
            "config_drift (22.00%)"
        ],
        investigation_steps=[
            step(1, "HTTP 503 errors pointing to external API. Dependency failure hypothesis at 76%.", "CALL_TOOL", ToolName.LOKI, "SendGrid API returning 503. Rate: 340 failures/hr. Our API key and config unchanged. No internal errors."),
            step(2, "No code changes. This is an external provider outage.", "CALL_TOOL", ToolName.GIT_BLAME, "No recent commits to notification-service. Last change 12 days ago."),
            step(3, "Confirmed third-party outage. No internal action required beyond monitoring.", "DONE"),
        ],
        code_changes=[],
        log_evidence=LogEvidence(
            correlation_id="corr-c3d4e5", evidence_path="correlation_id",
            stack_traces=[],
            key_log_lines=["ERROR: SendGrid API 503 Service Unavailable", "WARN: Email delivery queued for retry (attempt 3/5)"],
            slow_queries=[],
            total_error_count=340
        ),
        ruled_out_categories=[
            ruled_out(FailureCategory.CONFIG_DRIFT, "API key valid, config unchanged", "SendGrid auth succeeds before 503"),
            ruled_out(FailureCategory.CODE_LOGIC_ERROR, "No code changes, error is from external service", "Stack trace shows 503 from SendGrid endpoint"),
        ],
        possible_fixes=[
            fix(1, "Monitor SendGrid status page and wait for recovery", "External outage — no internal fix needed", "Recovery when SendGrid restores service"),
            fix(2, "Enable retry queue with exponential backoff", "Emails will deliver once provider recovers", "Automatic email catch-up"),
            fix(3, "Add fallback SMTP provider (AWS SES) for critical notifications", "Reduces dependency on single provider", "Long-term resilience"),
        ]
    ),

    # 6 — auth-service: Certificate expiry (CRITICAL, prod)
    RCAReport(
        report_id="rca-auth-service-seed-006",
        generated_at=dt(3.0),
        app_name="auth-service",
        alert_time=dt(3.1, 2, 0),
        severity="critical",
        environment="prod",
        root_cause="TLS certificate for auth.internal.company.com expired at 02:00 UTC. Auto-renewal via cert-manager failed due to misconfigured ClusterIssuer ACME challenge.",
        root_cause_category=FailureCategory.CERTIFICATE_EXPIRY,
        confidence_level=ConfidenceLevel.CONFIRMED,
        is_code_change=False,
        initial_hypotheses=[
            "certificate_expiry (93.00%)",
            "dns_failure (28.00%)",
            "network_intra_service (15.00%)"
        ],
        investigation_steps=[
            step(1, "Errors contain 'certificate has expired' and 'x509'. Certificate expiry at 93% confidence.", "CALL_TOOL", ToolName.LOKI, "x509: certificate has expired. CN=auth.internal.company.com, expired 2026-03-02T02:00:00Z. cert-manager renewal failed 48h ago."),
            step(2, "cert-manager failed. Checking recent infra commits.", "CALL_TOOL", ToolName.GIT_BLAME, "No app code changes. cert-manager ClusterIssuer config modified 5 days ago by DevOps."),
            step(3, "ClusterIssuer misconfiguration prevented auto-renewal. Root cause confirmed.", "DONE"),
        ],
        code_changes=[],
        log_evidence=LogEvidence(
            correlation_id="corr-d4e5f6", evidence_path="correlation_id",
            stack_traces=["tls: failed to verify certificate: x509: certificate has expired or is not yet valid"],
            key_log_lines=["ERROR: TLS handshake failed: certificate expired", "ERROR: cert-manager: ACME challenge failed: DNS-01 timeout"],
            slow_queries=[],
            total_error_count=890
        ),
        ruled_out_categories=[
            ruled_out(FailureCategory.DB_CONNECTIVITY, "DB accessible via internal IP", "DB errors not present in logs"),
            ruled_out(FailureCategory.CODE_LOGIC_ERROR, "No app code change, cert issue is infra", "Error in TLS layer not application layer"),
        ],
        possible_fixes=[
            fix(1, "Manually renew cert: kubectl cert-manager renew auth-service-tls", "Immediately issues new cert", "Service restored within 2 min"),
            fix(2, "Fix ClusterIssuer ACME challenge configuration", "Restores automated renewal", "Prevents recurrence"),
            fix(3, "Add cert expiry monitoring alert at 14 days before expiry", "Early warning of expiry", "Proactive prevention"),
        ]
    ),

    # 7 — inventory-service: Config drift (MEDIUM, staging)
    RCAReport(
        report_id="rca-inventory-service-seed-007",
        generated_at=dt(4.0),
        app_name="inventory-service",
        alert_time=dt(4.1, 13, 20),
        severity="medium",
        environment="staging",
        root_cause="REDIS_URL environment variable pointing to decommissioned Redis instance. Config drift between staging and prod — staging env was not updated after Redis migration.",
        root_cause_category=FailureCategory.CONFIG_DRIFT,
        confidence_level=ConfidenceLevel.HIGH,
        is_code_change=False,
        initial_hypotheses=[
            "config_drift (71.00%)",
            "dependency_failure (60.00%)",
            "network_intra_service (35.00%)"
        ],
        investigation_steps=[
            step(1, "Connection refused errors to Redis. Config drift and dependency failure both likely.", "CALL_TOOL", ToolName.LOKI, "Redis connection refused: redis-old.staging.internal:6379. Host decommissioned 3 days ago. REDIS_URL env var not updated."),
            step(2, "Checking if someone recently updated the redis config.", "CALL_TOOL", ToolName.GIT_BLAME, "No recent changes to inventory-service config. Redis migration ticket from 3 days ago not applied to staging."),
            step(3, "Config drift confirmed — staging env var stale. No code issue.", "DONE"),
        ],
        code_changes=[],
        log_evidence=LogEvidence(
            correlation_id="corr-e5f6g7", evidence_path="correlation_id",
            stack_traces=["redis.exceptions.ConnectionError: Error 111 connecting to redis-old.staging.internal:6379. Connection refused."],
            key_log_lines=["ERROR: Cannot connect to Redis at redis-old.staging.internal:6379", "WARN: Cache miss falling through to DB (degraded mode)"],
            slow_queries=[],
            total_error_count=29
        ),
        ruled_out_categories=[
            ruled_out(FailureCategory.CODE_LOGIC_ERROR, "Application code correct, issue is environment config", "No code changes, only env var outdated"),
            ruled_out(FailureCategory.DB_CONNECTIVITY, "Primary DB healthy", "Only Redis cache affected"),
        ],
        possible_fixes=[
            fix(1, "Update REDIS_URL in staging ConfigMap to redis-new.staging.internal:6379", "Immediately fixes connectivity", "Service restored"),
            fix(2, "Implement config validation on startup to fail fast on unreachable deps", "Catches stale config early", "Faster incident detection"),
            fix(3, "Add staging environment to migration runbooks as mandatory update target", "Process improvement", "Prevents drift recurrence"),
        ]
    ),

    # 8 — checkout-service: Network intra-service (HIGH, prod)
    RCAReport(
        report_id="rca-checkout-service-seed-008",
        generated_at=dt(5.0),
        app_name="checkout-service",
        alert_time=dt(5.1, 19, 10),
        severity="high",
        environment="prod",
        root_cause="Network policy misconfiguration after cluster upgrade blocked egress from checkout-service to payment-service on port 8443. Kubernetes NetworkPolicy was not migrated to new API version.",
        root_cause_category=FailureCategory.NETWORK_INTRA_SERVICE,
        confidence_level=ConfidenceLevel.CONFIRMED,
        is_code_change=False,
        initial_hypotheses=[
            "network_intra_service (82.00%)",
            "dns_failure (40.00%)",
            "dependency_failure (30.00%)"
        ],
        investigation_steps=[
            step(1, "Connection timeouts specifically to payment-service, DNS resolves fine. Network issue.", "CALL_TOOL", ToolName.LOKI, "TCP connection timeout to payment-service:8443. DNS resolves correctly. Network policy blocking port 8443 egress."),
            step(2, "NetworkPolicy issue likely. Checking for infra config changes.", "CALL_TOOL", ToolName.GIT_BLAME, "Cluster upgrade applied 6 hours ago. NetworkPolicy migration script missed checkout→payment rule."),
            step(3, "Network policy migration gap confirmed. Root cause identified.", "DONE"),
        ],
        code_changes=[],
        log_evidence=LogEvidence(
            correlation_id="corr-f6g7h8", evidence_path="correlation_id",
            stack_traces=["io.grpc.StatusRuntimeException: UNAVAILABLE: io exception: Connection refused"],
            key_log_lines=["ERROR: Failed to connect to payment-service:8443: connection timeout", "WARN: Retrying payment gRPC call (attempt 2/3)"],
            slow_queries=[],
            total_error_count=512
        ),
        ruled_out_categories=[
            ruled_out(FailureCategory.DNS_FAILURE, "payment-service hostname resolves correctly", "nslookup successful from pod"),
            ruled_out(FailureCategory.DB_CONNECTIVITY, "DB connections healthy in both services", "No DB errors in logs"),
        ],
        possible_fixes=[
            fix(1, "Apply missing NetworkPolicy rule: allow checkout-service egress to payment-service:8443", "Restores inter-service connectivity", "Immediate resolution"),
            fix(2, "Add automated NetworkPolicy validation to cluster upgrade checklist", "Catches missing rules during upgrades", "Process improvement"),
            fix(3, "Add inter-service connectivity healthchecks to readiness probes", "Detects network issues at startup", "Earlier detection"),
        ]
    ),

    # 9 — search-service: DB connectivity (HIGH, prod)
    RCAReport(
        report_id="rca-search-service-seed-009",
        generated_at=dt(6.0),
        app_name="search-service",
        alert_time=dt(6.1, 7, 0),
        severity="high",
        environment="prod",
        root_cause="PostgreSQL replica promoted to primary during failover, but search-service still pointing to old primary IP. Connection string uses hardcoded IP instead of DNS endpoint.",
        root_cause_category=FailureCategory.DB_CONNECTIVITY,
        confidence_level=ConfidenceLevel.CONFIRMED,
        is_code_change=True,
        initial_hypotheses=[
            "db_connectivity (85.00%)",
            "config_drift (65.00%)",
            "network_intra_service (25.00%)"
        ],
        investigation_steps=[
            step(1, "DB connection errors during what logs show as a DB failover event. High DB confidence.", "CALL_TOOL", ToolName.LOKI, "FATAL: connection to server at 10.0.2.45:5432 failed. Server closed connection unexpectedly. DB failover detected 07:02 UTC."),
            step(2, "DB failover happened. Checking if config uses DNS or IP.", "CALL_TOOL", ToolName.GIT_BLAME, "Commit e8c1f3a: 'Hardcode DB primary IP for latency' by Dev Team (2 months ago). DB_HOST=10.0.2.45 (old primary)."),
            step(3, "Hardcoded IP is root cause — doesn't follow failover. Also checking Jira.", "CALL_TOOL", ToolName.JIRA, "SRCH-445: Performance optimisation — hardcoded DB IP. Status: Closed. No mention of failover risk."),
            step(4, "Hardcoded IP confirmed as cause. New primary is at 10.0.2.67 after failover.", "DONE"),
        ],
        code_changes=[
            CodeChange(commit_hash="e8c1f3a", author="Dev Team", timestamp=dt(60, 10, 0),
                       message="Hardcode DB primary IP to reduce DNS lookup latency - SRCH-445",
                       files_changed=["config/database.yaml", "helm/values-prod.yaml"],
                       jira_ticket="SRCH-445", risk_flags=["hardcoded_ip", "failover_risk"])
        ],
        log_evidence=LogEvidence(
            correlation_id="corr-g7h8i9", evidence_path="correlation_id",
            stack_traces=["psycopg2.OperationalError: FATAL: connection to server at 10.0.2.45:5432 failed"],
            key_log_lines=["ERROR: DB connection failed to 10.0.2.45:5432", "INFO: DB failover detected — primary changed to 10.0.2.67"],
            slow_queries=[],
            total_error_count=201
        ),
        ruled_out_categories=[
            ruled_out(FailureCategory.CODE_LOGIC_ERROR, "Application logic correct, config is the issue", "Error in connection layer not business logic"),
            ruled_out(FailureCategory.DNS_FAILURE, "DNS resolving correctly, issue is hardcoded IP bypass of DNS", "DNS not involved — IP used directly"),
        ],
        possible_fixes=[
            fix(1, "Update DB_HOST to 10.0.2.67 (new primary) immediately", "Restores connectivity to correct primary", "Immediate resolution"),
            fix(2, "Replace hardcoded IP with DNS endpoint: db-primary.internal.company.com", "DNS automatically follows failover", "Permanent fix, eliminates recurrence"),
            fix(3, "Add DB failover simulation to quarterly DR drills", "Validates failover automation end-to-end", "Process improvement"),
        ]
    ),

    # 10 — analytics-service: Code logic error (MEDIUM, staging)
    RCAReport(
        report_id="rca-analytics-service-seed-010",
        generated_at=dt(7.0),
        app_name="analytics-service",
        alert_time=dt(7.1, 15, 55),
        severity="medium",
        environment="staging",
        root_cause="Division by zero error in daily active user calculation when day has zero events (e.g. weekend). No guard for empty dataset edge case in MetricsAggregator.",
        root_cause_category=FailureCategory.CODE_LOGIC_ERROR,
        confidence_level=ConfidenceLevel.HIGH,
        is_code_change=True,
        initial_hypotheses=[
            "code_logic_error (73.00%)",
            "config_drift (31.00%)",
            "db_connectivity (18.00%)"
        ],
        investigation_steps=[
            step(1, "ZeroDivisionError with clear Python traceback. Code error hypothesis at 73%.", "CALL_TOOL", ToolName.LOKI, "ZeroDivisionError in MetricsAggregator.calculate_dau() at line 88. Happens on days with 0 events."),
            step(2, "Line 88 is the culprit. Checking recent changes to MetricsAggregator.", "CALL_TOOL", ToolName.GIT_BLAME, "Commit 7b3a9d1: 'Add DAU calculation to analytics pipeline' by Eve Martinez (5 days ago). New code, no zero-division guard."),
            step(3, "New feature introduced without edge case handling. Root cause confirmed.", "DONE"),
        ],
        code_changes=[
            CodeChange(commit_hash="7b3a9d1", author="Eve Martinez", timestamp=dt(5, 14, 0),
                       message="Add DAU calculation to analytics pipeline - ANA-102",
                       files_changed=["src/analytics/MetricsAggregator.py", "tests/test_metrics.py"],
                       jira_ticket="ANA-102", risk_flags=["missing_edge_case", "no_zero_division_guard"])
        ],
        log_evidence=LogEvidence(
            correlation_id="corr-h8i9j0", evidence_path="correlation_id",
            stack_traces=["ZeroDivisionError: division by zero\n  File 'MetricsAggregator.py', line 88, in calculate_dau\n    dau = total_events / unique_days"],
            key_log_lines=["ERROR: ZeroDivisionError in calculate_dau()", "INFO: Event count for period: 0"],
            slow_queries=[],
            total_error_count=4
        ),
        ruled_out_categories=[
            ruled_out(FailureCategory.DB_CONNECTIVITY, "Data fetched successfully, error in calculation", "DB query returns empty result normally"),
            ruled_out(FailureCategory.CONFIG_DRIFT, "Config unchanged", "Error is in new code, not config"),
        ],
        possible_fixes=[
            fix(1, "Add guard: return 0 if unique_days == 0 else total_events / unique_days", "Fixes division by zero", "Immediate error resolution"),
            fix(2, "Add unit test for empty-period edge case", "Catches regression in CI", "Prevention of recurrence"),
            fix(3, "Add input validation at analytics pipeline entry point", "Catches empty datasets early", "Defensive programming improvement"),
        ]
    ),
]

# ── Save to Database ──────────────────────────────────────────────────────────

print(f"Seeding {len(reports)} RCA reports into the database...\n")

errors_to_seed = [
    ("rca-payment-service-seed-001", [
        ("corr-a1b2c3", "HikariPool-1 - Connection is not available, request timed out after 30000ms"),
        ("corr-a1b2c4", "org.hibernate.exception.JDBCConnectionException: Unable to acquire JDBC Connection"),
    ]),
    ("rca-user-service-seed-002", [
        (None, "java.lang.OutOfMemoryError: Java heap space"),
        ("corr-u2b2c3", "GC overhead limit exceeded — heap utilisation 98.7%"),
    ]),
    ("rca-api-gateway-seed-003", [
        ("corr-dns-001", "java.net.UnknownHostException: payment-service.svc.cluster.local"),
        ("corr-dns-002", "Upstream DNS timeout: NXDOMAIN for user-service.svc.cluster.local"),
    ]),
    ("rca-order-service-seed-004", [
        ("corr-b2c3d4", "java.lang.NullPointerException at OrderValidator.validate(OrderValidator.java:47)"),
    ]),
    ("rca-notification-service-seed-005", [
        ("corr-c3d4e5", "SendGrid API error: 503 Service Unavailable"),
    ]),
    ("rca-auth-service-seed-006", [
        ("corr-d4e5f6", "tls: failed to verify certificate: x509: certificate has expired"),
        ("corr-d4e5f7", "Certificate CN=auth.internal.company.com expired 2026-03-02T02:00:00Z"),
    ]),
    ("rca-inventory-service-seed-007", [
        ("corr-e5f6g7", "redis.exceptions.ConnectionError: Error 111 connecting to redis-old.staging.internal:6379"),
    ]),
    ("rca-checkout-service-seed-008", [
        ("corr-f6g7h8", "io.grpc.StatusRuntimeException: UNAVAILABLE: connection timeout to payment-service:8443"),
        ("corr-f6g7h9", "Failed to establish TCP connection to 10.100.5.23:8443 after 5000ms"),
    ]),
    ("rca-search-service-seed-009", [
        ("corr-g7h8i9", "psycopg2.OperationalError: FATAL: connection to server at 10.0.2.45:5432 failed"),
    ]),
    ("rca-analytics-service-seed-010", [
        ("corr-h8i9j0", "ZeroDivisionError: division by zero in MetricsAggregator.calculate_dau()"),
    ]),
]

success = 0
for report in reports:
    ok = db.save_report(report)
    status = "✅" if ok else "❌"
    print(f"  {status} {report.report_id}  [{report.severity.upper()} / {report.environment}]  {report.root_cause_category.value}")
    if ok:
        success += 1

# Seed error records directly (save_report skips errors since RCAReport has no errors field)
session = get_session(db.engine)
try:
    for investigation_id, errs in errors_to_seed:
        for corr_id, msg in errs:
            session.add(InvestigationError(
                investigation_id=investigation_id,
                correlation_id=corr_id,
                error_message=msg
            ))
    session.commit()
    print(f"\n  ✅ Error records seeded successfully")
except Exception as e:
    session.rollback()
    print(f"\n  ⚠️  Error records partially seeded: {e}")
finally:
    session.close()

print(f"\n{'='*55}")
print(f"Done: {success}/{len(reports)} reports saved.")
print(f"Open http://localhost:8000/dashboard-ui to view them.")
