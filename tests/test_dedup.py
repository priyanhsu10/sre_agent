"""
Unit tests for alert deduplication.

Tests fingerprinting, dedup logic (in-progress, completed, failed, expired),
and webhook integration.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from models.alert import AlertPayload, ErrorEntry, Severity, Environment
from api.dedup import AlertDeduplicator, make_fingerprint, DedupEntry


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_alert(
    app: str = "payment-service",
    env: str = "prod",
    errors: list[str] | None = None,
    alert_time: str = "2026-03-01T10:00:00Z",
) -> AlertPayload:
    if errors is None:
        errors = ["Connection refused to database"]
    return AlertPayload(
        app_name=app,
        alert_time=datetime.fromisoformat(alert_time.replace("Z", "+00:00")),
        severity=Severity.CRITICAL,
        environment=Environment(env),
        errors=[ErrorEntry(correlation_id=None, error_message=m) for m in errors],
    )


# ── 1. FINGERPRINTING ────────────────────────────────────────────────────────

def test_same_content_same_fingerprint():
    """Identical alerts produce the same fingerprint."""
    a1 = _make_alert(alert_time="2026-03-01T10:00:00Z")
    a2 = _make_alert(alert_time="2026-03-01T10:05:00Z")  # different time
    assert make_fingerprint(a1) == make_fingerprint(a2)


def test_different_app_different_fingerprint():
    a1 = _make_alert(app="payment-service")
    a2 = _make_alert(app="auth-service")
    assert make_fingerprint(a1) != make_fingerprint(a2)


def test_different_environment_different_fingerprint():
    a1 = _make_alert(env="prod")
    a2 = _make_alert(env="staging")
    assert make_fingerprint(a1) != make_fingerprint(a2)


def test_different_error_messages_different_fingerprint():
    a1 = _make_alert(errors=["Connection refused"])
    a2 = _make_alert(errors=["NullPointerException"])
    assert make_fingerprint(a1) != make_fingerprint(a2)


def test_error_order_does_not_matter():
    """Error message order is normalised — same content = same fingerprint."""
    a1 = _make_alert(errors=["Error A", "Error B"])
    a2 = _make_alert(errors=["Error B", "Error A"])
    assert make_fingerprint(a1) == make_fingerprint(a2)


def test_error_case_does_not_matter():
    """Error messages are lowercased before fingerprinting."""
    a1 = _make_alert(errors=["CONNECTION REFUSED"])
    a2 = _make_alert(errors=["connection refused"])
    assert make_fingerprint(a1) == make_fingerprint(a2)


# ── 2. DEDUPLICATOR LOGIC ────────────────────────────────────────────────────

def test_new_alert_not_blocked():
    """First alert for a fingerprint is never blocked."""
    dedup = AlertDeduplicator(window_minutes=30)
    alert = _make_alert()
    fp = make_fingerprint(alert)
    assert dedup.check(fp) is None


def test_in_progress_alert_is_blocked():
    """Duplicate while investigation is in-flight is always suppressed."""
    dedup = AlertDeduplicator(window_minutes=30)
    alert = _make_alert()
    fp = make_fingerprint(alert)

    dedup.register(fp, "rca-test-001", alert)
    result = dedup.check(fp)

    assert result is not None
    assert result.investigation_id == "rca-test-001"
    assert result.status == "in_progress"


def test_completed_within_window_is_blocked():
    """Duplicate after completion (within window) is suppressed."""
    dedup = AlertDeduplicator(window_minutes=30)
    alert = _make_alert()
    fp = make_fingerprint(alert)

    dedup.register(fp, "rca-test-002", alert)
    dedup.mark_completed(fp)

    result = dedup.check(fp)
    assert result is not None
    assert result.status == "completed"


def test_completed_after_window_allows_new():
    """Duplicate after completion outside the window is allowed through."""
    dedup = AlertDeduplicator(window_minutes=30)
    alert = _make_alert()
    fp = make_fingerprint(alert)

    dedup.register(fp, "rca-test-003", alert)
    dedup.mark_completed(fp)

    # Artificially age the entry past the window
    dedup._registry[fp].registered_at = datetime.utcnow() - timedelta(minutes=31)

    result = dedup.check(fp)
    assert result is None  # allowed through


def test_failed_investigation_allows_retry():
    """Failed investigation does NOT block the next duplicate — retry allowed."""
    dedup = AlertDeduplicator(window_minutes=30)
    alert = _make_alert()
    fp = make_fingerprint(alert)

    dedup.register(fp, "rca-test-004", alert)
    dedup.mark_failed(fp)

    result = dedup.check(fp)
    assert result is None  # retry allowed


def test_expired_entries_evicted():
    """Old completed/failed entries are evicted during check."""
    dedup = AlertDeduplicator(window_minutes=30)
    alert = _make_alert()
    fp = make_fingerprint(alert)

    dedup.register(fp, "rca-old-001", alert)
    dedup.mark_completed(fp)
    dedup._registry[fp].registered_at = datetime.utcnow() - timedelta(minutes=31)

    # Check triggers eviction
    dedup.check(fp)
    assert fp not in dedup._registry


def test_stats_reports_correctly():
    """stats() returns correct counts by status."""
    dedup = AlertDeduplicator(window_minutes=30)

    a1 = _make_alert(app="app-1")
    a2 = _make_alert(app="app-2")
    a3 = _make_alert(app="app-3")

    dedup.register(make_fingerprint(a1), "rca-s-001", a1)               # in_progress
    dedup.register(make_fingerprint(a2), "rca-s-002", a2)
    dedup.mark_completed(make_fingerprint(a2))                           # completed
    dedup.register(make_fingerprint(a3), "rca-s-003", a3)
    dedup.mark_failed(make_fingerprint(a3))                              # failed

    s = dedup.stats()
    assert s["total_tracked"] == 3
    assert s["by_status"]["in_progress"] == 1
    assert s["by_status"]["completed"] == 1
    assert s["by_status"]["failed"] == 1


# ── 3. WEBHOOK INTEGRATION ───────────────────────────────────────────────────

def test_webhook_returns_duplicate_on_second_identical_alert():
    """Second identical alert while first investigation is in-flight → 200 duplicate."""
    from fastapi.testclient import TestClient
    from main import app
    import api.dedup as dedup_module

    # Reset singleton for test isolation
    dedup_module._deduplicator = AlertDeduplicator(window_minutes=30)
    client = TestClient(app)

    payload = {
        "app_name": "dedup-test-svc",
        "alert_time": "2026-03-01T10:00:00Z",
        "severity": "critical",
        "environment": "prod",
        "errors": [{"correlation_id": None, "error_message": "DB connection refused"}],
    }

    # First alert → 202
    r1 = client.post("/webhook/alert", json=payload)
    assert r1.status_code == 202
    first_id = r1.json()["investigation_id"]

    # Second identical alert (different alert_time — same content) → 200 duplicate
    payload["alert_time"] = "2026-03-01T10:02:00Z"
    r2 = client.post("/webhook/alert", json=payload)
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"
    assert r2.json()["investigation_id"] == first_id


def test_webhook_different_app_not_deduplicated():
    """Alerts for different apps are NOT deduplicated."""
    from fastapi.testclient import TestClient
    from main import app
    import api.dedup as dedup_module

    dedup_module._deduplicator = AlertDeduplicator(window_minutes=30)
    client = TestClient(app)

    base = {
        "alert_time": "2026-03-01T10:00:00Z",
        "severity": "high",
        "environment": "prod",
        "errors": [{"correlation_id": None, "error_message": "Timeout error"}],
    }

    r1 = client.post("/webhook/alert", json={**base, "app_name": "service-alpha"})
    r2 = client.post("/webhook/alert", json={**base, "app_name": "service-beta"})

    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["investigation_id"] != r2.json()["investigation_id"]


def test_webhook_health_includes_dedup_stats():
    """Health endpoint includes dedup registry stats."""
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    r = client.get("/webhook/health")
    assert r.status_code == 200
    body = r.json()
    assert "dedup" in body
    assert "total_tracked" in body["dedup"]
    assert "window_minutes" in body["dedup"]
