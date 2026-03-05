"""
Remediation data models for automated code fix workflow.
"""

from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class RemediationStatus(str, Enum):
    pending = "pending"
    branch_created = "branch_created"
    fix_applied = "fix_applied"
    tests_running = "tests_running"
    tests_passed = "tests_passed"
    tests_failed = "tests_failed"
    pushed = "pushed"
    manual_instructions_created = "manual_instructions_created"  # Claude unavailable fallback
    failed = "failed"


class RemediationResult(BaseModel):
    """Result of an automated remediation attempt."""

    report_id: str = Field(..., description="RCA report that triggered this remediation")
    app_name: str = Field(..., description="Application being remediated")
    branch_name: str = Field(..., description="Git branch created for the fix")
    fix_type: str = Field(
        ...,
        description="Type of fix applied: 'revert' or 'claude_agent_patch'"
    )
    commit_hash_reverted: Optional[str] = Field(
        None,
        description="Commit hash that was reverted (revert fix only)"
    )
    fix_description: str = Field(..., description="Human-readable description of the fix applied")
    test_runtime: str = Field(
        ...,
        description="Detected runtime: 'python' | 'spring_boot' | 'react' | 'unknown'"
    )
    test_command: str = Field(..., description="Test command that was executed")
    tests_passed: bool = Field(default=False, description="Whether tests passed after fix")
    test_output: str = Field(
        default="",
        description="Last 2000 chars of test stdout/stderr"
    )
    fix_iterations: int = Field(
        default=1,
        ge=1,
        description="Number of agent iterations needed (claude_agent_patch only)"
    )
    claude_available: bool = Field(
        default=True,
        description="Whether Claude API was available at remediation time"
    )
    claude_unavailable_reason: Optional[str] = Field(
        None,
        description="Why Claude was unavailable (when claude_available=False)"
    )
    manual_instructions_file: Optional[str] = Field(
        None,
        description="Path to FIX_INSTRUCTIONS.md created when Claude is unavailable"
    )
    branch_pushed: bool = Field(default=False, description="Whether branch was pushed to remote")
    status: RemediationStatus = Field(default=RemediationStatus.pending)
    error_message: Optional[str] = Field(None, description="Error details if status=failed")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
