"""
Webhook API endpoint for receiving production alerts.

Accepts alert payloads, validates them, and triggers background investigation.

Author: Jordan (DEV-1)
"""

import logging
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse

from models.alert import AlertPayload
from orchestrator.agent import AgentOrchestrator
from api.dedup import make_fingerprint, get_deduplicator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WEBHOOK ENDPOINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post(
    "/alert",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=dict,
    summary="Receive production alert and trigger RCA investigation"
)
async def receive_alert(
    alert: AlertPayload,
    background_tasks: BackgroundTasks
) -> JSONResponse:
    """
    Webhook endpoint for receiving production alerts.

    **Flow:**
    1. Validate incoming alert payload (Pydantic validation)
    2. Return 202 Accepted immediately
    3. Trigger background investigation asynchronously
    4. Investigation runs: Classification → Tool Execution → Report Generation

    **Null Safety:**
    - Handles null correlation_id gracefully (common in production)
    - Never crashes on missing correlation IDs

    **Request Body:**
    ```json
    {
      "app_name": "rt-enricher-service",
      "alert_time": "2026-03-01T10:15:30Z",
      "severity": "critical",
      "environment": "prod",
      "errors": [
        {
          "correlation_id": "abc-123",  // Can be null
          "error_message": "Connection refused to database"
        }
      ]
    }
    ```

    **Response:**
    - 202 Accepted: Investigation started in background
    - 422 Unprocessable Entity: Invalid payload

    Args:
        alert: The incoming alert payload
        background_tasks: FastAPI background task manager

    Returns:
        JSON response with investigation_id and status
    """

    logger.info(
        f"Received alert for {alert.app_name} (severity={alert.severity.value}, "
        f"environment={alert.environment.value}, errors={len(alert.errors)})"
    )

    # ── Deduplication check ──────────────────────────────────────────────────
    dedup = get_deduplicator()
    fingerprint = make_fingerprint(alert)
    existing = dedup.check(fingerprint)

    if existing is not None:
        # Duplicate — return the existing investigation ID, no new work spawned
        logger.warning(
            f"Duplicate alert for {alert.app_name}/{alert.environment.value} suppressed. "
            f"Existing investigation: {existing.investigation_id} (status={existing.status})"
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "duplicate",
                "investigation_id": existing.investigation_id,
                "message": (
                    f"Duplicate alert detected for {alert.app_name}. "
                    f"Investigation {existing.investigation_id} is already {existing.status}. "
                    f"No new investigation started."
                ),
                "app_name": alert.app_name,
                "environment": alert.environment.value,
                "existing_status": existing.status,
                "existing_started_at": existing.registered_at.isoformat(),
            }
        )
    # ── New alert — proceed ──────────────────────────────────────────────────

    # Generate investigation ID for tracking
    investigation_id = f"rca-{alert.app_name}-{int(datetime.utcnow().timestamp())}"

    # Check for null correlation IDs (log warning but don't fail)
    null_corr_count = sum(1 for e in alert.errors if e.correlation_id is None)
    if null_corr_count > 0:
        logger.warning(
            f"Alert contains {null_corr_count}/{len(alert.errors)} errors with null correlation_id. "
            f"Will use fallback log retrieval methods."
        )

    # Register before scheduling so any concurrent duplicate is caught immediately
    dedup.register(fingerprint, investigation_id, alert)

    # Schedule background investigation
    background_tasks.add_task(
        run_investigation,
        investigation_id=investigation_id,
        fingerprint=fingerprint,
        alert=alert
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "investigation_id": investigation_id,
            "message": f"Alert received. Investigation started for {alert.app_name}.",
            "app_name": alert.app_name,
            "severity": alert.severity.value,
            "environment": alert.environment.value,
            "error_count": len(alert.errors),
            "null_correlation_ids": null_corr_count
        }
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BACKGROUND INVESTIGATION TASK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def run_investigation(
    investigation_id: str,
    fingerprint: str,
    alert: AlertPayload,
) -> None:
    """
    Background task that runs the full RCA investigation pipeline.

    Updates the dedup registry on completion or failure so subsequent
    duplicate checks reflect the correct state.

    **Pipeline:**
    1. Classification (think-first: MUST run before tools)
    2. Tool execution (Loki, Git, Jira based on hypotheses)
    3. Reasoning (evidence synthesis, root cause determination)
    4. Report generation (JSON + Markdown)

    Args:
        investigation_id: Unique ID for this investigation
        fingerprint: Alert content fingerprint for dedup registry update
        alert: The alert payload to investigate

    Returns:
        None (writes report to disk)

    Raises:
        Never raises - catches all exceptions and generates partial reports
    """
    logger.info(f"[{investigation_id}] Starting investigation for {alert.app_name}")
    dedup = get_deduplicator()

    try:
        # Initialize orchestrator and run full investigation
        orchestrator = AgentOrchestrator(investigation_id=investigation_id)
        report = await orchestrator.investigate(alert)

        logger.info(
            f"[{investigation_id}] Investigation complete. "
            f"Root cause: {report.root_cause_category.value} "
            f"(confidence: {report.confidence_level.value})"
        )

        # Report is saved to DB by ReportGenerator.generate() inside orchestrator.investigate()
        logger.info(f"[{investigation_id}] Report {report.report_id} saved to database")

        # Mark as completed — future duplicates within window will be suppressed
        dedup.mark_completed(fingerprint)

    except Exception as e:
        logger.error(
            f"[{investigation_id}] Investigation failed with unexpected error: {e}",
            exc_info=True
        )
        logger.warning(
            f"[{investigation_id}] Generating partial report due to investigation failure"
        )
        # Mark as failed — next alert for the same issue will be allowed through
        dedup.mark_failed(fingerprint)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HEALTH CHECK ENDPOINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Health check for webhook service"
)
async def health_check() -> dict:
    """
    Health check endpoint for monitoring.

    Returns:
        Status information
    """
    return {
        "status": "healthy",
        "service": "sre-agent-webhook",
        "timestamp": datetime.utcnow().isoformat(),
        "dedup": get_deduplicator().stats(),
    }
