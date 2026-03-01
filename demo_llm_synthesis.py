#!/usr/bin/env python3
"""
Demonstration of LLM-Enhanced Synthesis for Root Cause Analysis.

Shows how LLM synthesizes evidence from logs, git, and Jira into actionable RCA.

Author: Riley (DEV-3) + Alex (ARCHITECT)
"""

import sys
import asyncio
from datetime import datetime
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from models.alert import AlertPayload, ErrorEntry, Severity, Environment
from models.hypothesis import ClassificationResult, Hypothesis, FailureCategory, ConfidenceLevel
from models.tool_result import ToolResult, ToolName, EvidencePath
from reasoning.synthesis import SynthesisEngine
from reasoning.llm_synthesis import LLMSynthesisEngine
from llm.client import LLMConfig, LLMProvider

print("=" * 80)
print("SRE AGENT - LLM-ENHANCED SYNTHESIS DEMO")
print("=" * 80)


async def demo_synthesis_comparison():
    """Compare rule-based vs LLM-enhanced synthesis"""

    print("\n📊 SCENARIO: Database Connection Failure with Recent Code Changes")
    print("-" * 80)

    # Mock classification result
    classification = ClassificationResult(
        top_hypotheses=[
            Hypothesis(
                category=FailureCategory.DB_CONNECTIVITY,
                confidence_percentage=85.0,
                confidence_level=ConfidenceLevel.HIGH,
                reasoning="Multiple database connection errors detected in logs"
            ),
            Hypothesis(
                category=FailureCategory.CODE_LOGIC_ERROR,
                confidence_percentage=10.0,
                confidence_level=ConfidenceLevel.LOW,
                reasoning="No clear code logic errors"
            ),
            Hypothesis(
                category=FailureCategory.CONFIG_DRIFT,
                confidence_percentage=5.0,
                confidence_level=ConfidenceLevel.LOW,
                reasoning="Configuration appears stable"
            )
        ],
        category_scores={
            FailureCategory.DB_CONNECTIVITY: 85.0,
            FailureCategory.CODE_LOGIC_ERROR: 10.0,
            FailureCategory.CONFIG_DRIFT: 5.0,
        },
        classification_reasoning="Pattern matching identified database connection failures",
        classification_duration_ms=15.2,
        classified_at=datetime.utcnow()
    )

    # Mock tool results
    tool_results = {
        "loki": ToolResult(
            tool_name=ToolName.LOKI,
            success=True,
            data={
                "total_error_count": 47,
                "total_lines_retrieved": 150,
                "stack_traces": [
                    "psycopg2.OperationalError: connection refused to postgres.prod:5432\n"
                    "at connection.py:145\n"
                    "at pool.py:88"
                ],
                "slow_queries": [
                    "SELECT * FROM users WHERE id = $1 - 2500ms"
                ],
                "key_log_lines": [
                    "ERROR: Database connection pool exhausted - max 100 connections",
                    "ERROR: Connection timeout after 30s to postgres.prod:5432",
                    "ERROR: Too many connections (100 active, limit 100)"
                ]
            },
            error_message=None,
            duration_ms=1234.5,
            evidence_path=EvidencePath.CORRELATION_ID,
            timestamp=datetime.utcnow()
        ),
        "git_blame": ToolResult(
            tool_name=ToolName.GIT_BLAME,
            success=True,
            data={
                "total_commits": 3,
                "high_churn_files": ["src/database/pool.py"],
                "jira_keys": ["INFRA-123"],
                "commits": [
                    {
                        "commit_hash": "abc123",
                        "author": "jane.doe@company.com",
                        "timestamp": "2026-02-28T14:30:00Z",
                        "message": "INFRA-123: Increase connection pool size from 50 to 100",
                        "files_changed": ["src/database/pool.py", "config/production.yaml"]
                    },
                    {
                        "commit_hash": "def456",
                        "author": "john.smith@company.com",
                        "timestamp": "2026-02-27T10:15:00Z",
                        "message": "Add connection retry logic",
                        "files_changed": ["src/database/connection.py"]
                    }
                ]
            },
            error_message=None,
            duration_ms=567.8,
            evidence_path=None,
            timestamp=datetime.utcnow()
        ),
        "jira": ToolResult(
            tool_name=ToolName.JIRA,
            success=True,
            data={
                "total_tickets": 1,
                "risk_flagged_count": 1,
                "tickets": [
                    {
                        "key": "INFRA-123",
                        "summary": "Scale database connection pool for peak traffic",
                        "status": "In Progress",
                        "labels": ["performance", "database"],
                        "risk_flags": ["In Progress"]
                    }
                ]
            },
            error_message=None,
            duration_ms=234.1,
            evidence_path=None,
            timestamp=datetime.utcnow()
        )
    }

    print("\n📋 Evidence Summary:")
    print("   Logs: 47 errors, 1 stack trace, 1 slow query")
    print("   Git: 3 recent commits, including pool size increase")
    print("   Jira: INFRA-123 (In Progress) - Scale connection pool")

    # Test 1: Rule-based synthesis
    print("\n\n1️⃣  RULE-BASED SYNTHESIS")
    print("-" * 80)

    rule_based_engine = SynthesisEngine()
    root_cause, category, confidence, is_code_change = await rule_based_engine.synthesize_root_cause(
        classification=classification,
        tool_results=tool_results
    )

    print(f"✅ Root Cause (Rule-Based):")
    print(f"   {root_cause}")
    print(f"\n   Category: {category.value}")
    print(f"   Confidence: {confidence.value}")
    print(f"   Code Change Involved: {is_code_change}")

    # Test 2: LLM-enhanced synthesis
    print("\n\n2️⃣  LLM-ENHANCED SYNTHESIS")
    print("-" * 80)

    llm_config = LLMConfig(
        provider=LLMProvider.MOCK,  # Use mock for demo
        api_key="mock-key",
        model="mock-model"
    )

    llm_engine = LLMSynthesisEngine(llm_config)
    llm_root_cause, llm_category, llm_confidence, llm_code_change = await llm_engine.synthesize_root_cause(
        classification=classification,
        tool_results=tool_results
    )

    print(f"✅ Root Cause (LLM-Enhanced):")
    print(f"   {llm_root_cause}")
    print(f"\n   Category: {llm_category.value}")
    print(f"   Confidence: {llm_confidence.value}")
    print(f"   Code Change Involved: {llm_code_change}")

    print("\n\n📊 COMPARISON")
    print("-" * 80)
    print("\n📌 Rule-Based Synthesis:")
    print("   ✅ Fast (no API calls)")
    print("   ✅ Deterministic")
    print("   ⚠️  Template-based output")
    print("   ⚠️  Limited context correlation")

    print("\n🤖 LLM-Enhanced Synthesis:")
    print("   ✅ Context-aware narrative")
    print("   ✅ Better evidence correlation")
    print("   ✅ Actionable explanations")
    print("   ✅ Natural language reasoning")
    print("   ⚠️  Slower (API calls)")
    print("   ⚠️  Costs ~$0.01 per synthesis")


