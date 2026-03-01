"""
Unit tests for investigation tools (Loki, Git, Jira).

Tests tool contract compliance, null safety, error handling, and data extraction.

Author: Morgan (TESTER)
"""

import pytest
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from models.alert import AlertPayload, ErrorEntry, Severity, Environment
from models.tool_result import ToolResult, ToolName, EvidencePath
from tools.loki import LokiLogRetriever
from tools.git_blame import GitBlameChecker
from tools.jira import JiraTicketGetter
from config import Settings


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def mock_settings():
    """Mock settings for tools"""
    return Settings(
        LOKI_URL="http://localhost:3100",
        LOKI_TIMEOUT_SECONDS=30,
        LOKI_MAX_LINES=1000,
        LOKI_LOOKBACK_MINUTES=60,
        SLOW_QUERY_THRESHOLD_MS=1000,
        GIT_REPOS_ROOT="./test_repos",
        GIT_LOOKBACK_DAYS=7,
        HIGH_CHURN_COMMIT_COUNT=5,
        MAX_DIFF_LINES=500,
        JIRA_URL="https://jira.example.com",
        JIRA_USERNAME="test",
        JIRA_API_TOKEN="token",
        JIRA_TIMEOUT_SECONDS=10,
        JIRA_MAX_CONCURRENT_REQUESTS=10,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOKI TOOL TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_loki_returns_tool_result(mock_settings, db_connectivity_alert):
    """Test that Loki tool returns ToolResult contract"""
    loki = LokiLogRetriever(mock_settings)

    with patch.object(loki, '_execute_logql_query', new_callable=AsyncMock) as mock_query:
        mock_query.return_value = ["ERROR: Connection refused"]

        result = await loki.execute(db_connectivity_alert, {})

        # Verify ToolResult contract
        assert isinstance(result, ToolResult)
        assert result.tool_name == ToolName.LOKI
        assert isinstance(result.success, bool)
        assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_loki_uses_correlation_id_path(mock_settings):
    """Test that Loki uses correlation_id query when available"""
    alert = AlertPayload(
        app_name="test-app",
        alert_time=datetime.fromisoformat("2026-03-01T10:00:00+00:00"),
        severity=Severity.CRITICAL,
        environment=Environment.PROD,
        errors=[
            ErrorEntry(correlation_id="corr-123", error_message="Error 1"),
            ErrorEntry(correlation_id="corr-456", error_message="Error 2")
        ]
    )

    loki = LokiLogRetriever(mock_settings)

    with patch.object(loki, '_query_by_correlation_ids', new_callable=AsyncMock) as mock_corr:
        mock_corr.return_value = (["log line"], EvidencePath.CORRELATION_ID)

        result = await loki.execute(alert, {})

        # Should use correlation_id path
        mock_corr.assert_called_once()
        assert result.evidence_path == EvidencePath.CORRELATION_ID


@pytest.mark.asyncio
async def test_loki_uses_fingerprint_fallback_when_null(mock_settings, alert_with_null_correlation_ids):
    """
    CRITICAL TEST: Loki must use fingerprint fallback when correlation_id is null
    """
    loki = LokiLogRetriever(mock_settings)

    with patch.object(loki, '_query_by_fingerprint', new_callable=AsyncMock) as mock_fingerprint:
        mock_fingerprint.return_value = (["log line"], EvidencePath.FINGERPRINT_FALLBACK)

        result = await loki.execute(alert_with_null_correlation_ids, {})

        # Should use fingerprint fallback
        mock_fingerprint.assert_called_once()
        assert result.evidence_path == EvidencePath.FINGERPRINT_FALLBACK
        assert result.success is True


@pytest.mark.asyncio
async def test_loki_extracts_stack_traces(mock_settings, db_connectivity_alert):
    """Test that Loki extracts stack traces from logs"""
    loki = LokiLogRetriever(mock_settings)

    log_with_traceback = [
        "ERROR: Something failed",
        "Traceback (most recent call last):",
        "  File 'app.py', line 42, in process",
        "    result = connect()",
        "ConnectionError: Connection refused"
    ]

    with patch.object(loki, '_execute_logql_query', new_callable=AsyncMock) as mock_query:
        mock_query.return_value = log_with_traceback

        result = await loki.execute(db_connectivity_alert, {})

        assert result.success is True
        assert "stack_traces" in result.data
        assert len(result.data["stack_traces"]) > 0


@pytest.mark.asyncio
async def test_loki_extracts_slow_queries(mock_settings, db_connectivity_alert):
    """Test that Loki extracts slow queries"""
    loki = LokiLogRetriever(mock_settings)

    logs_with_slow_queries = [
        "Query executed in 1500 ms: SELECT * FROM orders",
        "Normal query completed in 50ms",
        "Slow SQL detected: duration 2500ms"
    ]

    with patch.object(loki, '_execute_logql_query', new_callable=AsyncMock) as mock_query:
        mock_query.return_value = logs_with_slow_queries

        result = await loki.execute(db_connectivity_alert, {})

        assert result.success is True
        assert "slow_queries" in result.data
        # Should detect 2 slow queries (>1000ms threshold)
        assert len(result.data["slow_queries"]) >= 1


@pytest.mark.asyncio
async def test_loki_handles_connection_error(mock_settings, db_connectivity_alert):
    """Test that Loki handles connection errors gracefully"""
    loki = LokiLogRetriever(mock_settings)

    with patch.object(loki, '_execute_logql_query', side_effect=Exception("Connection refused")):
        result = await loki.execute(db_connectivity_alert, {})

        # Should return failure, not raise exception
        assert result.success is False
        assert result.error_message is not None
        assert "failed" in result.error_message.lower()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GIT TOOL TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_git_returns_tool_result(mock_settings, db_connectivity_alert):
    """Test that Git tool returns ToolResult contract"""
    git = GitBlameChecker(mock_settings)

    with patch.object(Path, 'exists', return_value=True):
        with patch.object(git, '_update_repo', new_callable=AsyncMock):
            with patch.object(git, '_get_recent_commits', new_callable=AsyncMock) as mock_commits:
                mock_commits.return_value = []

                result = await git.execute(db_connectivity_alert, {})

                assert isinstance(result, ToolResult)
                assert result.tool_name == ToolName.GIT_BLAME
                assert isinstance(result.success, bool)


@pytest.mark.asyncio
async def test_git_handles_repo_not_found(mock_settings, db_connectivity_alert):
    """Test that Git handles missing repository gracefully"""
    git = GitBlameChecker(mock_settings)

    with patch.object(Path, 'exists', return_value=False):
        result = await git.execute(db_connectivity_alert, {})

        # Should return failure with helpful message
        assert result.success is False
        assert "not found" in result.error_message.lower()


@pytest.mark.asyncio
async def test_git_extracts_jira_keys(mock_settings, db_connectivity_alert):
    """Test that Git extracts Jira keys from commit messages"""
    git = GitBlameChecker(mock_settings)

    commits = [
        {"message": "Fix bug - PROJ-123", "files_changed": []},
        {"message": "INFRA-456: Update config", "files_changed": []},
        {"message": "No ticket reference here", "files_changed": []},
    ]

    with patch.object(Path, 'exists', return_value=True):
        with patch.object(git, '_update_repo', new_callable=AsyncMock):
            with patch.object(git, '_get_recent_commits', new_callable=AsyncMock) as mock_commits:
                mock_commits.return_value = commits

                result = await git.execute(db_connectivity_alert, {})

                assert result.success is True
                assert "jira_keys" in result.data
                # Should extract PROJ-123 and INFRA-456
                jira_keys = result.data["jira_keys"]
                assert "PROJ-123" in jira_keys
                assert "INFRA-456" in jira_keys


@pytest.mark.asyncio
async def test_git_detects_high_churn_files(mock_settings, db_connectivity_alert):
    """Test that Git detects high-churn files"""
    git = GitBlameChecker(mock_settings)

    # Create commits that modify same files repeatedly
    commits = [
        {"message": "Commit 1", "files_changed": ["src/app.py", "src/db.py"]},
        {"message": "Commit 2", "files_changed": ["src/app.py"]},
        {"message": "Commit 3", "files_changed": ["src/app.py"]},
        {"message": "Commit 4", "files_changed": ["src/app.py", "src/db.py"]},
        {"message": "Commit 5", "files_changed": ["src/app.py"]},
        {"message": "Commit 6", "files_changed": ["src/app.py"]},  # 6 commits total for app.py
    ]

    with patch.object(Path, 'exists', return_value=True):
        with patch.object(git, '_update_repo', new_callable=AsyncMock):
            with patch.object(git, '_get_recent_commits', new_callable=AsyncMock) as mock_commits:
                mock_commits.return_value = commits

                result = await git.execute(db_connectivity_alert, {})

                assert result.success is True
                assert "high_churn_files" in result.data
                # src/app.py should be flagged (6 commits > threshold of 5)
                high_churn = result.data["high_churn_files"]
                assert len(high_churn) > 0
                assert any(f["file"] == "src/app.py" for f in high_churn)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# JIRA TOOL TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_jira_returns_tool_result(mock_settings, db_connectivity_alert):
    """Test that Jira tool returns ToolResult contract"""
    jira = JiraTicketGetter(mock_settings)

    context = {"jira_keys": ["PROJ-123"]}

    with patch.object(jira, '_fetch_tickets_batch', new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = []

        result = await jira.execute(db_connectivity_alert, context)

        assert isinstance(result, ToolResult)
        assert result.tool_name == ToolName.JIRA
        assert isinstance(result.success, bool)


@pytest.mark.asyncio
async def test_jira_fetches_tickets_from_context(mock_settings, db_connectivity_alert):
    """Test that Jira fetches tickets when keys provided in context"""
    jira = JiraTicketGetter(mock_settings)

    context = {"jira_keys": ["PROJ-123", "PROJ-456"]}

    mock_tickets = [
        {"key": "PROJ-123", "summary": "Fix bug", "labels": []},
        {"key": "PROJ-456", "summary": "Update config", "labels": []}
    ]

    with patch.object(jira, '_fetch_tickets_batch', new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_tickets

        result = await jira.execute(db_connectivity_alert, context)

        assert result.success is True
        assert result.data["total_tickets"] == 2


@pytest.mark.asyncio
async def test_jira_uses_jql_fallback_when_no_keys(mock_settings, db_connectivity_alert):
    """Test that Jira uses JQL search when no keys provided"""
    jira = JiraTicketGetter(mock_settings)

    context = {}  # No jira_keys

    with patch.object(jira, '_search_by_jql', new_callable=AsyncMock) as mock_jql:
        mock_jql.return_value = []

        result = await jira.execute(db_connectivity_alert, context)

        # Should use JQL fallback
        mock_jql.assert_called_once()
        assert result.success is True


@pytest.mark.asyncio
async def test_jira_flags_risk_indicators(mock_settings, db_connectivity_alert):
    """Test that Jira flags tickets with risk indicators"""
    jira = JiraTicketGetter(mock_settings)

    context = {"jira_keys": ["PROJ-123", "PROJ-456"]}

    mock_tickets = [
        {
            "key": "PROJ-123",
            "summary": "Normal ticket",
            "labels": [],
            "status": "Done",
            "acceptance_criteria": "AC present"
        },
        {
            "key": "PROJ-456",
            "summary": "Risky ticket",
            "labels": ["hotfix", "emergency"],  # RISK FLAG
            "status": "Done",
            "acceptance_criteria": "AC present"
        }
    ]

    with patch.object(jira, '_fetch_tickets_batch', new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_tickets

        result = await jira.execute(db_connectivity_alert, context)

        assert result.success is True
        assert result.data["risk_flagged_count"] == 1
        assert len(result.data["risk_flagged_tickets"]) == 1


@pytest.mark.asyncio
async def test_jira_flags_missing_acceptance_criteria(mock_settings, db_connectivity_alert):
    """Test that Jira flags tickets with missing AC as risky"""
    jira = JiraTicketGetter(mock_settings)

    ticket_with_missing_ac = {
        "key": "PROJ-789",
        "summary": "Ticket without AC",
        "labels": [],
        "status": "Done",
        "acceptance_criteria": None  # Missing AC = RISK FLAG
    }

    # Test the risk detection directly
    has_risk = jira._has_risk_flags(ticket_with_missing_ac)
    assert has_risk is True


@pytest.mark.asyncio
async def test_jira_handles_connection_error(mock_settings, db_connectivity_alert):
    """Test that Jira handles connection errors gracefully"""
    jira = JiraTicketGetter(mock_settings)

    context = {"jira_keys": ["PROJ-123"]}

    with patch.object(jira, '_fetch_tickets_batch', side_effect=Exception("Connection error")):
        result = await jira.execute(db_connectivity_alert, context)

        # Should return failure, not raise exception
        assert result.success is False
        assert result.error_message is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TOOL CONTRACT COMPLIANCE TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_all_tools_never_raise_exceptions(mock_settings, db_connectivity_alert):
    """
    CRITICAL TEST: All tools must return ToolResult with success=False
    instead of raising exceptions.
    """
    loki = LokiLogRetriever(mock_settings)
    git = GitBlameChecker(mock_settings)
    jira = JiraTicketGetter(mock_settings)

    # Force errors in all tools
    with patch.object(loki, '_execute_logql_query', side_effect=Exception("Loki error")):
        loki_result = await loki.execute(db_connectivity_alert, {})
        assert isinstance(loki_result, ToolResult)
        assert loki_result.success is False

    with patch.object(Path, 'exists', side_effect=Exception("Git error")):
        git_result = await git.execute(db_connectivity_alert, {})
        assert isinstance(git_result, ToolResult)
        assert git_result.success is False

    with patch.object(jira, '_fetch_tickets_batch', side_effect=Exception("Jira error")):
        jira_result = await jira.execute(db_connectivity_alert, {"jira_keys": []})
        assert isinstance(jira_result, ToolResult)
        assert jira_result.success is False


@pytest.mark.asyncio
async def test_all_tools_track_duration(mock_settings, db_connectivity_alert):
    """Test that all tools track execution duration"""
    loki = LokiLogRetriever(mock_settings)
    git = GitBlameChecker(mock_settings)
    jira = JiraTicketGetter(mock_settings)

    with patch.object(loki, '_execute_logql_query', new_callable=AsyncMock, return_value=[]):
        loki_result = await loki.execute(db_connectivity_alert, {})
        assert loki_result.duration_ms >= 0

    with patch.object(Path, 'exists', return_value=False):
        git_result = await git.execute(db_connectivity_alert, {})
        assert git_result.duration_ms >= 0

    with patch.object(jira, '_search_by_jql', new_callable=AsyncMock, return_value=[]):
        jira_result = await jira.execute(db_connectivity_alert, {})
        assert jira_result.duration_ms >= 0
