#!/usr/bin/env python3
"""
Simulates the full SRE Agent pipeline for a test alert.
Shows what would happen when sending an alert to the webhook.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

print("=" * 80)
print("SRE AGENT - FULL PIPELINE SIMULATION")
print("Simulating: POST /webhook/alert")
print("=" * 80)

# Load test alert
alerts_file = Path("tests/fixtures/alerts.json")
if not alerts_file.exists():
    print("❌ Error: tests/fixtures/alerts.json not found")
    sys.exit(1)

with open(alerts_file) as f:
    alerts = json.load(f)

# Use the db_connectivity alert as example
alert_data = alerts["db_connectivity_failure"]

print("\n📨 INCOMING WEBHOOK REQUEST")
print("-" * 80)
print(f"POST http://localhost:8000/webhook/alert")
print(f"Content-Type: application/json")
print(f"\nPayload:")
print(json.dumps(alert_data, indent=2))

# Simulate webhook response (202 Accepted)
investigation_id = f"rca-{alert_data['app_name']}-{int(datetime.utcnow().timestamp())}"

print("\n\n📬 WEBHOOK RESPONSE (202 Accepted)")
print("-" * 80)
response = {
    "status": "accepted",
    "investigation_id": investigation_id,
    "message": f"Alert received. Investigation started for {alert_data['app_name']}.",
    "app_name": alert_data['app_name'],
    "severity": alert_data['severity'],
    "environment": alert_data['environment'],
    "error_count": len(alert_data['errors']),
    "null_correlation_ids": sum(1 for e in alert_data['errors'] if e['correlation_id'] is None)
}
print(json.dumps(response, indent=2))

print("\n\n🔄 BACKGROUND INVESTIGATION STARTED")
print("-" * 80)
print(f"Investigation ID: {investigation_id}")

# STEP 1: Classification
print("\n\n📊 STEP 1: CLASSIFICATION (Think-First)")
print("-" * 80)

error_messages = [e['error_message'] for e in alert_data['errors']]
combined_text = " ".join(error_messages).lower()

print(f"Analyzing {len(error_messages)} error message(s)...")
print(f"\nError messages:")
for i, msg in enumerate(error_messages, 1):
    print(f"  {i}. {msg[:80]}...")

# Pattern matching
db_patterns = [
    "connection", "refused", "postgres", "database", "pool", "exhausted",
    "timeout", "psycopg2", "operationalerror"
]
matches = [p for p in db_patterns if p in combined_text]

confidence = (len(matches) / len(db_patterns)) * 100

classification = {
    "top_hypotheses": [
        {
            "category": "db_connectivity",
            "confidence_percentage": round(confidence, 2),
            "confidence_level": "High" if confidence >= 70 else "Medium",
            "reasoning": f"Matched {len(matches)} DB connectivity patterns",
            "supporting_evidence": matches
        },
        {
            "category": "network_intra_service",
            "confidence_percentage": 25.0,
            "confidence_level": "Low",
            "reasoning": "Some network-related patterns detected",
            "supporting_evidence": ["connection", "timeout"]
        },
        {
            "category": "code_logic_error",
            "confidence_percentage": 15.0,
            "confidence_level": "Low",
            "reasoning": "Minimal code error patterns",
            "supporting_evidence": []
        }
    ],
    "classification_duration_ms": 12.5
}

print(f"\n✅ Classification complete in {classification['classification_duration_ms']}ms")
print(f"\nTop 3 Hypotheses:")
for i, hyp in enumerate(classification['top_hypotheses'], 1):
    print(f"  {i}. {hyp['category']}: {hyp['confidence_percentage']}% ({hyp['confidence_level']})")
    print(f"     Evidence: {', '.join(hyp['supporting_evidence'][:5])}")

# STEP 2: Reasoning Loop
print("\n\n🧠 STEP 2: REASONING LOOP")
print("-" * 80)

step_num = 2
top_hypothesis = classification['top_hypotheses'][0]['category']

# Decision: Call Loki first (infra-first for DB category)
print(f"\nStep {step_num}: Reasoning Decision")
print(f"  Top hypothesis: {top_hypothesis}")
print(f"  Decision: Call Loki tool (infra-first for DB connectivity)")
print(f"  Reasoning: Check logs for DB connection errors before blaming code")

step_num += 1

# Simulate Loki tool execution
print(f"\nStep {step_num}: Execute Loki Tool")
print(f"  Query method: Correlation ID (2 available, 1 null)")
print(f"  Fallback: Fingerprint query for null correlation ID")

loki_result = {
    "tool_name": "loki",
    "success": True,
    "duration_ms": 245.3,
    "evidence_path": "correlation_id",
    "data": {
        "total_lines_retrieved": 47,
        "stack_traces": 2,
        "slow_queries": 0,
        "key_log_lines": 15,
        "total_error_count": 47
    }
}

print(f"  ✅ Loki completed in {loki_result['duration_ms']}ms")
print(f"  Retrieved: {loki_result['data']['total_lines_retrieved']} log lines")
print(f"  Found: {loki_result['data']['stack_traces']} stack traces")
print(f"  Found: {loki_result['data']['key_log_lines']} key error lines")

step_num += 1

# Decision: Call Git
print(f"\nStep {step_num}: Reasoning Decision")
print(f"  Loki evidence gathered")
print(f"  Decision: Call Git tool to check for recent code changes")
print(f"  Reasoning: Check if recent commits introduced DB connection issues")

step_num += 1

print(f"\nStep {step_num}: Execute Git Tool")
print(f"  Repository: ./repos/{alert_data['app_name']}")
print(f"  Lookback: 7 days")

git_result = {
    "tool_name": "git_blame",
    "success": True,
    "duration_ms": 156.7,
    "data": {
        "total_commits": 3,
        "commits": [
            {
                "commit_hash": "a1b2c3d4e5f6",
                "author": "Jane Developer",
                "message": "Fix database connection pooling - PROJ-456",
                "files_changed": ["src/database/pool.py"]
            }
        ],
        "high_churn_files": [],
        "jira_keys": ["PROJ-456"]
    }
}

print(f"  ✅ Git completed in {git_result['duration_ms']}ms")
print(f"  Found: {git_result['data']['total_commits']} recent commits")
print(f"  Jira keys: {', '.join(git_result['data']['jira_keys'])}")

step_num += 1

# Decision: Call Jira
print(f"\nStep {step_num}: Reasoning Decision")
print(f"  Git found {len(git_result['data']['jira_keys'])} Jira key(s)")
print(f"  Decision: Call Jira tool to get ticket context")

step_num += 1

print(f"\nStep {step_num}: Execute Jira Tool")
print(f"  Fetching tickets: {', '.join(git_result['data']['jira_keys'])}")

jira_result = {
    "tool_name": "jira",
    "success": True,
    "duration_ms": 89.2,
    "data": {
        "total_tickets": 1,
        "risk_flagged_count": 0,
        "tickets": [
            {
                "key": "PROJ-456",
                "summary": "Fix database connection pooling issues",
                "status": "Done",
                "labels": ["database", "performance"]
            }
        ]
    }
}

print(f"  ✅ Jira completed in {jira_result['duration_ms']}ms")
print(f"  Retrieved: {jira_result['data']['total_tickets']} ticket(s)")
print(f"  Risk flags: {jira_result['data']['risk_flagged_count']}")

step_num += 1

# Decision: Done
print(f"\nStep {step_num}: Reasoning Decision")
print(f"  All relevant tools called (Loki, Git, Jira)")
print(f"  Confidence: High (DB connectivity confirmed by logs)")
print(f"  Decision: DONE - Investigation complete")

# STEP 3: Synthesis
print("\n\n🔬 STEP 3: EVIDENCE SYNTHESIS")
print("-" * 80)

print("\nCorrelating evidence from all tools:")
print(f"  ✅ Loki: 47 error lines about DB connections")
print(f"  ✅ Git: Recent commit touched connection pool code")
print(f"  ✅ Jira: PROJ-456 was fixing DB pooling issues")

synthesis = {
    "root_cause": "Database connection pool exhaustion. Recent changes to connection pooling (PROJ-456) may have introduced configuration issues.",
    "root_cause_category": "db_connectivity",
    "confidence_level": "High",
    "is_code_change": True
}

print(f"\n✅ Root Cause: {synthesis['root_cause']}")
print(f"   Category: {synthesis['root_cause_category']}")
print(f"   Confidence: {synthesis['confidence_level']}")
print(f"   Code Change Involved: {synthesis['is_code_change']}")

# STEP 4: Report Generation
print("\n\n📄 STEP 4: REPORT GENERATION")
print("-" * 80)

report_files = {
    "json": f"./reports/{investigation_id}.json",
    "markdown": f"./reports/{investigation_id}.md"
}

print(f"\nGenerating reports...")
print(f"  ✅ JSON report: {report_files['json']}")
print(f"  ✅ Markdown report: {report_files['markdown']}")

# Sample report content
print(f"\n📋 Report Summary:")
print(f"  Report ID: {investigation_id}")
print(f"  Application: {alert_data['app_name']}")
print(f"  Severity: {alert_data['severity']}")
print(f"  Environment: {alert_data['environment']}")
print(f"  Root Cause: DB connectivity - connection pool exhaustion")
print(f"  Confidence: High")
print(f"  Investigation Steps: {step_num}")
print(f"  Code Changes: 1 commit")
print(f"  Ruled Out: 2 categories (dns_failure, certificate_expiry)")

print("\n📝 Possible Fixes (prioritized):")
fixes = [
    {
        "priority": 1,
        "action": "Revert commit a1b2c3d4e5f6 if issue started after deployment",
        "impact": "Immediate resolution"
    },
    {
        "priority": 2,
        "action": "Increase database connection pool size",
        "impact": "Allows more concurrent connections"
    },
    {
        "priority": 3,
        "action": "Review connection pool configuration in PROJ-456",
        "impact": "Fixes configuration issues"
    },
    {
        "priority": 4,
        "action": "Implement connection pool monitoring and alerts",
        "impact": "Proactive prevention"
    }
]

for fix in fixes:
    print(f"  {fix['priority']}. {fix['action']}")
    print(f"     Impact: {fix['impact']}")

# Final summary
print("\n\n" + "=" * 80)
print("✅ INVESTIGATION COMPLETE")
print("=" * 80)

print(f"\n📊 Pipeline Statistics:")
print(f"  Total duration: ~600ms")
print(f"  Tools called: 3 (Loki, Git, Jira)")
print(f"  Investigation steps: {step_num}")
print(f"  Reports generated: 2 (JSON + Markdown)")

print(f"\n📁 Output Files:")
print(f"  {report_files['json']}")
print(f"  {report_files['markdown']}")

print("\n🎯 Result:")
print(f"  Root Cause: Database connection pool exhaustion")
print(f"  Confidence: High")
print(f"  Recommended Action: Check connection pool config from PROJ-456")

print("\n" + "=" * 80)
print("Pipeline executed successfully! 🚀")
print("=" * 80)
