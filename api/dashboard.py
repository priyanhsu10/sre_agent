"""
Dashboard API endpoints for viewing RCA reports.

Author: Alex (ARCHITECT) - Dashboard Enhancement
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from database.service import ReportDatabaseService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Initialize database service
db_service = ReportDatabaseService()


class ReportSummary(BaseModel):
    """Summary of an investigation report"""
    id: str
    app_name: str
    severity: str
    environment: str
    alert_time: str
    root_cause_category: str
    confidence_level: str
    confidence_percentage: float
    is_code_change: bool
    error_count: int
    step_count: int
    code_change_count: int


class DashboardStats(BaseModel):
    """Dashboard statistics"""
    total_investigations: int
    severity_breakdown: dict
    category_breakdown: dict
    code_changes_involved: int
    recent_24h: int
    top_apps: List[dict]
    top_authors: List[dict]


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats():
    """
    Get dashboard statistics.

    Returns overall metrics for the dashboard.
    """
    try:
        stats = db_service.get_statistics()
        return stats
    except Exception as e:
        logger.error(f"Failed to get dashboard stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports")
async def list_reports(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    app_name: Optional[str] = None,
    severity: Optional[str] = None,
    environment: Optional[str] = None,
    category: Optional[str] = None,
    days: Optional[int] = None
):
    """
    List RCA reports with optional filters.

    Query Parameters:
    - limit: Maximum number of reports to return (1-100)
    - offset: Number of reports to skip
    - app_name: Filter by application name
    - severity: Filter by severity (critical, high, medium)
    - environment: Filter by environment (prod, staging)
    - category: Filter by root cause category
    - days: Filter by last N days
    """
    try:
        result = db_service.list_reports(
            limit=limit,
            offset=offset,
            app_name=app_name,
            severity=severity,
            environment=environment,
            category=category,
            days=days
        )
        return result
    except Exception as e:
        logger.error(f"Failed to list reports: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/{report_id}")
async def get_report(report_id: str):
    """
    Get detailed report by ID.

    Returns complete investigation details including:
    - Errors
    - Hypotheses
    - Investigation steps
    - Code changes
    - Log evidence
    """
    try:
        report = db_service.get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
        return report
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get report {report_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def search_reports(
    q: str = Query(..., min_length=3, description="Search query (error message)")
):
    """
    Search reports by error message.

    Query Parameters:
    - q: Search text (minimum 3 characters)
    """
    try:
        reports = db_service.search_by_error(q)
        return {
            'query': q,
            'count': len(reports),
            'reports': reports
        }
    except Exception as e:
        logger.error(f"Failed to search reports: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/apps")
async def list_apps():
    """
    Get list of all application names in database.

    Useful for filtering dropdowns.
    """
    try:
        stats = db_service.get_statistics()
        apps = [app['app'] for app in stats['top_apps']]
        return {'apps': apps}
    except Exception as e:
        logger.error(f"Failed to list apps: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
