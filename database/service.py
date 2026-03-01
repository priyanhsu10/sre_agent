"""
Database service for storing and retrieving RCA reports.

Author: Alex (ARCHITECT) - Dashboard Enhancement
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from database.models import (
    Investigation, InvestigationError, Hypothesis, InvestigationStep,
    CodeChange, LogEvidence, JiraTicket, init_database, get_session
)
from sqlalchemy.orm import joinedload
from models.report import RCAReport

logger = logging.getLogger(__name__)


class ReportDatabaseService:
    """Service for storing and querying RCA reports in SQLite"""

    def __init__(self, db_url: str = "sqlite:///./reports.db"):
        self.engine = init_database(db_url)
        logger.info(f"Database initialized: {db_url}")

    def save_report(self, report: RCAReport) -> bool:
        """
        Save RCA report to database.

        Args:
            report: Complete RCA report

        Returns:
            True if saved successfully
        """
        session = get_session(self.engine)
        try:
            # Skip if already saved (idempotent)
            existing = session.query(Investigation).filter_by(id=report.report_id).first()
            if existing:
                logger.warning(f"Report {report.report_id} already in database, skipping save")
                return True

            # Create investigation record
            investigation = Investigation(
                id=report.report_id,
                app_name=report.app_name,
                severity=report.severity,
                environment=report.environment,
                alert_time=report.alert_time,
                created_at=report.generated_at,
                root_cause_category=report.root_cause_category.value,
                root_cause=report.root_cause,
                confidence_level=report.confidence_level.value,
                confidence_percentage=self._extract_confidence_percentage(report),
                is_code_change=report.is_code_change,
                possible_fixes=[
                    {
                        'priority': f.priority,
                        'action': f.action,
                        'rationale': f.rationale,
                        'estimated_impact': f.estimated_impact
                    }
                    for f in report.possible_fixes
                ],
                ruled_out_categories=[
                    {
                        'category': r.category.value,
                        'reason': r.reason,
                        'evidence': r.evidence
                    }
                    for r in report.ruled_out_categories
                ],
                status='completed'
            )
            session.add(investigation)

            # Save errors (if available in report)
            # Note: RCAReport doesn't include original errors, so we skip this for now
            # Future enhancement: pass original errors separately to this method
            # For now, error information is available in log_evidence if present

            # Save hypotheses
            for idx, hyp_str in enumerate(report.initial_hypotheses):
                # Parse hypothesis string: "category (percentage%)"
                parts = hyp_str.split(' (')
                if len(parts) == 2:
                    category = parts[0]
                    confidence = float(parts[1].rstrip('%)'))
                    hypothesis = Hypothesis(
                        investigation_id=report.report_id,
                        category=category,
                        confidence_percentage=confidence,
                        confidence_level=self._percentage_to_level(confidence),
                        rank=idx + 1
                    )
                    session.add(hypothesis)

            # Save investigation steps
            for step in report.investigation_steps:
                inv_step = InvestigationStep(
                    investigation_id=report.report_id,
                    step_number=step.step_number,
                    reasoning=step.reasoning,
                    decision=step.decision,
                    tool_called=step.tool_called.value if step.tool_called else None,
                    result_summary=step.result_summary or '',
                    timestamp=step.timestamp
                )
                session.add(inv_step)

            # Save code changes
            for change in report.code_changes:
                code_change = CodeChange(
                    investigation_id=report.report_id,
                    commit_hash=change.commit_hash,
                    author=change.author,
                    timestamp=change.timestamp,
                    message=change.message,
                    files_changed=change.files_changed,
                    jira_ticket=change.jira_ticket,
                    risk_flags=change.risk_flags
                )
                session.add(code_change)

            # Save log evidence
            if report.log_evidence:
                log_ev = LogEvidence(
                    investigation_id=report.report_id,
                    correlation_id=report.log_evidence.correlation_id,
                    evidence_path=report.log_evidence.evidence_path,
                    stack_traces=report.log_evidence.stack_traces,
                    key_log_lines=report.log_evidence.key_log_lines,
                    slow_queries=report.log_evidence.slow_queries,
                    total_error_count=report.log_evidence.total_error_count
                )
                session.add(log_ev)

            session.commit()
            logger.info(f"Report {report.report_id} saved to database")
            return True

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save report {report.report_id}: {e}", exc_info=True)
            return False
        finally:
            session.close()

    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        """Get single report by ID with full details"""
        session = get_session(self.engine)
        try:
            investigation = (
                session.query(Investigation)
                .filter_by(id=report_id)
                .first()
            )
            if not investigation:
                return None

            # Eagerly fetch log evidence
            log_ev = session.query(LogEvidence).filter_by(investigation_id=report_id).first()
            return self._investigation_to_full_dict(investigation, log_ev)
        finally:
            session.close()

    def list_reports(
        self,
        limit: int = 50,
        offset: int = 0,
        app_name: Optional[str] = None,
        severity: Optional[str] = None,
        environment: Optional[str] = None,
        category: Optional[str] = None,
        days: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        List reports with filters.

        Returns:
            Dict with 'total', 'reports' list
        """
        session = get_session(self.engine)
        try:
            query = session.query(Investigation)

            # Apply filters
            if app_name:
                query = query.filter(Investigation.app_name == app_name)
            if severity:
                query = query.filter(Investigation.severity == severity)
            if environment:
                query = query.filter(Investigation.environment == environment)
            if category:
                query = query.filter(Investigation.root_cause_category == category)
            if days:
                cutoff = datetime.utcnow() - timedelta(days=days)
                query = query.filter(Investigation.alert_time >= cutoff)

            # Get total count
            total = query.count()

            # Get paginated results
            investigations = query.order_by(desc(Investigation.alert_time)).limit(limit).offset(offset).all()

            reports = [self._investigation_to_dict(inv) for inv in investigations]

            return {
                'total': total,
                'limit': limit,
                'offset': offset,
                'reports': reports
            }
        finally:
            session.close()

    def get_statistics(self) -> Dict[str, Any]:
        """Get dashboard statistics"""
        session = get_session(self.engine)
        try:
            total_investigations = session.query(func.count(Investigation.id)).scalar()

            # Count by severity
            severity_counts = dict(
                session.query(Investigation.severity, func.count(Investigation.id))
                .group_by(Investigation.severity)
                .all()
            )

            # Count by category
            category_counts = dict(
                session.query(Investigation.root_cause_category, func.count(Investigation.id))
                .group_by(Investigation.root_cause_category)
                .all()
            )

            # Count code changes involved
            code_changes_count = session.query(func.count(Investigation.id)).filter(
                Investigation.is_code_change == True
            ).scalar()

            # Recent investigations (last 24 hours)
            cutoff_24h = datetime.utcnow() - timedelta(hours=24)
            recent_count = session.query(func.count(Investigation.id)).filter(
                Investigation.alert_time >= cutoff_24h
            ).scalar()

            # Top apps with most incidents
            top_apps = session.query(
                Investigation.app_name,
                func.count(Investigation.id).label('count')
            ).group_by(Investigation.app_name).order_by(desc('count')).limit(5).all()

            # Top authors in code changes
            top_authors = session.query(
                CodeChange.author,
                func.count(CodeChange.id).label('count')
            ).group_by(CodeChange.author).order_by(desc('count')).limit(5).all()

            return {
                'total_investigations': total_investigations,
                'severity_breakdown': severity_counts,
                'category_breakdown': category_counts,
                'code_changes_involved': code_changes_count,
                'recent_24h': recent_count,
                'top_apps': [{'app': app, 'count': count} for app, count in top_apps],
                'top_authors': [{'author': author, 'count': count} for author, count in top_authors]
            }
        finally:
            session.close()

    def search_by_error(self, error_text: str) -> List[Dict[str, Any]]:
        """Search investigations by error message"""
        session = get_session(self.engine)
        try:
            errors = session.query(InvestigationError).filter(
                InvestigationError.error_message.like(f'%{error_text}%')
            ).all()

            investigation_ids = list(set([e.investigation_id for e in errors]))

            investigations = session.query(Investigation).filter(
                Investigation.id.in_(investigation_ids)
            ).order_by(desc(Investigation.alert_time)).all()

            return [self._investigation_to_dict(inv) for inv in investigations]
        finally:
            session.close()

    def _investigation_to_dict(self, investigation: Investigation) -> Dict[str, Any]:
        """Convert Investigation ORM object to summary dict"""
        return {
            'id': investigation.id,
            'app_name': investigation.app_name,
            'severity': investigation.severity,
            'environment': investigation.environment,
            'alert_time': investigation.alert_time.isoformat(),
            'created_at': investigation.created_at.isoformat(),
            'root_cause_category': investigation.root_cause_category,
            'confidence_level': investigation.confidence_level,
            'confidence_percentage': investigation.confidence_percentage,
            'is_code_change': investigation.is_code_change,
            'status': investigation.status,
            'error_count': len(investigation.errors),
            'step_count': len(investigation.steps),
            'code_change_count': len(investigation.code_changes)
        }

    def _investigation_to_full_dict(self, investigation: Investigation, log_ev=None) -> Dict[str, Any]:
        """Convert Investigation ORM object to full detail dict including all related records"""
        base = self._investigation_to_dict(investigation)

        # Extra top-level fields
        base['root_cause'] = investigation.root_cause
        base['possible_fixes'] = investigation.possible_fixes or []
        base['ruled_out_categories'] = investigation.ruled_out_categories or []

        base['errors'] = [
            {
                'correlation_id': e.correlation_id,
                'error_message': e.error_message
            }
            for e in investigation.errors
        ]

        base['hypotheses'] = [
            {
                'rank': h.rank,
                'category': h.category,
                'confidence_percentage': h.confidence_percentage,
                'confidence_level': h.confidence_level,
                'reasoning': h.reasoning
            }
            for h in sorted(investigation.hypotheses, key=lambda h: h.rank or 99)
        ]

        base['investigation_steps'] = [
            {
                'step_number': s.step_number,
                'reasoning': s.reasoning,
                'decision': s.decision,
                'tool_called': s.tool_called,
                'result_summary': s.result_summary,
                'timestamp': s.timestamp.isoformat() if s.timestamp else None
            }
            for s in sorted(investigation.steps, key=lambda s: s.step_number)
        ]

        base['code_changes'] = [
            {
                'commit_hash': c.commit_hash,
                'author': c.author,
                'timestamp': c.timestamp.isoformat() if c.timestamp else None,
                'message': c.message,
                'files_changed': c.files_changed,
                'jira_ticket': c.jira_ticket,
                'risk_flags': c.risk_flags
            }
            for c in investigation.code_changes
        ]

        if log_ev:
            base['log_evidence'] = {
                'correlation_id': log_ev.correlation_id,
                'evidence_path': log_ev.evidence_path,
                'stack_traces': log_ev.stack_traces or [],
                'key_log_lines': log_ev.key_log_lines or [],
                'slow_queries': log_ev.slow_queries or [],
                'total_error_count': log_ev.total_error_count
            }
        else:
            base['log_evidence'] = None

        return base

    def _extract_confidence_percentage(self, report: RCAReport) -> float:
        """Extract confidence percentage from hypotheses"""
        if report.initial_hypotheses and len(report.initial_hypotheses) > 0:
            # Parse first hypothesis: "category (percentage%)"
            parts = report.initial_hypotheses[0].split(' (')
            if len(parts) == 2:
                return float(parts[1].rstrip('%)'))
        return 0.0

    def _percentage_to_level(self, percentage: float) -> str:
        """Convert percentage to confidence level"""
        if percentage >= 85:
            return 'confirmed'
        elif percentage >= 70:
            return 'high'
        elif percentage >= 40:
            return 'medium'
        else:
            return 'low'
