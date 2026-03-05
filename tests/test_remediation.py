"""
Unit tests for the automated code fix remediation feature.

Tests: runtime detection, branch naming, fix type selection,
       error handling, and the full happy-path revert flow.
"""

import pytest
import asyncio
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from models.report import RCAReport, PossibleFix, CodeChange, InvestigationStep
from models.hypothesis import FailureCategory, ConfidenceLevel
from models.remediation import RemediationStatus
from remediation.test_runner import detect_runtime
from remediation.branch_manager import make_branch_name
from remediation.fix_applier import determine_fix_type
from remediation.capability import check_claude_available


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _make_report(
    is_code_change: bool = False,
    category: FailureCategory = FailureCategory.CODE_LOGIC_ERROR,
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH,
    code_changes: list | None = None,
    possible_fixes: list | None = None,
) -> RCAReport:
    """Build a minimal RCAReport for testing."""
    if code_changes is None:
        code_changes = []
    if possible_fixes is None:
        possible_fixes = [
            PossibleFix(
                priority=1,
                action="Revert commit abc123def by dev@example.com" if is_code_change else "Add null checks",
                rationale="Test rationale",
                estimated_impact="Immediate",
            )
        ]
    return RCAReport(
        report_id="rca-test-app-1234567890",
        generated_at=datetime(2026, 3, 1, 10, 0, 0),
        app_name="test-app",
        alert_time=datetime(2026, 3, 1, 10, 0, 0),
        severity="critical",
        environment="prod",
        root_cause="NullPointerException in payment processor",
        root_cause_category=category,
        confidence_level=confidence,
        is_code_change=is_code_change,
        ruled_out_categories=[],
        code_changes=code_changes,
        log_evidence=None,
        possible_fixes=possible_fixes,
        investigation_steps=[
            InvestigationStep(
                step_number=1,
                reasoning="classification step",
                decision="code_logic_error identified",
                tool_called=None,
            )
        ],
        initial_hypotheses=["code_logic_error (85%)"],
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. RUNTIME DETECTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_runtime_detection_python(tmp_path: Path):
    """requirements.txt present → detected as python runtime using pytest."""
    (tmp_path / "requirements.txt").write_text("fastapi\npytest\n")
    runtime, cmd = detect_runtime(tmp_path)
    assert runtime == "python"
    assert "pytest" in cmd


def test_runtime_detection_spring_boot_maven(tmp_path: Path):
    """pom.xml + mvnw present → Spring Boot with ./mvnw test."""
    (tmp_path / "pom.xml").write_text("<project/>")
    (tmp_path / "mvnw").write_text("#!/bin/bash")
    runtime, cmd = detect_runtime(tmp_path)
    assert runtime == "spring_boot"
    assert "./mvnw" in cmd


def test_runtime_detection_spring_boot_gradle(tmp_path: Path):
    """build.gradle + gradlew present → Spring Boot with ./gradlew test."""
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }")
    (tmp_path / "gradlew").write_text("#!/bin/bash")
    runtime, cmd = detect_runtime(tmp_path)
    assert runtime == "spring_boot"
    assert "./gradlew" in cmd


def test_runtime_detection_react(tmp_path: Path):
    """package.json present → React/Node runtime."""
    (tmp_path / "package.json").write_text('{"name": "my-app", "scripts": {"test": "jest"}}')
    runtime, cmd = detect_runtime(tmp_path)
    assert runtime == "react"
    assert "npm" in cmd or "yarn" in cmd


def test_runtime_detection_fallback(tmp_path: Path):
    """Empty repo → unknown runtime with make test."""
    runtime, cmd = detect_runtime(tmp_path)
    assert runtime == "unknown"
    assert "make" in cmd


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. BRANCH NAMING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_branch_name_format():
    """Branch name should follow fix/rca-{report_id} pattern."""
    report_id = "rca-payment-service-1234567890"
    branch = make_branch_name("fix/rca", report_id)
    assert branch.startswith("fix/rca-")
    assert report_id in branch


def test_branch_name_sanitizes_special_chars():
    """Spaces and slashes in report_id are replaced with dashes."""
    branch = make_branch_name("fix/rca", "my report/id")
    assert " " not in branch


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. FIX TYPE SELECTION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_revert_fix_type_selected():
    """is_code_change=True with a revert action → fix_type 'revert'."""
    cc = CodeChange(
        commit_hash="abc123def456",
        author="dev@example.com",
        timestamp=datetime(2026, 3, 1),
        message="Update payment logic",
        files_changed=["src/payment.py"],
    )
    report = _make_report(is_code_change=True, code_changes=[cc])
    fix_type = determine_fix_type(report, llm_enabled=False, claude_available=False)
    assert fix_type == "revert"


def test_llm_patch_fix_type_selected():
    """code_logic_error + claude_available=True → fix_type 'claude_agent_patch'."""
    report = _make_report(
        is_code_change=False,
        category=FailureCategory.CODE_LOGIC_ERROR,
        possible_fixes=[
            PossibleFix(
                priority=1,
                action="Add null checks and defensive programming",
                rationale="Prevent NullPointerException",
                estimated_impact="Fixes immediate error",
            )
        ],
    )
    fix_type = determine_fix_type(report, llm_enabled=True, claude_available=True)
    assert fix_type == "claude_agent_patch"


