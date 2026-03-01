"""
Reasoning Engine - Decision-making loop for investigation.

Determines which tools to call and when to stop based on evidence.

Author: Riley (DEV-3)
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from models.hypothesis import FailureCategory, ClassificationResult, ConfidenceLevel
from models.tool_result import ToolResult, ToolName
from models.report import InvestigationStep
from config import Settings

logger = logging.getLogger(__name__)


class ReasoningEngine:
    """
    Decision-making engine for investigation.

    **Critical Responsibility:**
    Enforce infra-first checking: DB/DNS/Cert/Network checks BEFORE code blame.

    **Decision Loop:**
    1. Analyze current hypotheses and evidence
    2. Decide next tool to call (or DONE)
    3. Generate InvestigationStep with reasoning
    4. Update confidence after each tool result
    5. Stop when confidence > threshold or max steps reached
    """

    # Tool priority by category (lower = higher priority)
    # Infra tools (Loki) have priority over code tools (Git, Jira)
    TOOL_PRIORITY = {
        # Infra categories: Check Loki FIRST
        FailureCategory.DB_CONNECTIVITY: [ToolName.LOKI],
        FailureCategory.DNS_FAILURE: [ToolName.LOKI],
        FailureCategory.CERTIFICATE_EXPIRY: [ToolName.LOKI],
        FailureCategory.NETWORK_INTRA_SERVICE: [ToolName.LOKI],
        FailureCategory.MEMORY_RESOURCE_EXHAUSTION: [ToolName.LOKI],
        FailureCategory.DEPENDENCY_FAILURE: [ToolName.LOKI],

        # Code/config categories: Check Loki THEN Git/Jira
        FailureCategory.CODE_LOGIC_ERROR: [ToolName.LOKI, ToolName.GIT_BLAME, ToolName.JIRA],
        FailureCategory.CONFIG_DRIFT: [ToolName.LOKI, ToolName.GIT_BLAME],
    }

    def __init__(self, settings: Settings):
        self.settings = settings
        self.confidence_threshold = settings.CONFIDENCE_THRESHOLD
        self.max_steps = settings.MAX_INVESTIGATION_STEPS

    def step(
        self,
        current_step: int,
        classification: ClassificationResult,
        tools_called: List[ToolName],
        tool_results: Dict[str, ToolResult]
    ) -> InvestigationStep:
        """
        Execute one reasoning step: decide next action.

        Args:
            current_step: Current step number
            classification: Initial classification result
            tools_called: Tools already called
            tool_results: Results from previous tool calls

        Returns:
            InvestigationStep with decision and reasoning
        """
        # Get top hypothesis
        top_hypothesis = classification.top_hypotheses[0]
        top_category = top_hypothesis.category
        current_confidence = top_hypothesis.confidence_percentage

        logger.info(
            f"Step {current_step}: Analyzing {top_category.value} "
            f"(confidence: {current_confidence}%)"
        )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # DECISION 1: Check stop conditions
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        if current_confidence >= self.confidence_threshold:
            return InvestigationStep(
                step_number=current_step,
                reasoning=(
                    f"Confidence for {top_category.value} is {current_confidence}%, "
                    f"which exceeds threshold of {self.confidence_threshold}%. "
                    f"Investigation complete."
                ),
                decision="DONE - High confidence achieved",
                tool_called=None,
                result_summary=None,
                hypothesis_update=(
                    f"{top_category.value}: {current_confidence}% (CONFIRMED)"
                ),
                timestamp=datetime.utcnow()
            )

        if current_step >= self.max_steps:
            return InvestigationStep(
                step_number=current_step,
                reasoning=(
                    f"Maximum investigation steps ({self.max_steps}) reached. "
                    f"Stopping with current best hypothesis: {top_category.value} "
                    f"at {current_confidence}% confidence."
                ),
                decision="DONE - Max steps reached",
                tool_called=None,
                result_summary=None,
                hypothesis_update=(
                    f"{top_category.value}: {current_confidence}% (MEDIUM confidence)"
                ),
                timestamp=datetime.utcnow()
            )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # DECISION 2: Select next tool (INFRA-FIRST PRIORITY)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        next_tool = self._select_next_tool(top_category, tools_called)

        if next_tool is None:
            # All relevant tools called
            return InvestigationStep(
                step_number=current_step,
                reasoning=(
                    f"All relevant tools for {top_category.value} have been called. "
                    f"Current confidence: {current_confidence}%. "
                    f"Tool results: {', '.join([t.value for t in tools_called])}"
                ),
                decision="DONE - All tools exhausted",
                tool_called=None,
                result_summary=None,
                hypothesis_update=(
                    f"{top_category.value}: {current_confidence}% "
                    f"(based on {len(tools_called)} tool(s))"
                ),
                timestamp=datetime.utcnow()
            )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # DECISION 3: Call next tool
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        reasoning = self._generate_tool_call_reasoning(
            next_tool=next_tool,
            category=top_category,
            confidence=current_confidence,
            tools_called=tools_called
        )

        return InvestigationStep(
            step_number=current_step,
            reasoning=reasoning,
            decision=f"Call {next_tool.value} to gather evidence",
            tool_called=next_tool,
            result_summary=None,  # Will be filled after tool executes
            hypothesis_update=None,  # Will be filled after tool executes
            timestamp=datetime.utcnow()
        )

    def _select_next_tool(
        self,
        category: FailureCategory,
        tools_called: List[ToolName]
    ) -> Optional[ToolName]:
        """
        Select next tool to call based on category and priority.

        CRITICAL: Enforces infra-first checking.

        Args:
            category: Current top hypothesis category
            tools_called: Tools already called

        Returns:
            Next tool to call, or None if all tools called
        """
        # Get priority list for this category
        priority_list = self.TOOL_PRIORITY.get(category, [ToolName.LOKI])

        # Return first tool not yet called
        for tool in priority_list:
            if tool not in tools_called:
                return tool

        return None

    def _generate_tool_call_reasoning(
        self,
        next_tool: ToolName,
        category: FailureCategory,
        confidence: float,
        tools_called: List[ToolName]
    ) -> str:
        """
        Generate reasoning for why this tool should be called.

        Args:
            next_tool: Tool to call
            category: Current hypothesis category
            confidence: Current confidence percentage
            tools_called: Tools already called

        Returns:
            Reasoning text
        """
        reasoning_parts = []

        # Explain current state
        reasoning_parts.append(
            f"Current top hypothesis: {category.value} ({confidence}% confidence)."
        )

        # Explain why this tool is needed
        if next_tool == ToolName.LOKI:
            if category in [
                FailureCategory.DB_CONNECTIVITY,
                FailureCategory.DNS_FAILURE,
                FailureCategory.CERTIFICATE_EXPIRY,
                FailureCategory.NETWORK_INTRA_SERVICE
            ]:
                reasoning_parts.append(
                    f"Calling Loki first to check for infrastructure-level {category.value} "
                    f"evidence in logs. This must be verified before checking code changes."
                )
            else:
                reasoning_parts.append(
                    f"Calling Loki to retrieve log evidence for {category.value}."
                )

        elif next_tool == ToolName.GIT_BLAME:
            reasoning_parts.append(
                f"Loki evidence gathered. Now calling Git to check for recent code changes "
                f"that may have introduced {category.value}."
            )

        elif next_tool == ToolName.JIRA:
            reasoning_parts.append(
                f"Git commits identified. Calling Jira to retrieve ticket context and "
                f"flag any risk indicators (hotfix labels, missing AC, In Progress status)."
            )

        # Add context about tools already called
        if tools_called:
            reasoning_parts.append(
                f"Tools already called: {', '.join([t.value for t in tools_called])}."
            )

        return " ".join(reasoning_parts)

    def update_hypothesis_confidence(
        self,
        classification: ClassificationResult,
        tool_results: Dict[str, ToolResult]
    ) -> str:
        """
        Update hypothesis confidence based on tool results.

        Args:
            classification: Initial classification
            tool_results: Results from tools

        Returns:
            Hypothesis update text
        """
        top_hypothesis = classification.top_hypotheses[0]
        category = top_hypothesis.category
        initial_confidence = top_hypothesis.confidence_percentage

        # Analyze tool results
        confidence_adjustments = []

        # Loki results
        if ToolName.LOKI.value in tool_results:
            loki_result = tool_results[ToolName.LOKI.value]
            if loki_result.success and loki_result.data:
                stack_trace_count = len(loki_result.data.get("stack_traces", []))
                slow_query_count = len(loki_result.data.get("slow_queries", []))

                if category == FailureCategory.DB_CONNECTIVITY and slow_query_count > 0:
                    confidence_adjustments.append(
                        f"Loki found {slow_query_count} slow queries → +10% confidence"
                    )
                elif category == FailureCategory.CODE_LOGIC_ERROR and stack_trace_count > 0:
                    confidence_adjustments.append(
                        f"Loki found {stack_trace_count} stack traces → +15% confidence"
                    )

        # Git results
        if ToolName.GIT_BLAME.value in tool_results:
            git_result = tool_results[ToolName.GIT_BLAME.value]
            if git_result.success and git_result.data:
                commit_count = git_result.data.get("total_commits", 0)
                if commit_count > 0:
                    confidence_adjustments.append(
                        f"Git found {commit_count} recent commits → +5% confidence"
                    )

        # Jira results
        if ToolName.JIRA.value in tool_results:
            jira_result = tool_results[ToolName.JIRA.value]
            if jira_result.success and jira_result.data:
                risk_count = jira_result.data.get("risk_flagged_count", 0)
                if risk_count > 0:
                    confidence_adjustments.append(
                        f"Jira found {risk_count} risky tickets → +10% confidence"
                    )

        if confidence_adjustments:
            update_text = f"{category.value}: {initial_confidence}% → " + ", ".join(confidence_adjustments)
        else:
            update_text = f"{category.value}: {initial_confidence}% (no evidence change)"

        return update_text
