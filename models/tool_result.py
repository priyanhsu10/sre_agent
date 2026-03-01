from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime
from enum import Enum


class ToolName(str, Enum):
    """Available investigation tools"""
    LOKI = "loki"
    GIT_BLAME = "git_blame"
    JIRA = "jira"


class EvidencePath(str, Enum):
    """How evidence was retrieved"""
    CORRELATION_ID = "correlation_id"
    FINGERPRINT_FALLBACK = "fingerprint_fallback"
    LABEL_QUERY = "label_query"
    TIME_RANGE = "time_range"


class ToolResult(BaseModel):
    """
    Standard result contract for all tools.
    Sam: All your tools must return this exact structure.
    """
    tool_name: ToolName = Field(
        ...,
        description="Which tool produced this result"
    )
    success: bool = Field(
        ...,
        description="Whether the tool execution succeeded"
    )
    data: Optional[dict[str, Any]] = Field(
        None,
        description="Tool-specific result data. None if tool failed."
    )
    error_message: Optional[str] = Field(
        None,
        description="Error message if success=False"
    )
    duration_ms: float = Field(
        ...,
        ge=0.0,
        description="Tool execution duration in milliseconds"
    )
    evidence_path: Optional[EvidencePath] = Field(
        None,
        description="How the evidence was retrieved (for audit trail)"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this result was generated"
    )


class ToolMetadata(BaseModel):
    """Metadata about tool availability and circuit breaker state"""
    tool_name: ToolName
    available: bool = Field(
        default=True,
        description="Whether the tool is currently available"
    )
    circuit_breaker_open: bool = Field(
        default=False,
        description="Whether circuit breaker is open (tool failing repeatedly)"
    )
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