async def demo_high_confidence_synthesis():
    """Show that LLM synthesis works even at high confidence (>70%)"""

    print("\n\n" + "=" * 80)
    print("SCENARIO: High Confidence (90%) - LLM Still Provides Better Analysis")
    print("=" * 80)

    classification = ClassificationResult(
        top_hypotheses=[
            Hypothesis(
                category=FailureCategory.MEMORY_RESOURCE_EXHAUSTION,
                confidence_percentage=95.0,
                confidence_level=ConfidenceLevel.CONFIRMED,
                reasoning="Clear OOM errors in logs"
            )
        ],
        category_scores={FailureCategory.MEMORY_RESOURCE_EXHAUSTION: 95.0},
        classification_reasoning="Multiple OOMKilled events",
        classification_duration_ms=12.0,
        classified_at=datetime.utcnow()
    )

    tool_results = {
        "loki": ToolResult(
            tool_name=ToolName.LOKI,
            success=True,
            data={
                "total_error_count": 23,
                "stack_traces": [
                    "OOMKilled: Container exceeded memory limit (512Mi)\n"
                    "java.lang.OutOfMemoryError: Java heap space"
                ],
                "key_log_lines": [
                    "FATAL: Container killed - memory limit exceeded",
                    "ERROR: Heap size: 512MB, Used: 510MB, Free: 2MB"
                ]
            },
            error_message=None,
            duration_ms=890.0,
            evidence_path=EvidencePath.FINGERPRINT_FALLBACK,
            timestamp=datetime.utcnow()
        )
    }

    print("\n📋 Scenario Details:")
    print("   Classification Confidence: 95% (CONFIRMED)")
    print("   Evidence: Clear OOM errors")
    print("   User Request: Use LLM synthesis even at >70% confidence")

    llm_config = LLMConfig(
        provider=LLMProvider.MOCK,
        api_key="mock-key",
        model="mock-model"
    )

    llm_engine = LLMSynthesisEngine(llm_config)
    root_cause, category, confidence, is_code = await llm_engine.synthesize_root_cause(
        classification=classification,
        tool_results=tool_results
    )

    print(f"\n🤖 LLM Synthesis Result (Even at 95% confidence):")
    print(f"   {root_cause}")
    print(f"\n   ✅ LLM provides richer context and actionable insights")
    print(f"   ✅ Correlates multiple evidence sources intelligently")
    print(f"   ✅ Natural language explanation for stakeholders")


async def main():
    """Run all demos"""
    await demo_synthesis_comparison()
    await demo_high_confidence_synthesis()

    print("\n\n" + "=" * 80)
    print("DEMO COMPLETE")
    print("=" * 80)

    print("\n✅ Key Takeaways:")
    print("   1. LLM synthesis provides context-aware root cause analysis")
    print("   2. Works at ANY confidence level (not just low confidence)")
    print("   3. Better correlation of evidence from logs, git, and Jira")
    print("   4. Natural language explanations for stakeholders")
    print("   5. Falls back to rule-based if LLM fails (reliability)")

    print("\n🚀 Production Usage:")
    print("   - Set LLM_ENABLED=true in .env")
    print("   - LLM synthesis happens automatically in orchestrator")
    print("   - Classification AND synthesis both use LLM when enabled")
    print("   - Cost: ~$0.02 per complete investigation (classification + synthesis)")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
