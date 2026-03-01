from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from .hypothesis import FailureCategory, ConfidenceLevel
from .tool_result import ToolName


class InvestigationStep(BaseModel):
    """
    Single step in the investigation trace.
    Riley: Your ReasoningEngine must produce this for each step.
    """
    step_number: int = Field(
        ...,
        ge=1,
        description="Sequential step number"
    )
    reasoning: str = Field(
        ...,
        min_length=10,
        description="Why this step was taken. MUST NOT BE EMPTY."
    )
    decision: str = Field(
        ...,
        description="What decision was made (which tool to call, or DONE)"
    )
    tool_called: Optional[ToolName] = Field(
        None,
        description="Tool that was invoked in this step (None if decision was DONE)"
    )
    result_summary: Optional[str] = Field(
        None,
        description="Brief summary of the tool result"
    )
    hypothesis_update: Optional[str] = Field(
        None,
        description="How hypotheses were updated after this step"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow
    )


class RuledOutCategory(BaseModel):
    """A failure category that was investigated and ruled out"""
    category: FailureCategory
    reason: str = Field(
        ...,
        description="Why this category was ruled out"
    )
    evidence: str = Field(
        ...,
        description="What evidence was used to rule it out"
    )


class CodeChange(BaseModel):
    """Information about a code change"""
    commit_hash: str
    author: str
    timestamp: datetime
    message: str
    files_changed: list[str]
    jira_ticket: Optional[str] = None
    risk_flags: list[str] = Field(
        default_factory=list,
        description="Risk indicators (e.g., 'hotfix', 'emergency', 'missing_ac')"
    )


class LogEvidence(BaseModel):
    """Log evidence extracted from Loki"""
    correlation_id: Optional[str] = None
    evidence_path: str = Field(
        ...,
        description="How logs were retrieved"
    )
    stack_traces: list[str] = Field(
        default_factory=list,
        description="Unique stack traces (deduplicated)"
    )
    key_log_lines: list[str] = Field(
        default_factory=list,
        description="Important log lines"
    )
    slow_queries: list[str] = Field(
        default_factory=list,
        description="Slow queries detected (if DB-related)"
    )
    total_error_count: int = Field(
        default=0,
        ge=0
    )


class PossibleFix(BaseModel):
    """A suggested remediation action"""
    priority: int = Field(
        ...,
        ge=1,
        description="Priority order (1 = immediate, higher = longer-term)"
    )
    action: str = Field(
        ...,
        description="The suggested action"
    )
    rationale: str = Field(
        ...,
        description="Why this fix is suggested"
    )
    estimated_impact: str = Field(
        ...,
        description="Expected impact (e.g., 'immediate resolution', 'preventative')"
    )


class RCAReport(BaseModel):
    """
    Final Root Cause Analysis report.
    This is the ultimate output of the entire pipeline.
    """
    # Metadata
    report_id: str = Field(
        ...,
        description="Unique report identifier"
    )
    generated_at: datetime = Field(
        default_factory=datetime.utcnow
    )

    # Alert context
    app_name: str
    alert_time: datetime
    severity: str
    environment: str

    # Root cause analysis
    root_cause: str = Field(
        ...,
        description="The identified root cause"
    )
    root_cause_category: FailureCategory = Field(
        ...,
        description="Category of the root cause"
    )
    confidence_level: ConfidenceLevel = Field(
        ...,
        description="Confidence in the root cause determination"
    )
    is_code_change: bool = Field(
        ...,
        description="Whether root cause involves a code change"
    )

    # Evidence
    ruled_out_categories: list[RuledOutCategory] = Field(
        default_factory=list,
        description="Categories that were investigated and ruled out"
    )
    code_changes: list[CodeChange] = Field(
        default_factory=list,
        description="Relevant code changes found"
    )
    log_evidence: Optional[LogEvidence] = None

    # Remediation
    possible_fixes: list[PossibleFix] = Field(
        ...,
        min_length=1,
        description="Suggested fixes ordered by priority"
    )

    # Investigation trace (full audit trail)
    investigation_steps: list[InvestigationStep] = Field(
        ...,
        min_length=1,
        description="Complete step-by-step investigation trace"
    )

    # Classification result (for audit)
    initial_hypotheses: list[str] = Field(
        ...,
        description="Initial top-3 hypotheses from classification"
    )