def test_manual_instructions_fix_type_when_claude_unavailable():
    """code_logic_error + claude_available=False → fix_type 'manual_instructions'."""
    report = _make_report(
        is_code_change=False,
        category=FailureCategory.CODE_LOGIC_ERROR,
        possible_fixes=[
            PossibleFix(
                priority=1,
                action="Add null checks and defensive programming",
                rationale="Prevent NullPointerException",
                estimated_impact="Fixes immediate error",
            )
        ],
    )
    fix_type = determine_fix_type(report, llm_enabled=False, claude_available=False)
    assert fix_type == "manual_instructions"


def test_no_fix_type_when_not_applicable():
    """Non-code category without LLM → 'none'."""
    report = _make_report(
        is_code_change=False,
        category=FailureCategory.DB_CONNECTIVITY,
        possible_fixes=[
            PossibleFix(
                priority=1,
                action="Check database server health",
                rationale="Restore DB",
                estimated_impact="Immediate",
            )
        ],
    )
    fix_type = determine_fix_type(report, llm_enabled=False, claude_available=False)
    assert fix_type == "none"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3b. CAPABILITY CHECK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_capability_check_fails_when_llm_disabled():
    """LLM_ENABLED=False → Claude not available with helpful reason."""
    available, reason = check_claude_available(llm_enabled=False, api_key="sk-test-key")
    assert available is False
    assert "LLM_ENABLED" in reason


def test_capability_check_fails_when_no_api_key():
    """Empty API key → Claude not available with helpful reason."""
    available, reason = check_claude_available(llm_enabled=True, api_key="")
    assert available is False
    assert "LLM_API_KEY" in reason


def test_capability_check_fails_when_api_key_blank():
    """Whitespace-only API key → Claude not available."""
    available, reason = check_claude_available(llm_enabled=True, api_key="   ")
    assert available is False


def test_capability_check_network_failure():
    """Unreachable host → Claude not available."""
    from unittest.mock import patch
    with patch("remediation.capability._is_anthropic_reachable", return_value=False):
        available, reason = check_claude_available(llm_enabled=True, api_key="sk-real-key")
    assert available is False
    assert "network" in reason.lower() or "reachable" in reason.lower() or "api.anthropic" in reason


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. REMEDIATION AGENT — ERROR HANDLING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_remediation_agent_no_repo():
    """When app repo does not exist, RemediationAgent returns status=failed gracefully."""
    from remediation.agent import RemediationAgent

    report = _make_report(is_code_change=True, code_changes=[
        CodeChange(
            commit_hash="abc123",
            author="dev@example.com",
            timestamp=datetime(2026, 3, 1),
            message="Bad commit",
            files_changed=["src/main.py"],
        )
    ])

    # GIT_REPOS_ROOT is ./repos, which won't have 'test-app' in tests
    agent = RemediationAgent()
    result = await agent.run(report)

    # Must not raise — always returns a result
    assert result is not None
    assert result.report_id == report.report_id
    assert result.status in (RemediationStatus.failed,)
    assert result.error_message is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. REMEDIATION AGENT — HAPPY PATH (mocked)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_remediation_agent_revert_happy_path(tmp_path: Path):
    """Full revert happy path with mocked git and test operations."""
    from remediation.agent import RemediationAgent

    # Create a fake Python repo
    (tmp_path / "requirements.txt").write_text("pytest\n")

    report = _make_report(
        is_code_change=True,
        category=FailureCategory.CODE_LOGIC_ERROR,
        confidence=ConfidenceLevel.HIGH,
        code_changes=[
            CodeChange(
                commit_hash="abc123def456",
                author="dev@example.com",
                timestamp=datetime(2026, 3, 1),
                message="Broke payment logic",
                files_changed=["src/payment.py"],
            )
        ],
    )

    with (
        patch("remediation.agent.settings") as mock_settings,
        patch("remediation.agent.check_claude_available", return_value=(False, "LLM_ENABLED=False")) as _,
        patch("remediation.agent.create_branch", new_callable=AsyncMock) as mock_create,
        patch("remediation.agent.apply_revert", new_callable=AsyncMock) as mock_revert,
        patch("remediation.agent.run_tests", new_callable=AsyncMock) as mock_tests,
        patch("remediation.agent.push_branch", new_callable=AsyncMock) as mock_push,
    ):
        mock_settings.AUTO_REMEDIATION_ENABLED = True
        mock_settings.AUTO_REMEDIATION_MIN_CONFIDENCE = "High"
        mock_settings.REMEDIATION_BRANCH_PREFIX = "fix/rca"
        mock_settings.REMEDIATION_REMOTE = "origin"
        mock_settings.REMEDIATION_TEST_TIMEOUT_SECONDS = 60
        mock_settings.REMEDIATION_MAX_FIX_ITERATIONS = 3
        mock_settings.GIT_REPOS_ROOT = str(tmp_path.parent)
        mock_settings.LLM_ENABLED = False
        mock_settings.LLM_API_KEY = ""
        mock_settings.LLM_MODEL = "claude-sonnet-4-6"

        # Mock the repo path to exist
        app_repo = tmp_path.parent / report.app_name
        app_repo.mkdir(exist_ok=True)
        (app_repo / "requirements.txt").write_text("pytest\n")

        mock_create.return_value = None
        mock_revert.return_value = (True, "Reverted commit abc123def456")
        mock_tests.return_value = (True, "1 passed in 0.5s")
        mock_push.return_value = None

        agent = RemediationAgent()
        result = await agent.run(report)

    assert result.tests_passed is True
    assert result.branch_pushed is True
    assert result.status == RemediationStatus.pushed
    assert result.fix_type == "revert"
    assert result.commit_hash_reverted is not None
