"""
Shared test fixtures and utilities for SRE Agent test suite.

Author: Morgan (TESTER)
"""

import json
import pytest
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

from models.alert import AlertPayload, ErrorEntry, Severity, Environment


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FIXTURE DATA LOADERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Return path to fixtures directory"""
    return Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def alert_fixtures_raw(fixtures_dir: Path) -> Dict[str, Any]:
    """Load raw alert fixtures from JSON"""
    with open(fixtures_dir / "alerts.json", "r") as f:
        return json.load(f)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ALERT PAYLOAD FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def db_connectivity_alert(alert_fixtures_raw: Dict[str, Any]) -> AlertPayload:
    """Alert fixture for DB connectivity failure"""
    return AlertPayload(**alert_fixtures_raw["db_connectivity_failure"])


@pytest.fixture
def dns_failure_alert(alert_fixtures_raw: Dict[str, Any]) -> AlertPayload:
    """Alert fixture for DNS failure"""
    return AlertPayload(**alert_fixtures_raw["dns_failure"])


@pytest.fixture
def certificate_expiry_alert(alert_fixtures_raw: Dict[str, Any]) -> AlertPayload:
    """Alert fixture for certificate expiry"""
    return AlertPayload(**alert_fixtures_raw["certificate_expiry"])


@pytest.fixture
def code_logic_error_alert(alert_fixtures_raw: Dict[str, Any]) -> AlertPayload:
    """Alert fixture for code logic error"""
    return AlertPayload(**alert_fixtures_raw["code_logic_error"])


@pytest.fixture
def mixed_ambiguous_alert(alert_fixtures_raw: Dict[str, Any]) -> AlertPayload:
    """Alert fixture for mixed/ambiguous failure patterns"""
    return AlertPayload(**alert_fixtures_raw["mixed_ambiguous"])


@pytest.fixture
def memory_exhaustion_alert(alert_fixtures_raw: Dict[str, Any]) -> AlertPayload:
    """Alert fixture for memory exhaustion"""
    return AlertPayload(**alert_fixtures_raw["memory_exhaustion"])


@pytest.fixture
def config_drift_alert(alert_fixtures_raw: Dict[str, Any]) -> AlertPayload:
    """Alert fixture for configuration drift"""
    return AlertPayload(**alert_fixtures_raw["config_drift"])


@pytest.fixture
def network_intra_service_alert(alert_fixtures_raw: Dict[str, Any]) -> AlertPayload:
    """Alert fixture for network/intra-service failure"""
    return AlertPayload(**alert_fixtures_raw["network_intra_service"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NULL CORRELATION ID FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def alert_with_null_correlation_ids() -> AlertPayload:
    """
    Alert fixture where ALL correlation_ids are null.
    Critical test case for null safety.
    """
    return AlertPayload(
        app_name="test-service",
        alert_time=datetime.fromisoformat("2026-03-01T10:00:00+00:00"),
        severity=Severity.CRITICAL,
        environment=Environment.PROD,
        errors=[
            ErrorEntry(
                correlation_id=None,  # Explicitly null
                error_message="Connection refused to database server"
            ),
            ErrorEntry(
                correlation_id=None,  # Explicitly null
                error_message="psycopg2.OperationalError: timeout"
            )
        ]
    )


@pytest.fixture
def alert_with_mixed_correlation_ids() -> AlertPayload:
    """
    Alert fixture with mix of null and non-null correlation_ids.
    Tests handling of partial correlation ID availability.
    """
    return AlertPayload(
        app_name="test-service",
        alert_time=datetime.fromisoformat("2026-03-01T10:00:00+00:00"),
        severity=Severity.HIGH,
        environment=Environment.PROD,
        errors=[
            ErrorEntry(
                correlation_id="corr-123",
                error_message="Certificate expired"
            ),
            ErrorEntry(
                correlation_id=None,  # Null
                error_message="TLS handshake failed"
            ),
            ErrorEntry(
                correlation_id="corr-124",
                error_message="SSL error"
            )
        ]
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UTILITY FIXTURES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.fixture
def sample_correlation_ids() -> list[str]:
    """Sample correlation IDs for testing"""
    return [
        "test-corr-001",
        "test-corr-002",
        "test-corr-003",
    ]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MOCK FACTORIES (to be expanded in Phase 3-4)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# TODO: Morgan will add mock factories for Loki, Git, Jira in later phases
