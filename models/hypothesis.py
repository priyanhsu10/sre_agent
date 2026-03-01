from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class FailureCategory(str, Enum):
    """
    All possible root cause categories.
    These must match the pattern map in classifier/patterns.py
    """
    DB_CONNECTIVITY = "db_connectivity"
    DNS_FAILURE = "dns_failure"
    CERTIFICATE_EXPIRY = "certificate_expiry"
    NETWORK_INTRA_SERVICE = "network_intra_service"
    CODE_LOGIC_ERROR = "code_logic_error"
    CONFIG_DRIFT = "config_drift"
    DEPENDENCY_FAILURE = "dependency_failure"
    MEMORY_RESOURCE_EXHAUSTION = "memory_resource_exhaustion"


class ConfidenceLevel(str, Enum):
    """Confidence levels for hypotheses and final root cause"""
    LOW = "Low"           # < 40%
    MEDIUM = "Medium"     # 40-70%
    HIGH = "High"         # 70-85%
    CONFIRMED = "Confirmed"  # > 85%


class Hypothesis(BaseModel):
    """
    A single hypothesis about the root cause.
    Produced by the classification engine and updated by reasoning engine.
    """
    category: FailureCategory = Field(
        ...,
        description="The failure category this hypothesis represents"
    )
    confidence_percentage: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Confidence score (0-100)"
    )
    confidence_level: ConfidenceLevel = Field(
        ...,
        description="Human-readable confidence level"
    )
    reasoning: str = Field(
        ...,
        description="Why this hypothesis was scored this way. Must not be empty."
    )
    supporting_evidence: list[str] = Field(
        default_factory=list,
        description="Keywords/patterns from error messages that support this hypothesis"
    )


class ClassificationResult(BaseModel):
    """
    Result of the classification engine.
    Contains ranked hypotheses and full scoring matrix for audit trail.
    """
    top_hypotheses: list[Hypothesis] = Field(
        ...,
        min_length=1,
        max_length=3,
        description="Top 3 ranked hypotheses"
    )
    all_scores: dict[str, float] = Field(
        ...,
        description="Full scoring matrix: {category: score} for all categories"
    )
    classification_reasoning: str = Field(
        ...,
        description="Overall reasoning for the classification decision"
    )
    classification_duration_ms: float = Field(
        ...,
        ge=0.0,
        description="Time taken to classify the alert"
    )
