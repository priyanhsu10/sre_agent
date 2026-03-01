"""
Unit tests for Agent Orchestrator.

Tests think-first protocol enforcement, investigation trace building, and error handling.

Author: Morgan (TESTER)
"""

import pytest
from datetime import datetime

from orchestrator.agent import AgentOrchestrator
from models.alert import AlertPayload, ErrorEntry, Severity, Environment


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# THINK-FIRST PROTOCOL TESTS (CRITICAL)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_classification_runs_first(db_connectivity_alert: AlertPayload):
    """
    CRITICAL TEST: Classification MUST be the first step in investigation_trace.
    No tools should be called before classification completes.
    """
    orchestrator = AgentOrchestrator(investigation_id="test-001")
    report = await orchestrator.investigate(db_connectivity_alert)

    # First investigation step must be classification
    first_step = report.investigation_steps[0]
    assert first_step.step_number == 1
    assert first_step.tool_called is None  # Classification is not a tool
    assert "classification" in first_step.reasoning.lower()


@pytest.mark.asyncio
async def test_investigation_trace_populated(db_connectivity_alert: AlertPayload):
    """Test that investigation_trace is populated with all steps"""
    orchestrator = AgentOrchestrator(investigation_id="test-002")
    report = await orchestrator.investigate(db_connectivity_alert)

    # Should have at least 2 steps (classification + reasoning placeholder)
    assert len(report.investigation_steps) >= 2

    # All steps should have required fields
    for step in report.investigation_steps:
        assert step.step_number >= 1
        assert len(step.reasoning) >= 10  # Non-empty reasoning
        assert step.decision is not None


@pytest.mark.asyncio
async def test_initial_hypotheses_recorded(certificate_expiry_alert: AlertPayload):
    """Test that initial classification hypotheses are recorded in report"""
    orchestrator = AgentOrchestrator(investigation_id="test-003")
    report = await orchestrator.investigate(certificate_expiry_alert)

    # Should have initial hypotheses from classification
    assert len(report.initial_hypotheses) > 0
    assert "certificate_expiry" in report.initial_hypotheses[0].lower()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NULL SAFETY TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_orchestrator_handles_null_correlation_ids(alert_with_null_correlation_ids: AlertPayload):
    """
    CRITICAL TEST: Orchestrator must handle alerts with null correlation_ids
    without crashing.
    """
    orchestrator = AgentOrchestrator(investigation_id="test-004")

    # Should not raise exception
    report = await orchestrator.investigate(alert_with_null_correlation_ids)

    # Should still produce valid report
    assert report is not None
    assert report.report_id == "test-004"
    assert len(report.investigation_steps) > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# REPORT GENERATION TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_report_always_generated(db_connectivity_alert: AlertPayload):
    """Test that a report is always generated, even if tools fail"""
    orchestrator = AgentOrchestrator(investigation_id="test-005")
    report = await orchestrator.investigate(db_connectivity_alert)

    # Report should have all required fields
    assert report.report_id is not None
    assert report.app_name == db_connectivity_alert.app_name
    assert report.severity == db_connectivity_alert.severity.value
    assert report.environment == db_connectivity_alert.environment.value
    assert report.root_cause is not None
    assert len(report.possible_fixes) >= 1


@pytest.mark.asyncio
async def test_report_includes_classification_result(dns_failure_alert: AlertPayload):
    """Test that report includes classification results"""
    orchestrator = AgentOrchestrator(investigation_id="test-006")
    report = await orchestrator.investigate(dns_failure_alert)

    # Should have root cause category from classification
    assert report.root_cause_category is not None
    assert report.confidence_level is not None


@pytest.mark.asyncio
async def test_report_has_valid_timestamps(code_logic_error_alert: AlertPayload):
    """Test that report timestamps are valid"""
    orchestrator = AgentOrchestrator(investigation_id="test-007")
    report = await orchestrator.investigate(code_logic_error_alert)

    assert report.generated_at is not None
    assert report.alert_time == code_logic_error_alert.alert_time

    # Investigation steps should have timestamps
    for step in report.investigation_steps:
        assert step.timestamp is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLASSIFICATION INTEGRATION TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_classification_result_stored(memory_exhaustion_alert: AlertPayload):
    """Test that classification result is stored in orchestrator state"""
    orchestrator = AgentOrchestrator(investigation_id="test-008")
    await orchestrator.investigate(memory_exhaustion_alert)

    # Classification result should be stored
    assert orchestrator.classification_result is not None
    assert len(orchestrator.classification_result.top_hypotheses) > 0


@pytest.mark.asyncio
async def test_different_alerts_produce_different_classifications(
    db_connectivity_alert: AlertPayload,
    dns_failure_alert: AlertPayload
):
    """Test that different alert types produce different classifications"""
    orch1 = AgentOrchestrator(investigation_id="test-009a")
    report1 = await orch1.investigate(db_connectivity_alert)

    orch2 = AgentOrchestrator(investigation_id="test-009b")
    report2 = await orch2.investigate(dns_failure_alert)

    # Should classify to different categories
    assert report1.root_cause_category != report2.root_cause_category


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ERROR HANDLING TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_orchestrator_handles_classification_in_investigation_trace():
    """Test that even if something fails, investigation trace includes classification"""
    alert = AlertPayload(
        app_name="test-app",
        alert_time=datetime.fromisoformat("2026-03-01T10:00:00+00:00"),
        severity=Severity.CRITICAL,
        environment=Environment.PROD,
        errors=[
            ErrorEntry(
                correlation_id="test",
                error_message="Some error"
            )
        ]
    )

    orchestrator = AgentOrchestrator(investigation_id="test-010")
    report = await orchestrator.investigate(alert)

    # Should have classification step even on partial failure
    classification_steps = [
        s for s in report.investigation_steps
        if "classification" in s.reasoning.lower()
    ]
    assert len(classification_steps) > 0
