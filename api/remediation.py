"""
Remediation API - Manual trigger and status endpoints for code fix workflow.

POST /remediation/{report_id}  → trigger remediation for an existing RCA report
GET  /remediation/{report_id}  → get remediation result/status
"""

import logging
from datetime import datetime
from typing import Dict

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from models.remediation import RemediationResult, RemediationStatus
from database.service import ReportDatabaseService
from models.report import RCAReport
from models.hypothesis import FailureCategory, ConfidenceLevel
from models.report import PossibleFix

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/remediation", tags=["remediation"])

# In-memory store for remediation results (keyed by report_id)
_remediation_results: Dict[str, RemediationResult] = {}

# Shared DB service
_db = ReportDatabaseService()


@router.post(
    "/{report_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger automated code fix for an RCA report"
)
async def trigger_remediation(
    report_id: str,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Manually trigger the remediation workflow for an existing RCA report.

    The workflow:
    1. Fetch report from database
    2. Create a fix branch in the app's git repo
    3. Apply fix (revert commit or Claude agent patch)
    4. Run tests
    5. Push branch if tests pass

    Returns 202 immediately; check GET /remediation/{report_id} for status.
    """
    # Check if already running / completed
    if report_id in _remediation_results:
        existing = _remediation_results[report_id]
        if existing.status not in (RemediationStatus.failed, RemediationStatus.tests_failed):
            return {
                "status": "already_triggered",
                "report_id": report_id,
                "remediation_status": existing.status.value,
                "message": f"Remediation already in progress or completed for {report_id}",
            }

    # Fetch report from DB
    report_data = _db.get_report(report_id)
    if not report_data:
        raise HTTPException(
            status_code=404,
            detail=f"Report '{report_id}' not found. Run investigation first."
        )

    report = _build_report_from_db(report_data, report_id)

    # Set pending state immediately
    _remediation_results[report_id] = RemediationResult(
        report_id=report_id,
        app_name=report.app_name,
        branch_name="",
        fix_type="pending",
        fix_description="Queued for processing",
        test_runtime="unknown",
        test_command="",
        status=RemediationStatus.pending,
    )

    background_tasks.add_task(_run_remediation_task, report_id, report)

    logger.info(f"Remediation triggered for report {report_id} (app={report.app_name})")

    return {
        "status": "accepted",
        "report_id": report_id,
        "app_name": report.app_name,
        "message": f"Remediation workflow started for {report_id}",
        "check_status": f"GET /remediation/{report_id}",
    }


@router.get(
    "/{report_id}",
    summary="Get remediation status and result"
)
async def get_remediation_status(report_id: str) -> dict:
    """
    Get the current status and result of a remediation workflow.
    """
    if report_id not in _remediation_results:
        raise HTTPException(
            status_code=404,
            detail=f"No remediation found for report '{report_id}'. "
                   f"Trigger via POST /remediation/{report_id}"
        )

    result = _remediation_results[report_id]
    return result.model_dump()


async def _run_remediation_task(report_id: str, report: RCAReport) -> None:
    """Background task that runs the remediation agent."""
    try:
        from remediation.agent import RemediationAgent
        agent = RemediationAgent()
        result = await agent.run(report)
        _remediation_results[report_id] = result
        logger.info(
            f"Remediation complete for {report_id}: "
            f"status={result.status.value}, pushed={result.branch_pushed}"
        )
    except Exception as e:
        logger.error(f"Remediation task failed for {report_id}: {e}", exc_info=True)
        _remediation_results[report_id] = RemediationResult(
            report_id=report_id,
            app_name=report.app_name,
            branch_name="",
            fix_type="unknown",
            fix_description="",
            test_runtime="unknown",
            test_command="",
            status=RemediationStatus.failed,
            error_message=str(e),
            completed_at=datetime.utcnow(),
        )


def _build_report_from_db(report_data: dict, report_id: str) -> RCAReport:
    """Build an RCAReport from the DB dictionary returned by get_report()."""
    from models.report import CodeChange, LogEvidence, RuledOutCategory, InvestigationStep
    from models.tool_result import ToolName
    from datetime import datetime as dt

    def _parse_dt(val):
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            return dt.fromisoformat(val.replace("Z", "+00:00"))
        return dt.utcnow()

    # Build code_changes
    code_changes = []
    for cc in report_data.get("code_changes", []):
        code_changes.append(CodeChange(
            commit_hash=cc.get("commit_hash", ""),
            author=cc.get("author", ""),
            timestamp=_parse_dt(cc.get("timestamp", dt.utcnow())),
            message=cc.get("message", ""),
            files_changed=cc.get("files_changed", []),
            jira_ticket=cc.get("jira_ticket"),
            risk_flags=cc.get("risk_flags", []),
        ))

    # Build possible_fixes
    possible_fixes = []
    for pf in report_data.get("possible_fixes", []):
        possible_fixes.append(PossibleFix(
            priority=pf.get("priority", 99),
            action=pf.get("action", ""),
            rationale=pf.get("rationale", ""),
            estimated_impact=pf.get("estimated_impact", ""),
        ))
    if not possible_fixes:
        possible_fixes = [PossibleFix(
            priority=1, action="Manual investigation required",
            rationale="No fixes available", estimated_impact="Unknown"
        )]

    # Build investigation_steps
    steps = []
    for s in report_data.get("steps", []):
        tool_name = None
        if s.get("tool_called"):
            try:
                tool_name = ToolName(s["tool_called"])
            except ValueError:
                pass
        steps.append(InvestigationStep(
            step_number=s.get("step_number", 1),
            reasoning=s.get("reasoning", "recovered from db"),
            decision=s.get("decision", ""),
            tool_called=tool_name,
            result_summary=s.get("result_summary"),
            hypothesis_update=s.get("hypothesis_update"),
            timestamp=_parse_dt(s.get("timestamp", dt.utcnow())),
        ))
    if not steps:
        steps = [InvestigationStep(
            step_number=1,
            reasoning="Recovered from database",
            decision="Report loaded from DB",
            tool_called=None,
        )]

    # Parse enums safely
    try:
        category = FailureCategory(report_data.get("root_cause_category", "code_logic_error"))
    except ValueError:
        category = FailureCategory.CODE_LOGIC_ERROR

    try:
        confidence = ConfidenceLevel(report_data.get("confidence_level", "Low"))
    except ValueError:
        confidence = ConfidenceLevel.LOW

    return RCAReport(
        report_id=report_id,
        generated_at=_parse_dt(report_data.get("created_at", dt.utcnow())),
        app_name=report_data.get("app_name", ""),
        alert_time=_parse_dt(report_data.get("alert_time", dt.utcnow())),
        severity=report_data.get("severity", "high"),
        environment=report_data.get("environment", "prod"),
        root_cause=report_data.get("root_cause", ""),
        root_cause_category=category,
        confidence_level=confidence,
        is_code_change=report_data.get("is_code_change", False),
        ruled_out_categories=[],
        code_changes=code_changes,
        log_evidence=None,
        possible_fixes=possible_fixes,
        investigation_steps=steps,
        initial_hypotheses=report_data.get("initial_hypotheses", []),
    )
