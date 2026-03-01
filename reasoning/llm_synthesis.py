"""
LLM-Enhanced Synthesis Engine - Uses LLM for intelligent root cause analysis.

Uses LLM to synthesize evidence from logs, git, and Jira into actionable root cause.
This provides context-aware analysis even at high confidence levels.

Author: Riley (DEV-3) + Alex (ARCHITECT) - LLM Enhancement
"""

import logging
import json
from typing import List, Dict, Any, Optional

from models.hypothesis import ClassificationResult, FailureCategory, ConfidenceLevel
from models.tool_result import ToolResult, ToolName
from models.report import RuledOutCategory, CodeChange, LogEvidence
from reasoning.synthesis import SynthesisEngine
from llm.client import LLMClient, LLMConfig
from llm.prompts import SREPrompts

logger = logging.getLogger(__name__)


class LLMSynthesisEngine(SynthesisEngine):
    """
    Enhanced synthesis engine that uses LLM for root cause analysis.

    **Key Differences from Base SynthesisEngine:**
    - Uses LLM to generate root cause text (context-aware, natural language)
    - Provides better evidence correlation
    - More actionable explanations
    - Falls back to rule-based if LLM fails
    """

    def __init__(self, llm_config: Optional[LLMConfig] = None):
        """
        Initialize LLM-enhanced synthesis engine.

        Args:
            llm_config: LLM configuration. If None, falls back to rule-based.
        """
        super().__init__()

        self.llm_client = None
        if llm_config:
            try:
                self.llm_client = LLMClient(llm_config)
                logger.info("LLM synthesis enabled with provider: %s", llm_config.provider.value)
            except Exception as e:
                logger.warning("Failed to initialize LLM client for synthesis: %s. Falling back to rule-based.", e)
                self.llm_client = None

    async def synthesize_root_cause(
        self,
        classification: ClassificationResult,
        tool_results: Dict[str, ToolResult]
    ) -> tuple[str, FailureCategory, ConfidenceLevel, bool]:
        """
        Determine root cause using LLM-enhanced analysis.

        Workflow:
        1. Analyze evidence (same as base class)
        2. If LLM available: Use LLM to generate root cause
        3. If LLM fails: Fall back to rule-based synthesis
        4. Calculate final confidence

        Args:
            classification: Initial classification result
            tool_results: Results from all tools

        Returns:
            (root_cause_text, category, confidence_level, is_code_change)
        """
        top_hypothesis = classification.top_hypotheses[0]
        category = top_hypothesis.category
        base_confidence = top_hypothesis.confidence_percentage

        # Analyze evidence
        loki_evidence = self._analyze_loki_evidence(tool_results)
        git_evidence = self._analyze_git_evidence(tool_results)
        jira_evidence = self._analyze_jira_evidence(tool_results)

        # Determine if code change is involved
        is_code_change = git_evidence["has_commits"] and git_evidence["commit_count"] > 0

        # Try LLM synthesis first
        root_cause = None
        if self.llm_client:
            try:
                root_cause = await self._synthesize_with_llm(
                    hypothesis=top_hypothesis.category.value,
                    loki_evidence=loki_evidence,
                    git_evidence=git_evidence,
                    jira_evidence=jira_evidence,
                    tool_results=tool_results
                )
                logger.info("LLM synthesis successful for %s", category.value)
            except Exception as e:
                logger.warning("LLM synthesis failed: %s. Falling back to rule-based.", e)
                root_cause = None

        # Fallback to rule-based if LLM fails
        if not root_cause:
            root_cause = self._build_root_cause_text(
                category=category,
                loki_evidence=loki_evidence,
                git_evidence=git_evidence,
                jira_evidence=jira_evidence,
                is_code_change=is_code_change
            )
            logger.info("Using rule-based synthesis for %s", category.value)

        # Calculate final confidence
        final_confidence = self._calculate_final_confidence(
            base_confidence=base_confidence,
            loki_evidence=loki_evidence,
            git_evidence=git_evidence,
            jira_evidence=jira_evidence
        )

        confidence_level = self._confidence_to_level(final_confidence)

        logger.info(
            f"Synthesis: {category.value} at {final_confidence}% confidence "
            f"(code_change={is_code_change}, llm_used={self.llm_client is not None and root_cause is not None})"
        )

        return root_cause, category, confidence_level, is_code_change

    async def _synthesize_with_llm(
        self,
        hypothesis: str,
        loki_evidence: Dict[str, Any],
        git_evidence: Dict[str, Any],
        jira_evidence: Dict[str, Any],
        tool_results: Dict[str, ToolResult]
    ) -> str:
        """
        Use LLM to synthesize root cause from evidence.

        Args:
            hypothesis: Current failure category hypothesis
            loki_evidence: Analyzed Loki evidence
            git_evidence: Analyzed Git evidence
            jira_evidence: Analyzed Jira evidence
            tool_results: Raw tool results

        Returns:
            Root cause text generated by LLM

        Raises:
            Exception if LLM call fails
        """
        # Enrich evidence with actual data from tool results
        enriched_loki = self._enrich_loki_evidence(loki_evidence, tool_results)
        enriched_git = self._enrich_git_evidence(git_evidence, tool_results)
        enriched_jira = self._enrich_jira_evidence(jira_evidence, tool_results)

        # Generate synthesis prompt
        prompt = SREPrompts.synthesis_prompt(
            hypothesis=hypothesis,
            loki_evidence=enriched_loki,
            git_evidence=enriched_git,
            jira_evidence=enriched_jira
        )

        system_prompt = SREPrompts.synthesis_system_prompt()

        # Call LLM
        response = await self.llm_client.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            response_format="json"
        )

        # Parse response
        try:
            result = json.loads(response)
            root_cause = result.get("root_cause", "")

            # Add correlation info if available
            correlation = result.get("correlation", "")
            if correlation:
                root_cause += f" {correlation}"

            # Add key evidence
            key_evidence = result.get("key_evidence", [])
            if key_evidence:
                evidence_text = " Key evidence: " + "; ".join(key_evidence[:3])
                root_cause += evidence_text

            return root_cause
        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM synthesis response: %s", e)
            # Return raw response if JSON parsing fails
            return response[:500]  # Limit length

    def _enrich_loki_evidence(
        self,
        loki_evidence: Dict[str, Any],
        tool_results: Dict[str, ToolResult]
    ) -> Dict[str, Any]:
        """Add actual log data to Loki evidence summary"""
        enriched = loki_evidence.copy()

        if ToolName.LOKI.value not in tool_results:
            return enriched

        loki_result = tool_results[ToolName.LOKI.value]
        if not loki_result.success or not loki_result.data:
            return enriched

        data = loki_result.data

        # Add patterns from actual log lines
        key_lines = data.get("key_log_lines", [])
        if key_lines:
            # Extract common patterns from log lines
            patterns = self._extract_log_patterns(key_lines[:10])
            enriched["patterns"] = patterns
        else:
            enriched["patterns"] = "None"

        # Add stack trace snippets
        stack_traces = data.get("stack_traces", [])
        if stack_traces:
            enriched["stack_trace_snippet"] = stack_traces[0][:200] + "..." if len(stack_traces[0]) > 200 else stack_traces[0]

        return enriched

    def _enrich_git_evidence(
        self,
        git_evidence: Dict[str, Any],
        tool_results: Dict[str, ToolResult]
    ) -> Dict[str, Any]:
        """Add actual commit data to Git evidence summary"""
        enriched = git_evidence.copy()

        if ToolName.GIT_BLAME.value not in tool_results:
            enriched["files_changed"] = []
            enriched["authors"] = []
            return enriched

        git_result = tool_results[ToolName.GIT_BLAME.value]
        if not git_result.success or not git_result.data:
            enriched["files_changed"] = []
            enriched["authors"] = []
            return enriched

        data = git_result.data
        commits = data.get("commits", [])

        # Extract files and authors
        all_files = []
        all_authors = set()

        for commit in commits[:5]:  # Last 5 commits
            all_files.extend(commit.get("files_changed", []))
            all_authors.add(commit.get("author", "Unknown"))

        enriched["files_changed"] = list(set(all_files))[:10]  # Unique, limit to 10
        enriched["authors"] = list(all_authors)

        return enriched

    def _enrich_jira_evidence(
        self,
        jira_evidence: Dict[str, Any],
        tool_results: Dict[str, ToolResult]
    ) -> Dict[str, Any]:
        """Add actual ticket data to Jira evidence summary"""
        enriched = jira_evidence.copy()

        if ToolName.JIRA.value not in tool_results:
            enriched["summaries"] = []
            return enriched

        jira_result = tool_results[ToolName.JIRA.value]
        if not jira_result.success or not jira_result.data:
            enriched["summaries"] = []
            return enriched

        data = jira_result.data
        tickets = data.get("tickets", [])

        # Extract ticket summaries
        summaries = []
        for ticket in tickets[:5]:
            key = ticket.get("key", "")
            summary = ticket.get("summary", "No summary")
            summaries.append(f"{key}: {summary}")

        enriched["summaries"] = summaries

        return enriched

    def _extract_log_patterns(self, log_lines: List[str]) -> str:
        """Extract common patterns from log lines"""
        if not log_lines:
            return "None"

        # Look for common error keywords
        keywords = []
        for line in log_lines:
            lower_line = line.lower()
            if "connection" in lower_line and "refused" in lower_line:
                keywords.append("connection refused")
            elif "timeout" in lower_line:
                keywords.append("timeout")
            elif "not found" in lower_line:
                keywords.append("not found")
            elif "permission" in lower_line:
                keywords.append("permission denied")
            elif "memory" in lower_line:
                keywords.append("memory issue")

        if keywords:
            unique_keywords = list(set(keywords))
            return ", ".join(unique_keywords[:3])

        return "General errors"
