"""
Report Generator - Produces JSON and Markdown RCA reports.

Author: Riley (DEV-3)
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import List

from models.alert import AlertPayload
from models.hypothesis import ClassificationResult
from models.tool_result import ToolResult
from models.report import (
    RCAReport,
    InvestigationStep,
    RuledOutCategory,
    CodeChange,
    LogEvidence,
    PossibleFix
)
from config import Settings

# Database service for storing reports
try:
    from database.service import ReportDatabaseService
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    logger.warning("Database service not available. Reports will only be saved to files.")

logger = logging.getLogger(__name__)


class ReportGenerator:
    """
    Generates RCA reports in JSON and Markdown formats.

    **Outputs:**
    - JSON: Machine-readable, complete data
    - Markdown: Human-readable, formatted for readability
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.output_dir = Path(settings.REPORT_OUTPUT_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database service if available
        if DB_AVAILABLE:
            try:
                self.db_service = ReportDatabaseService()
                logger.info("Database service initialized for report storage")
            except Exception as e:
                logger.warning(f"Failed to initialize database service: {e}")
                self.db_service = None
        else:
            self.db_service = None

    def generate(self, report: RCAReport) -> tuple[Path, Path]:
        """
        Generate both JSON and Markdown reports.

        Args:
            report: The RCA report object

        Returns:
            (json_path, markdown_path)
        """
        # Generate filenames
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        base_name = f"{report.report_id}"

        json_path = self.output_dir / f"{base_name}.json"
        md_path = self.output_dir / f"{base_name}.md"

        # Write JSON report
        self._write_json_report(report, json_path)

        # Write Markdown report
        self._write_markdown_report(report, md_path)

        # Save to database if available
        if self.db_service:
            try:
                success = self.db_service.save_report(report)
                if success:
                    logger.info(f"Report {report.report_id} saved to database")
                else:
                    logger.warning(f"Failed to save report {report.report_id} to database")
            except Exception as e:
                logger.error(f"Error saving report to database: {e}", exc_info=True)

        logger.info(f"Reports generated: {json_path} and {md_path}")

        return json_path, md_path

    def _write_json_report(self, report: RCAReport, path: Path) -> None:
        """Write JSON report"""
        # Convert Pydantic model to dict
        report_dict = report.model_dump(mode='json')

        with open(path, 'w') as f:
            json.dump(report_dict, f, indent=2, default=str)

        logger.info(f"JSON report written to {path}")

    def _write_markdown_report(self, report: RCAReport, path: Path) -> None:
        """Write Markdown report"""
        md_content = self._generate_markdown(report)

        with open(path, 'w') as f:
            f.write(md_content)

        logger.info(f"Markdown report written to {path}")

    def _generate_markdown(self, report: RCAReport) -> str:
        """Generate Markdown content"""
        sections = []

        # Header
        sections.append(f"# Root Cause Analysis Report")
        sections.append(f"\n**Report ID:** `{report.report_id}`")
        sections.append(f"**Generated:** {report.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        sections.append("\n---\n")

        # Executive Summary
        sections.append("## Executive Summary")
        sections.append(f"\n**Root Cause:** {report.root_cause}")
        sections.append(f"\n**Category:** `{report.root_cause_category.value}`")
        sections.append(f"\n**Confidence:** {report.confidence_level.value}")
        sections.append(f"\n**Code Change Involved:** {'Yes' if report.is_code_change else 'No'}")
        sections.append("\n---\n")

        # Alert Details
        sections.append("## Alert Details")
        sections.append(f"\n- **Application:** {report.app_name}")
        sections.append(f"- **Severity:** {report.severity}")
        sections.append(f"- **Environment:** {report.environment}")
        sections.append(f"- **Alert Time:** {report.alert_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        sections.append("\n---\n")

        # Initial Hypotheses
        sections.append("## Initial Hypotheses")
        sections.append("\nClassification engine identified the following top hypotheses:")
        for i, hyp in enumerate(report.initial_hypotheses, 1):
            sections.append(f"\n{i}. {hyp}")
        sections.append("\n---\n")

        # Root Cause Analysis
        sections.append("## Root Cause Analysis")
        sections.append(f"\n{report.root_cause}")
        sections.append("\n---\n")

        # Ruled-Out Categories
        if report.ruled_out_categories:
            sections.append("## Ruled-Out Categories")
            sections.append("\nThe following failure categories were investigated and ruled out:")
            for ruled_out in report.ruled_out_categories[:5]:  # Top 5
                sections.append(f"\n### {ruled_out.category.value}")
                sections.append(f"\n- **Reason:** {ruled_out.reason}")
                sections.append(f"- **Evidence:** {ruled_out.evidence}\n")
            sections.append("\n---\n")

        # Code Changes
        if report.code_changes:
            sections.append("## Code Changes")
            sections.append(f"\nFound {len(report.code_changes)} recent code change(s):")
            for change in report.code_changes[:5]:  # Top 5
                sections.append(f"\n### Commit `{change.commit_hash}`")
                sections.append(f"\n- **Author:** {change.author}")
                sections.append(f"- **Time:** {change.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                sections.append(f"- **Message:** {change.message}")
                if change.jira_ticket:
                    sections.append(f"- **Jira:** {change.jira_ticket}")
                if change.risk_flags:
                    sections.append(f"- **Risk Flags:** {', '.join(change.risk_flags)}")
                sections.append(f"- **Files Changed:** {len(change.files_changed)} file(s)")
                if change.files_changed:
                    for file in change.files_changed[:5]:
                        sections.append(f"  - `{file}`")
                sections.append("")
            sections.append("\n---\n")

        # Log Evidence
        if report.log_evidence:
            sections.append("## Log Evidence")
            log = report.log_evidence
            if log.correlation_id:
                sections.append(f"\n**Correlation ID:** `{log.correlation_id}`")
            sections.append(f"\n**Evidence Path:** {log.evidence_path}")
            sections.append(f"\n**Total Errors:** {log.total_error_count}")

            if log.stack_traces:
                sections.append(f"\n### Stack Traces ({len(log.stack_traces)})")
                for i, trace in enumerate(log.stack_traces[:3], 1):  # Top 3
                    sections.append(f"\n**Trace {i}:**")
                    sections.append(f"\n```\n{trace[:500]}\n```\n")

            if log.slow_queries:
                sections.append(f"\n### Slow Queries ({len(log.slow_queries)})")
                for i, query in enumerate(log.slow_queries[:5], 1):  # Top 5
                    sections.append(f"\n{i}. `{query[:200]}`")

            if log.key_log_lines:
                sections.append(f"\n### Key Error Log Lines ({len(log.key_log_lines)})")
                for i, line in enumerate(log.key_log_lines[:10], 1):  # Top 10
                    sections.append(f"\n{i}. `{line[:200]}`")

            sections.append("\n---\n")

        # Possible Fixes
        sections.append("## Recommended Fixes")
        sections.append("\nOrdered by priority (1 = most urgent):")
        for fix in report.possible_fixes:
            sections.append(f"\n### Priority {fix.priority}: {fix.action}")
            sections.append(f"\n- **Rationale:** {fix.rationale}")
            sections.append(f"- **Estimated Impact:** {fix.estimated_impact}\n")
        sections.append("\n---\n")

        # Investigation Trace
        sections.append("## Investigation Trace")
        sections.append("\nComplete step-by-step investigation log:")
        for step in report.investigation_steps:
            sections.append(f"\n### Step {step.step_number}: {step.decision}")
            sections.append(f"\n**Reasoning:** {step.reasoning}")
            if step.tool_called:
                sections.append(f"\n**Tool Called:** {step.tool_called.value}")
            if step.result_summary:
                sections.append(f"\n**Result:** {step.result_summary}")
            if step.hypothesis_update:
                sections.append(f"\n**Hypothesis Update:** {step.hypothesis_update}")
            sections.append(f"\n**Timestamp:** {step.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        sections.append("\n---\n")

        # Footer
        sections.append("\n*Report generated by SRE Agent - Smart Root Cause Analyser*")
        sections.append(f"\n*Powered by Claude Code*")

        return "\n".join(sections)
