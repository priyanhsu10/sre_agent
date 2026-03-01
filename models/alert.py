from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class Severity(str, Enum):
    """Alert severity levels"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"


class Environment(str, Enum):
    """Deployment environments"""
    PROD = "prod"
    STAGING = "staging"


class ErrorEntry(BaseModel):
    """
    Single error entry from alert payload.
    correlation_id can be null — this is expected and must be handled gracefully.
    """
    correlation_id: Optional[str] = Field(
        None,
        description="Correlation ID for tracing. Can be null."
    )
    error_message: str = Field(
        ...,
        description="Raw error message from the alert"
    )


class AlertPayload(BaseModel):
    """
    Incoming webhook alert payload.
    This is the root contract for all webhook ingestion.
    """
    app_name: str = Field(
        ...,
        description="Name of the application/service that triggered the alert"
    )
    alert_time: datetime = Field(
        ...,
        description="ISO8601 timestamp of when the alert was triggered"
    )
    severity: Severity = Field(
        ...,
        description="Severity level of the alert"
    )
    environment: Environment = Field(
        ...,
        description="Environment where the alert originated"
    )
    errors: list[ErrorEntry] = Field(
        ...,
        min_length=1,
        description="List of error entries. Must contain at least one error."
    )
