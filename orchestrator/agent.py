"""
Agent Orchestrator - Central coordinator for RCA investigation pipeline.

Enforces think-first protocol, coordinates tool execution, and produces reports.

Author: Jordan (DEV-1)
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from models.alert import AlertPayload
from models.hypothesis import ClassificationResult, FailureCategory, ConfidenceLevel
from models.report import RCAReport, InvestigationStep, PossibleFix
from models.tool_result import ToolResult, ToolName
from classifier.engine import ClassificationEngine
from config import settings

# LLM-enhanced classifier (optional)
try:
    from classifier.llm_classifier import LLMEnhancedClassifier
    from llm.client import LLMConfig, LLMProvider
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    logger.warning("LLM modules not available. Using pattern-only classification.")

# Reasoning and report components
from reasoning.engine import ReasoningEngine
from reasoning.synthesis import SynthesisEngine
from reasoning.llm_synthesis import LLMSynthesisEngine
from report.generator import ReportGenerator
from report.fixes import PossibleFixesGenerator

# Investigation tools
from tools.loki import LokiLogRetriever
from tools.git_blame import GitBlameChecker
from tools.jira import JiraTicketGetter
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """
    Central orchestrator for the RCA investigation pipeline.

    **Critical Responsibilities:**
    1. ENFORCE think-first protocol (classification before tools)
    2. Coordinate tool execution based on reasoning decisions
    3. Build complete investigation trace
    4. Generate final RCA report (even on partial failure)

    **Pipeline Flow:**
    Alert → Classification → Reasoning Loop → Report Generation
              (REQUIRED)      (Tool calls)      (Always runs)
    """

    def __init__(self, investigation_id: str):
        """
        Initialize the orchestrator.

        Args:
            investigation_id: Unique ID for this investigation
        """
        self.investigation_id = investigation_id

        # Initialize classifier (LLM-enhanced if enabled)
        llm_config = None
        if settings.LLM_ENABLED and LLM_AVAILABLE and settings.LLM_API_KEY:
            provider = LLMProvider(settings.LLM_PROVIDER)
            logger.info(f"LLM-enhanced classification enabled (provider={provider.value})")
            llm_config = LLMConfig(
                provider=provider,
                api_key=settings.LLM_API_KEY,
                model=settings.LLM_MODEL,
                max_tokens=settings.LLM_MAX_TOKENS,
                temperature=settings.LLM_TEMPERATURE,
                timeout=settings.LLM_TIMEOUT,
                base_url=settings.LLM_BASE_URL or None,
            )
            self.classifier = LLMEnhancedClassifier(llm_config)
        else:
            logger.info("Using pattern-only classification")
            self.classifier = ClassificationEngine()

        # Investigation state
        self.investigation_trace: List[InvestigationStep] = []
        self.classification_result: Optional[ClassificationResult] = None
        self.tool_results: Dict[str, ToolResult] = {}
        self.tools_called: List[ToolName] = []

        # Initialize tools
        self.tools: Dict[ToolName, BaseTool] = {
            ToolName.LOKI: LokiLogRetriever(settings),
            ToolName.GIT_BLAME: GitBlameChecker(settings),
            ToolName.JIRA: JiraTicketGetter(settings),
        }

        # Initialize reasoning and reporting components
        self.reasoning_engine = ReasoningEngine(settings)

        # Initialize synthesis engine (LLM-enhanced if enabled)
        if llm_config:
            logger.info("LLM-enhanced synthesis enabled")
            self.synthesis_engine = LLMSynthesisEngine(llm_config)
        else:
            logger.info("Using rule-based synthesis")
            self.synthesis_engine = SynthesisEngine()

        self.fixes_generator = PossibleFixesGenerator()
        self.report_generator = ReportGenerator(settings)

        logger.info(f"[{self.investigation_id}] AgentOrchestrator initialized")

    async def investigate(self, alert: AlertPayload) -> RCAReport:
        """
        Main orchestration method - coordinates the entire investigation pipeline.

        **NON-NEGOTIABLE RULES:**
        1. Classification MUST run first and be recorded in investigation_trace
        2. NO tool calls before classification completes
        3. MUST produce a report even if tools fail
        4. Null correlation_id must NEVER crash the pipeline

        Args:
            alert: The incoming alert payload

        Returns:
            Complete RCA report with all evidence and reasoning

        Raises:
            Never raises - returns partial report on failure
        """
        logger.info(
            f"[{self.investigation_id}] Starting investigation for {alert.app_name}"
        )

        try:
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # STEP 1: CLASSIFICATION (THINK-FIRST GATE)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # This MUST happen before any tool is called.
            # Alex enforces this at the architecture level.

            logger.info(f"[{self.investigation_id}] STEP 1: Running classification...")
            self.classification_result = await self.classify_failure(alert)

            # Record classification as first investigation step
            classification_step = InvestigationStep(
                step_number=1,
                reasoning=(
                    f"First step: Classify the alert to understand failure patterns. "
                    f"Analyzed {len(alert.errors)} error message(s) across 8 failure categories. "
                    f"{self.classification_result.classification_reasoning}"
                ),
                decision=f"Classification complete. Top hypothesis: {self.classification_result.top_hypotheses[0].category.value}",
                tool_called=None,  # Classification is not a tool
                result_summary=(
                    f"Top 3 hypotheses: "
                    f"1) {self.classification_result.top_hypotheses[0].category.value} ({self.classification_result.top_hypotheses[0].confidence_percentage}%), "
                    f"2) {self.classification_result.top_hypotheses[1].category.value if len(self.classification_result.top_hypotheses) > 1 else 'N/A'} "
                    f"({self.classification_result.top_hypotheses[1].confidence_percentage if len(self.classification_result.top_hypotheses) > 1 else 0}%), "
                    f"3) {self.classification_result.top_hypotheses[2].category.value if len(self.classification_result.top_hypotheses) > 2 else 'N/A'} "
                    f"({self.classification_result.top_hypotheses[2].confidence_percentage if len(self.classification_result.top_hypotheses) > 2 else 0}%)"
                ),
                hypothesis_update=None,
                timestamp=datetime.utcnow()
            )
            self.investigation_trace.append(classification_step)

            logger.info(
                f"[{self.investigation_id}] Classification complete. "
                f"Top hypothesis: {self.classification_result.top_hypotheses[0].category.value} "
                f"({self.classification_result.top_hypotheses[0].confidence_percentage}%)"
            )

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # STEP 2: REASONING LOOP (Tool Execution)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # ReasoningEngine determines which tools to call and when to stop

            logger.info(f"[{self.investigation_id}] STEP 2: Starting reasoning loop...")

            step_number = 2
            max_steps = 10  # Safety limit

            while step_number <= max_steps:
                # Get next decision from reasoning engine
                reasoning_step = self.reasoning_engine.step(
                    current_step=step_number,
                    classification=self.classification_result,
                    tools_called=self.tools_called,
                    tool_results=self.tool_results
                )

                # Add step to trace
                self.investigation_trace.append(reasoning_step)

                # Check if we're done
                if reasoning_step.tool_called is None:
                    logger.info(
                        f"[{self.investigation_id}] Reasoning complete: {reasoning_step.decision}"
                    )
                    break

                # Execute the tool
                logger.info(
                    f"[{self.investigation_id}] Step {step_number}: "
                    f"Calling {reasoning_step.tool_called.value}"
                )

                tool_result = await self._execute_tool(
                    tool_name=reasoning_step.tool_called,
                    alert=alert
                )

                # Store result
                self.tool_results[reasoning_step.tool_called.value] = tool_result
                self.tools_called.append(reasoning_step.tool_called)

                # Update the step with result summary
                reasoning_step.result_summary = self._summarize_tool_result(tool_result)
                reasoning_step.hypothesis_update = self.reasoning_engine.update_hypothesis_confidence(
                    classification=self.classification_result,
                    tool_results=self.tool_results
                )

                logger.info(
                    f"[{self.investigation_id}] {reasoning_step.tool_called.value} "
                    f"completed in {tool_result.duration_ms:.0f}ms "
                    f"(success={tool_result.success})"
                )

                step_number += 1

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # STEP 3: SYNTHESIS & REPORT GENERATION
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Synthesize evidence and generate final report
            # Uses LLM for intelligent synthesis if enabled

            logger.info(f"[{self.investigation_id}] STEP 3: Synthesizing evidence...")

            # Synthesize root cause from all evidence (LLM-enhanced if enabled)
            root_cause, category, confidence_level, is_code_change = \
                await self.synthesis_engine.synthesize_root_cause(
                    classification=self.classification_result,
                    tool_results=self.tool_results
                )

            # Build ruled-out categories
            ruled_out = self.synthesis_engine.build_ruled_out_categories(
                classification=self.classification_result,
                tool_results=self.tool_results
            )

            # Extract code changes
            code_changes = self.synthesis_engine.extract_code_changes(
                tool_results=self.tool_results
            )

            # Extract log evidence
            correlation_ids = [e.correlation_id for e in alert.errors]
            log_evidence = self.synthesis_engine.extract_log_evidence(
                tool_results=self.tool_results,
                correlation_ids=correlation_ids
            )

            # Generate possible fixes
            possible_fixes = self.fixes_generator.generate_fixes(
                root_cause_category=category,
                is_code_change=is_code_change,
                code_changes=code_changes,
                evidence={}
            )

            # Build final report
            report = RCAReport(
                report_id=self.investigation_id,
                generated_at=datetime.utcnow(),
                app_name=alert.app_name,
                alert_time=alert.alert_time,
                severity=alert.severity.value,
                environment=alert.environment.value,
                root_cause=root_cause,
                root_cause_category=category,
                confidence_level=confidence_level,
                is_code_change=is_code_change,
                ruled_out_categories=ruled_out,
                code_changes=code_changes,
                log_evidence=log_evidence,
                possible_fixes=possible_fixes,
                investigation_steps=self.investigation_trace,
                initial_hypotheses=[
                    f"{h.category.value} ({h.confidence_percentage}%)"
                    for h in self.classification_result.top_hypotheses
                ]
            )

            # Generate report files
            json_path, md_path = self.report_generator.generate(report)

            logger.info(
                f"[{self.investigation_id}] Investigation complete. "
                f"Reports written to {json_path} and {md_path}"
            )

            # Auto-remediation: trigger in background if conditions are met
            if settings.AUTO_REMEDIATION_ENABLED and self._should_remediate(report):
                asyncio.create_task(self._run_remediation(report))

            return report

        except Exception as e:
            logger.error(
                f"[{self.investigation_id}] Investigation failed: {e}",
                exc_info=True
            )
            # Generate partial report even on failure
            return self._generate_error_report(alert, error=str(e))

    async def classify_failure(self, alert: AlertPayload) -> ClassificationResult:
        """
        Run the classification engine and return ranked hypotheses.

        This is the FIRST step in any investigation and MUST complete
        before any tool is called.

        Args:
            alert: The alert payload

        Returns:
            ClassificationResult with top 3 hypotheses

        Raises:
            Never raises - classification errors are logged and re-raised
        """
        try:
            result = await self.classifier.classify(alert)
            logger.info(
                f"[{self.investigation_id}] Classification completed in "
                f"{result.classification_duration_ms:.2f}ms"
            )
            return result
        except Exception as e:
            logger.error(
                f"[{self.investigation_id}] Classification failed: {e}",
                exc_info=True
            )
            raise

    async def _execute_tool(
        self,
        tool_name: ToolName,
        alert: AlertPayload
    ) -> ToolResult:
        """
        Execute a tool and return its result.

        Args:
            tool_name: Tool to execute
            alert: Alert payload

        Returns:
            ToolResult
        """
        tool = self.tools[tool_name]

        # Build context from previous tool results
        context = {}

        # Pass Jira keys from Git to Jira tool
        if tool_name == ToolName.JIRA and ToolName.GIT_BLAME.value in self.tool_results:
            git_result = self.tool_results[ToolName.GIT_BLAME.value]
            if git_result.success and git_result.data:
                context["jira_keys"] = git_result.data.get("jira_keys", [])

        try:
            result = await tool.execute(alert, context)
            return result
        except Exception as e:
            # This should never happen (tools should catch all exceptions)
            # but provide fallback just in case
            logger.error(f"Unexpected error executing {tool_name.value}: {e}", exc_info=True)
            return ToolResult(
                tool_name=tool_name,
                success=False,
                data=None,
                error_message=f"Unexpected error: {str(e)}",
                duration_ms=0.0,
                evidence_path=None,
                timestamp=datetime.utcnow()
            )

    def _summarize_tool_result(self, result: ToolResult) -> str:
        """
        Create a brief summary of tool result.

        Args:
            result: ToolResult to summarize

        Returns:
            Summary text
        """
        if not result.success:
            return f"{result.tool_name.value} failed: {result.error_message}"

        if result.tool_name == ToolName.LOKI:
            data = result.data or {}
            return (
                f"Retrieved {data.get('total_lines_retrieved', 0)} log lines, "
                f"{len(data.get('stack_traces', []))} stack traces, "
                f"{len(data.get('slow_queries', []))} slow queries"
            )

        elif result.tool_name == ToolName.GIT_BLAME:
            data = result.data or {}
            return (
                f"Found {data.get('total_commits', 0)} commits, "
                f"{len(data.get('high_churn_files', []))} high-churn files, "
                f"{len(data.get('jira_keys', []))} Jira keys"
            )

        elif result.tool_name == ToolName.JIRA:
            data = result.data or {}
            return (
                f"Retrieved {data.get('total_tickets', 0)} tickets, "
                f"{data.get('risk_flagged_count', 0)} with risk flags"
            )

        return f"{result.tool_name.value} completed successfully"

    def _should_remediate(self, report: RCAReport) -> bool:
        """
        Determine if automated remediation should be triggered.

        Conditions:
          - is_code_change=True AND confidence >= AUTO_REMEDIATION_MIN_CONFIDENCE → revert
          - code_logic_error AND LLM_ENABLED → claude_agent_patch
        """
        min_conf = settings.AUTO_REMEDIATION_MIN_CONFIDENCE  # "High" or "Confirmed"
        confidence_order = {
            ConfidenceLevel.LOW.value: 0,
            ConfidenceLevel.MEDIUM.value: 1,
            ConfidenceLevel.HIGH.value: 2,
            ConfidenceLevel.CONFIRMED.value: 3,
        }
        min_threshold = confidence_order.get(min_conf, 2)
        current = confidence_order.get(report.confidence_level.value, 0)

        if report.is_code_change and current >= min_threshold:
            return True

        if (
            report.root_cause_category == FailureCategory.CODE_LOGIC_ERROR
            and settings.LLM_ENABLED
            and current >= min_threshold
        ):
            return True

        return False

    async def _run_remediation(self, report: RCAReport) -> None:
        """Background task: run remediation agent and log result."""
        try:
            from remediation.agent import RemediationAgent
            agent = RemediationAgent()
            result = await agent.run(report)
            logger.info(
                f"[{self.investigation_id}] Remediation complete: "
                f"status={result.status.value}, branch={result.branch_name}, "
                f"tests_passed={result.tests_passed}, pushed={result.branch_pushed}"
            )
        except Exception as e:
            logger.error(
                f"[{self.investigation_id}] Remediation failed unexpectedly: {e}",
                exc_info=True
            )

    def _generate_placeholder_report(self, alert: AlertPayload) -> RCAReport:
        """
        Generate a placeholder report until Riley implements the real ReportGenerator.

        Args:
            alert: The alert payload

        Returns:
            Placeholder RCA report
        """
        top_category = self.classification_result.top_hypotheses[0].category

        return RCAReport(
            report_id=self.investigation_id,
            generated_at=datetime.utcnow(),
            app_name=alert.app_name,
            alert_time=alert.alert_time,
            severity=alert.severity.value,
            environment=alert.environment.value,
            root_cause=f"Placeholder: Likely {top_category.value} based on classification",
            root_cause_category=top_category,
            confidence_level=self.classification_result.top_hypotheses[0].confidence_level,
            is_code_change=False,  # Unknown until Git tool runs
            ruled_out_categories=[],
            code_changes=[],
            log_evidence=None,
            possible_fixes=[
                PossibleFix(
                    priority=1,
                    action="Placeholder: Run full investigation with tools (Phase 3-4)",
                    rationale="Tools not yet integrated",
                    estimated_impact="N/A - placeholder report"
                )
            ],
            investigation_steps=self.investigation_trace,
            initial_hypotheses=[
                f"{h.category.value} ({h.confidence_percentage}%)"
                for h in self.classification_result.top_hypotheses
            ]
        )

    def _generate_error_report(self, alert: AlertPayload, error: str) -> RCAReport:
        """
        Generate a partial report when investigation fails.

        This ensures we NEVER silently fail - always produce output.

        Args:
            alert: The alert payload
            error: Error message

        Returns:
            Partial RCA report with error information
        """
        return RCAReport(
            report_id=self.investigation_id,
            generated_at=datetime.utcnow(),
            app_name=alert.app_name,
            alert_time=alert.alert_time,
            severity=alert.severity.value,
            environment=alert.environment.value,
            root_cause=f"Investigation failed: {error}",
            root_cause_category=FailureCategory.CODE_LOGIC_ERROR,  # Default
            confidence_level=ConfidenceLevel.LOW,
            is_code_change=False,
            ruled_out_categories=[],
            code_changes=[],
            log_evidence=None,
            possible_fixes=[
                PossibleFix(
                    priority=1,
                    action="Review investigation logs for error details",
                    rationale=f"Investigation failed with error: {error}",
                    estimated_impact="Manual investigation required"
                )
            ],
            investigation_steps=self.investigation_trace,
            initial_hypotheses=["Investigation failed before classification"]
        )
