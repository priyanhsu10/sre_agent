"""
Unit tests for Webhook API endpoint.

Tests payload validation, 202 response, background task triggering.

Author: Morgan (TESTER)
"""

import pytest
from fastapi.testclient import TestClient
from datetime import datetime

from main import app
from models.alert import AlertPayload, ErrorEntry, Severity, Environment

client = TestClient(app)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WEBHOOK ACCEPTANCE TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_webhook_accepts_valid_alert():
    """Test that webhook accepts valid alert and returns 202"""
    payload = {
        "app_name": "test-service",
        "alert_time": "2026-03-01T10:00:00Z",
        "severity": "critical",
        "environment": "prod",
        "errors": [
            {
                "correlation_id": "test-123",
                "error_message": "Connection refused"
            }
        ]
    }

    response = client.post("/webhook/alert", json=payload)

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert "investigation_id" in response.json()
    assert response.json()["app_name"] == "test-service"


def test_webhook_accepts_null_correlation_ids():
    """
    CRITICAL TEST: Webhook must accept alerts with null correlation_ids.
    This is a common production scenario.
    """
    payload = {
        "app_name": "test-service",
        "alert_time": "2026-03-01T10:00:00Z",
        "severity": "high",
        "environment": "prod",
        "errors": [
            {
                "correlation_id": None,  # Explicitly null
                "error_message": "Database timeout"
            }
        ]
    }

    response = client.post("/webhook/alert", json=payload)

    assert response.status_code == 202
    assert response.json()["null_correlation_ids"] == 1


def test_webhook_tracks_null_correlation_id_count():
    """Test that webhook counts null correlation IDs correctly"""
    payload = {
        "app_name": "test-service",
        "alert_time": "2026-03-01T10:00:00Z",
        "severity": "medium",
        "environment": "staging",
        "errors": [
            {"correlation_id": "corr-1", "error_message": "Error 1"},
            {"correlation_id": None, "error_message": "Error 2"},
            {"correlation_id": None, "error_message": "Error 3"},
        ]
    }

    response = client.post("/webhook/alert", json=payload)

    assert response.status_code == 202
    assert response.json()["error_count"] == 3
    assert response.json()["null_correlation_ids"] == 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VALIDATION TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_webhook_rejects_missing_required_fields():
    """Test that webhook rejects payloads with missing required fields"""
    payload = {
        "app_name": "test-service",
        # Missing alert_time, severity, environment, errors
    }

    response = client.post("/webhook/alert", json=payload)

    assert response.status_code == 422  # Unprocessable Entity


def test_webhook_rejects_invalid_severity():
    """Test that webhook rejects invalid severity values"""
    payload = {
        "app_name": "test-service",
        "alert_time": "2026-03-01T10:00:00Z",
        "severity": "invalid_severity",  # Invalid
        "environment": "prod",
        "errors": [
            {"correlation_id": "test", "error_message": "Error"}
        ]
    }

    response = client.post("/webhook/alert", json=payload)

    assert response.status_code == 422


def test_webhook_rejects_invalid_environment():
    """Test that webhook rejects invalid environment values"""
    payload = {
        "app_name": "test-service",
        "alert_time": "2026-03-01T10:00:00Z",
        "severity": "critical",
        "environment": "invalid_env",  # Invalid
        "errors": [
            {"correlation_id": "test", "error_message": "Error"}
        ]
    }

    response = client.post("/webhook/alert", json=payload)

    assert response.status_code == 422


def test_webhook_rejects_empty_errors_list():
    """Test that webhook rejects alerts with empty errors list"""
    payload = {
        "app_name": "test-service",
        "alert_time": "2026-03-01T10:00:00Z",
        "severity": "critical",
        "environment": "prod",
        "errors": []  # Empty - should be rejected
    }

    response = client.post("/webhook/alert", json=payload)

    assert response.status_code == 422


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RESPONSE FORMAT TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_webhook_response_contains_investigation_id():
    """Test that response includes investigation_id for tracking"""
    payload = {
        "app_name": "test-service",
        "alert_time": "2026-03-01T10:00:00Z",
        "severity": "critical",
        "environment": "prod",
        "errors": [
            {"correlation_id": "test", "error_message": "Error"}
        ]
    }

    response = client.post("/webhook/alert", json=payload)

    assert "investigation_id" in response.json()
    assert response.json()["investigation_id"].startswith("rca-")


def test_webhook_response_includes_app_context():
    """Test that response includes alert context"""
    payload = {
        "app_name": "my-app",
        "alert_time": "2026-03-01T10:00:00Z",
        "severity": "high",
        "environment": "staging",
        "errors": [
            {"correlation_id": "test", "error_message": "Error"}
        ]
    }

    response = client.post("/webhook/alert", json=payload)

    data = response.json()
    assert data["app_name"] == "my-app"
    assert data["severity"] == "high"
    assert data["environment"] == "staging"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HEALTH CHECK TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def test_webhook_health_check():
    """Test that health check endpoint works"""
    response = client.get("/webhook/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_root_health_check():
    """Test that root health check endpoint works"""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
