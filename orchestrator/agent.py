"""
Agent Orchestrator - LangGraph-based RCA investigation pipeline.

Pipeline nodes:
  classify → reason → [execute_tool → reason]* → synthesize → END

Author: Jordan (DEV-1) + LangGraph refactor
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, TypedDict

from langgraph.graph import StateGraph, END

from models.alert import AlertPayload
from models.hypothesis import ClassificationResult, FailureCategory, ConfidenceLevel
from models.report import RCAReport, InvestigationStep, PossibleFix
from models.tool_result import ToolResult, ToolName
from classifier.engine import ClassificationEngine
from config import settings

# LLM-enhanced components (optional)
try:
    from classifier.llm_classifier import LLMEnhancedClassifier
    from llm.client import LLMConfig
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

from reasoning.engine import ReasoningEngine
from reasoning.synthesis import SynthesisEngine
from reasoning.llm_synthesis import LLMSynthesisEngine
from report.generator import ReportGenerator
from report.fixes import PossibleFixesGenerator
from tools.loki import LokiLogRetriever
from tools.git_blame import GitBlameChecker
from tools.jira import JiraTicketGetter
from tools.base import BaseTool

logger = logging.getLogger(__name__)

MAX_STEPS = 10


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LANGGRAPH STATE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AgentState(TypedDict):
    """Shared state threaded through every LangGraph node."""
    investigation_id: str
    alert: AlertPayload
    classification_result: Optional[ClassificationResult]
    investigation_trace: List[InvestigationStep]
    tool_results: Dict[str, ToolResult]
    tools_called: List[ToolName]
    step_number: int
    next_tool: Optional[ToolName]   # Set by reason_node; consumed by execute_tool_node
    report: Optional[RCAReport]
    error: Optional[str]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ORCHESTRATOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AgentOrchestrator:
    """
    LangGraph-based orchestrator for the RCA investigation pipeline.

    Graph topology:
        classify → reason ──(tool?)──► execute_tool ─┐
                      └──(done)──► synthesize         └──► reason
                                       └──► END
    """

    def __init__(self, investigation_id: str):
        self.investigation_id = investigation_id

        # Build LLM config if enabled
        llm_config = None
        if (
            settings.LLM_ENABLED
            and LLM_AVAILABLE
            and settings.LLM_API_KEY
            and settings.LLM_BASE_URL
        ):
            logger.info("LLM-enhanced mode enabled (custom OpenAI-compatible endpoint)")
            llm_config = LLMConfig(
                api_key=settings.LLM_API_KEY,
                model=settings.LLM_MODEL,
                max_tokens=settings.LLM_MAX_TOKENS,
                temperature=settings.LLM_TEMPERATURE,
                timeout=settings.LLM_TIMEOUT,
                base_url=settings.LLM_BASE_URL,
            )
            self.classifier = LLMEnhancedClassifier(llm_config)
        else:
            logger.info("Using pattern-only classification")
            self.classifier = ClassificationEngine()

        self.tools: Dict[ToolName, BaseTool] = {
            ToolName.LOKI: LokiLogRetriever(settings),
            ToolName.GIT_BLAME: GitBlameChecker(settings),
            ToolName.JIRA: JiraTicketGetter(settings),
        }

        self.reasoning_engine = ReasoningEngine(settings)
        self.synthesis_engine = (
            LLMSynthesisEngine(llm_config) if llm_config else SynthesisEngine()
        )
        self.fixes_generator = PossibleFixesGenerator()
        self.report_generator = ReportGenerator(settings)

        self._graph = self._build_graph()
        logger.info(f"[{self.investigation_id}] AgentOrchestrator initialized (LangGraph)")

    # ── Graph construction ─────────────────────────────────────────────────

    def _build_graph(self):
        """Compile the LangGraph state machine."""
        graph = StateGraph(AgentState)

        graph.add_node("classify", self._classify_node)
        graph.add_node("reason", self._reason_node)
        graph.add_node("execute_tool", self._execute_tool_node)
        graph.add_node("synthesize", self._synthesize_node)

        graph.set_entry_point("classify")
        graph.add_edge("classify", "reason")
        graph.add_conditional_edges(
            "reason",
            self._route_after_reason,
            {"execute_tool": "execute_tool", "synthesize": "synthesize"},
        )
        graph.add_edge("execute_tool", "reason")
        graph.add_edge("synthesize", END)

        return graph.compile()

    @staticmethod
    def _route_after_reason(state: AgentState) -> str:
        """Route to execute_tool if there is a pending tool, else synthesize."""
        if state["next_tool"] is not None and state["step_number"] <= MAX_STEPS:
            return "execute_tool"
        return "synthesize"

    # ── Public interface ───────────────────────────────────────────────────

    async def investigate(self, alert: AlertPayload) -> RCAReport:
        """
        Run the full investigation pipeline and return an RCA report.

        Never raises — returns a partial error report on failure.
        """
        logger.info(f"[{self.investigation_id}] Starting investigation for {alert.app_name}")

        initial_state: AgentState = {
            "investigation_id": self.investigation_id,
            "alert": alert,
            "classification_result": None,
            "investigation_trace": [],
            "tool_results": {},
            "tools_called": [],
            "step_number": 2,   # Step 1 is classification; tool loop starts at 2
            "next_tool": None,
            "report": None,
            "error": None,
        }

        try:
            final_state = await self._graph.ainvoke(initial_state)
        except Exception as e:
            logger.error(f"[{self.investigation_id}] Graph execution failed: {e}", exc_info=True)
            return self._generate_error_report(alert, str(e))

        # Expose final state on the instance for backward-compat / testing
        self.classification_result = final_state.get("classification_result")
        self.investigation_trace = final_state.get("investigation_trace", [])

        report = final_state.get("report")
        if report is None:
            return self._generate_error_report(
                alert, final_state.get("error") or "Unknown error"
            )

        # Auto-remediation in background
        if settings.AUTO_REMEDIATION_ENABLED and self._should_remediate(report):
            asyncio.create_task(self._run_remediation(report))

        return report

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # NODE IMPLEMENTATIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def _classify_node(self, state: AgentState) -> dict:
        """
        Node 1 — THINK-FIRST GATE.
        Classification must complete before any tool is called.
        """
        alert = state["alert"]
        logger.info(f"[{self.investigation_id}] STEP 1: Running classification...")

        try:
            result = await self.classifier.classify(alert)
        except Exception as e:
            logger.error(f"[{self.investigation_id}] Classification failed: {e}", exc_info=True)
            return {"error": str(e)}

        h = result.top_hypotheses
        step = InvestigationStep(
            step_number=1,
            reasoning=(
                f"First step: Run classification to understand failure patterns. "
                f"Analyzed {len(alert.errors)} error message(s) across 8 failure categories. "
                f"{result.classification_reasoning}"
            ),
            decision=f"Classification complete. Top hypothesis: {h[0].category.value}",
            tool_called=None,
            result_summary=(
                f"Top 3 hypotheses: "
                f"1) {h[0].category.value} ({h[0].confidence_percentage}%), "
                f"2) {h[1].category.value if len(h) > 1 else 'N/A'} "
                f"({h[1].confidence_percentage if len(h) > 1 else 0}%), "
                f"3) {h[2].category.value if len(h) > 2 else 'N/A'} "
                f"({h[2].confidence_percentage if len(h) > 2 else 0}%)"
            ),
            hypothesis_update=None,
            timestamp=datetime.utcnow(),
        )

        logger.info(
            f"[{self.investigation_id}] Classification complete. "
            f"Top: {h[0].category.value} ({h[0].confidence_percentage}%)"
        )
        return {"classification_result": result, "investigation_trace": [step]}

    async def _reason_node(self, state: AgentState) -> dict:
        """
        Node 2 — Decide which tool to call next (or signal completion).
        Sets next_tool in state; the conditional edge routes accordingly.
        """
        step_number = state["step_number"]
        logger.info(f"[{self.investigation_id}] STEP {step_number}: Reasoning...")

        reasoning_step = self.reasoning_engine.step(
            current_step=step_number,
            classification=state["classification_result"],
            tools_called=state["tools_called"],
            tool_results=state["tool_results"],
        )

        trace = list(state["investigation_trace"]) + [reasoning_step]

        if reasoning_step.tool_called is None:
            logger.info(
                f"[{self.investigation_id}] Reasoning complete: {reasoning_step.decision}"
            )

        return {"investigation_trace": trace, "next_tool": reasoning_step.tool_called}

    async def _execute_tool_node(self, state: AgentState) -> dict:
        """
        Node 3 — Execute the tool chosen by reason_node and store the result.
        Updates the last investigation step with a result summary.
        """
        tool_name: ToolName = state["next_tool"]
        alert = state["alert"]
        logger.info(f"[{self.investigation_id}] Calling {tool_name.value}")

        # Pass Jira keys from Git results when querying Jira
        context: dict = {}
        if tool_name == ToolName.JIRA and ToolName.GIT_BLAME.value in state["tool_results"]:
            git_result = state["tool_results"][ToolName.GIT_BLAME.value]
            if git_result.success and git_result.data:
                context["jira_keys"] = git_result.data.get("jira_keys", [])

        try:
            tool_result = await self.tools[tool_name].execute(alert, context)
        except Exception as e:
            logger.error(f"Unexpected error executing {tool_name.value}: {e}", exc_info=True)
            tool_result = ToolResult(
                tool_name=tool_name,
                success=False,
                data=None,
                error_message=f"Unexpected error: {str(e)}",
                duration_ms=0.0,
                evidence_path=None,
                timestamp=datetime.utcnow(),
            )

        tool_results = {**state["tool_results"], tool_name.value: tool_result}

        # Back-fill result_summary and hypothesis_update into the last trace step
        trace = list(state["investigation_trace"])
        if trace:
            last_step = trace[-1]
            last_step.result_summary = self._summarize_tool_result(tool_result)
            last_step.hypothesis_update = self.reasoning_engine.update_hypothesis_confidence(
                classification=state["classification_result"],
                tool_results=tool_results,
            )

        logger.info(
            f"[{self.investigation_id}] {tool_name.value} "
            f"completed in {tool_result.duration_ms:.0f}ms (success={tool_result.success})"
        )

        return {
            "tool_results": tool_results,
            "tools_called": state["tools_called"] + [tool_name],
            "investigation_trace": trace,
            "step_number": state["step_number"] + 1,
            "next_tool": None,
        }

    async def _synthesize_node(self, state: AgentState) -> dict:
        """
        Node 4 — Synthesize all evidence and generate the final RCA report.
        Always runs; produces the terminal artifact of the pipeline.
        """
        alert = state["alert"]
        logger.info(f"[{self.investigation_id}] STEP 3: Synthesizing evidence...")

        root_cause, category, confidence_level, is_code_change = (
            await self.synthesis_engine.synthesize_root_cause(
                classification=state["classification_result"],
                tool_results=state["tool_results"],
            )
        )

        ruled_out = self.synthesis_engine.build_ruled_out_categories(
            classification=state["classification_result"],
            tool_results=state["tool_results"],
        )
        code_changes = self.synthesis_engine.extract_code_changes(
            tool_results=state["tool_results"]
        )
        correlation_ids = [e.correlation_id for e in alert.errors]
        log_evidence = self.synthesis_engine.extract_log_evidence(
            tool_results=state["tool_results"],
            correlation_ids=correlation_ids,
        )
        possible_fixes = self.fixes_generator.generate_fixes(
            root_cause_category=category,
            is_code_change=is_code_change,
            code_changes=code_changes,
            evidence={},
        )

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
            investigation_steps=state["investigation_trace"],
            initial_hypotheses=[
                f"{h.category.value} ({h.confidence_percentage}%)"
                for h in state["classification_result"].top_hypotheses
            ],
        )

        json_path, md_path = self.report_generator.generate(report)
        logger.info(
            f"[{self.investigation_id}] Investigation complete. "
            f"Reports written to {json_path} and {md_path}"
        )

        return {"report": report}

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HELPERS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _summarize_tool_result(self, result: ToolResult) -> str:
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
        min_conf = settings.AUTO_REMEDIATION_MIN_CONFIDENCE
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
                exc_info=True,
            )

    def _generate_error_report(self, alert: AlertPayload, error: str) -> RCAReport:
        """Generate a partial report when the pipeline fails — never silently fails."""
        return RCAReport(
            report_id=self.investigation_id,
            generated_at=datetime.utcnow(),
            app_name=alert.app_name,
            alert_time=alert.alert_time,
            severity=alert.severity.value,
            environment=alert.environment.value,
            root_cause=f"Investigation failed: {error}",
            root_cause_category=FailureCategory.CODE_LOGIC_ERROR,
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
                    estimated_impact="Manual investigation required",
                )
            ],
            investigation_steps=[],
            initial_hypotheses=["Investigation failed before classification"],
        )
