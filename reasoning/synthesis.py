"""
Synthesis Engine - Correlates evidence from all tools to determine root cause.

Author: Riley (DEV-3)
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from models.hypothesis import ClassificationResult, FailureCategory, ConfidenceLevel
from models.tool_result import ToolResult, ToolName
from models.report import (
    RuledOutCategory,
    CodeChange,
    LogEvidence
)

logger = logging.getLogger(__name__)


class SynthesisEngine:
    """
    Correlates evidence from logs, git, and Jira to determine root cause.

    **Correlation Logic:**
    - Log evidence + Recent commits → Code change root cause
    - Log evidence + No commits → Infrastructure root cause
    - Jira risk flags → Increase confidence in code change
    - Ruled-out categories → Evidence showing why other categories don't apply
    """

    async def synthesize_root_cause(
        self,
        classification: ClassificationResult,
        tool_results: Dict[str, ToolResult]
    ) -> tuple[str, FailureCategory, ConfidenceLevel, bool]:
        """
        Determine root cause from all evidence.

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

        # Build root cause text
        root_cause = self._build_root_cause_text(
            category=category,
            loki_evidence=loki_evidence,
            git_evidence=git_evidence,
            jira_evidence=jira_evidence,
            is_code_change=is_code_change
        )

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
            f"(code_change={is_code_change})"
        )

        return root_cause, category, confidence_level, is_code_change

    def build_ruled_out_categories(
        self,
        classification: ClassificationResult,
        tool_results: Dict[str, ToolResult]
    ) -> List[RuledOutCategory]:
        """
        Build list of categories that were investigated and ruled out.

        Args:
            classification: Initial classification
            tool_results: Tool results

        Returns:
            List of ruled-out categories with evidence
        """
        ruled_out = []
        top_category = classification.top_hypotheses[0].category

        # Check all hypotheses except the top one
        for hypothesis in classification.top_hypotheses[1:]:
            category = hypothesis.category
            reason, evidence = self._get_ruled_out_reason(
                category=category,
                tool_results=tool_results
            )

            ruled_out.append(
                RuledOutCategory(
                    category=category,
                    reason=reason,
                    evidence=evidence
                )
            )

        # Also rule out major categories not in top 3
        all_categories = [
            FailureCategory.DB_CONNECTIVITY,
            FailureCategory.DNS_FAILURE,
            FailureCategory.CERTIFICATE_EXPIRY,
            FailureCategory.NETWORK_INTRA_SERVICE,
            FailureCategory.CODE_LOGIC_ERROR,
            FailureCategory.CONFIG_DRIFT,
            FailureCategory.DEPENDENCY_FAILURE,
            FailureCategory.MEMORY_RESOURCE_EXHAUSTION,
        ]

        top_3_categories = [h.category for h in classification.top_hypotheses]

        for category in all_categories:
            if category not in top_3_categories and category != top_category:
                reason, evidence = self._get_ruled_out_reason(
                    category=category,
                    tool_results=tool_results
                )
                ruled_out.append(
                    RuledOutCategory(
                        category=category,
                        reason=reason,
                        evidence=evidence
                    )
                )

        return ruled_out

    def extract_code_changes(
        self,
        tool_results: Dict[str, ToolResult]
    ) -> List[CodeChange]:
        """
        Extract code changes from git results.

        Args:
            tool_results: Tool results

        Returns:
            List of CodeChange objects
        """
        if ToolName.GIT_BLAME.value not in tool_results:
            return []

        git_result = tool_results[ToolName.GIT_BLAME.value]
        if not git_result.success or not git_result.data:
            return []

        commits = git_result.data.get("commits", [])
        jira_data = self._get_jira_data(tool_results)

        code_changes = []
        for commit in commits[:10]:  # Limit to 10 most recent
            commit_hash = commit.get("commit_hash", "")
            jira_ticket = self._extract_jira_from_message(commit.get("message", ""))

            # Check for risk flags
            risk_flags = []
            if "hotfix" in commit.get("message", "").lower():
                risk_flags.append("hotfix")
            if "emergency" in commit.get("message", "").lower():
                risk_flags.append("emergency")

            # Check Jira for additional risk flags
            if jira_ticket and jira_ticket in jira_data:
                ticket_risks = jira_data[jira_ticket].get("risk_flags", [])
                risk_flags.extend(ticket_risks)

            code_changes.append(
                CodeChange(
                    commit_hash=commit_hash,
                    author=commit.get("author", "Unknown"),
                    timestamp=datetime.fromisoformat(commit.get("timestamp", "").replace('Z', '+00:00')),
                    message=commit.get("message", ""),
                    files_changed=commit.get("files_changed", []),
                    jira_ticket=jira_ticket,
                    risk_flags=list(set(risk_flags))  # Deduplicate
                )
            )

        return code_changes

    def extract_log_evidence(
        self,
        tool_results: Dict[str, ToolResult],
        correlation_ids: List[Optional[str]]
    ) -> Optional[LogEvidence]:
        """
        Extract log evidence from Loki results.

        Args:
            tool_results: Tool results
            correlation_ids: Correlation IDs from alert

        Returns:
            LogEvidence object or None
        """
        if ToolName.LOKI.value not in tool_results:
            return None

        loki_result = tool_results[ToolName.LOKI.value]
        if not loki_result.success or not loki_result.data:
            return None

        data = loki_result.data

        # Get first non-null correlation ID
        corr_id = next((cid for cid in correlation_ids if cid is not None), None)

        return LogEvidence(
            correlation_id=corr_id,
            evidence_path=loki_result.evidence_path.value if loki_result.evidence_path else "unknown",
            stack_traces=data.get("stack_traces", []),
            key_log_lines=data.get("key_log_lines", [])[:20],  # Limit
            slow_queries=data.get("slow_queries", []),
            total_error_count=data.get("total_error_count", 0)
        )

    def _analyze_loki_evidence(self, tool_results: Dict[str, ToolResult]) -> Dict[str, Any]:
        """Analyze Loki tool results"""
        if ToolName.LOKI.value not in tool_results:
            return {"has_evidence": False}

        loki_result = tool_results[ToolName.LOKI.value]
        if not loki_result.success or not loki_result.data:
            return {"has_evidence": False}

        data = loki_result.data
        return {
            "has_evidence": True,
            "stack_trace_count": len(data.get("stack_traces", [])),
            "slow_query_count": len(data.get("slow_queries", [])),
            "error_count": data.get("total_error_count", 0),
            "evidence_path": loki_result.evidence_path.value if loki_result.evidence_path else "unknown"
        }

    def _analyze_git_evidence(self, tool_results: Dict[str, ToolResult]) -> Dict[str, Any]:
        """Analyze Git tool results"""
        if ToolName.GIT_BLAME.value not in tool_results:
            return {"has_commits": False, "commit_count": 0}

        git_result = tool_results[ToolName.GIT_BLAME.value]
        if not git_result.success or not git_result.data:
            return {"has_commits": False, "commit_count": 0}

        data = git_result.data
        return {
            "has_commits": True,
            "commit_count": data.get("total_commits", 0),
            "high_churn_count": len(data.get("high_churn_files", [])),
            "jira_key_count": len(data.get("jira_keys", []))
        }

    def _analyze_jira_evidence(self, tool_results: Dict[str, ToolResult]) -> Dict[str, Any]:
        """Analyze Jira tool results"""
        if ToolName.JIRA.value not in tool_results:
            return {"has_tickets": False}

        jira_result = tool_results[ToolName.JIRA.value]
        if not jira_result.success or not jira_result.data:
            return {"has_tickets": False}

        data = jira_result.data
        return {
            "has_tickets": True,
            "ticket_count": data.get("total_tickets", 0),
            "risk_flagged_count": data.get("risk_flagged_count", 0)
        }

    def _build_root_cause_text(
        self,
        category: FailureCategory,
        loki_evidence: Dict[str, Any],
        git_evidence: Dict[str, Any],
        jira_evidence: Dict[str, Any],
        is_code_change: bool
    ) -> str:
        """Build human-readable root cause text"""
        parts = [f"Root cause identified as {category.value}."]

        if loki_evidence.get("has_evidence"):
            parts.append(
                f"Log analysis found {loki_evidence.get('error_count', 0)} error occurrences."
            )

        if is_code_change and git_evidence.get("has_commits"):
            parts.append(
                f"Git analysis identified {git_evidence.get('commit_count', 0)} "
                f"recent code change(s) that may have introduced this issue."
            )

        if jira_evidence.get("has_tickets") and jira_evidence.get("risk_flagged_count", 0) > 0:
            parts.append(
                f"Jira analysis flagged {jira_evidence.get('risk_flagged_count')} "
                f"risky ticket(s) (hotfix labels, In Progress status, or missing acceptance criteria)."
            )

        return " ".join(parts)

    def _calculate_final_confidence(
        self,
        base_confidence: float,
        loki_evidence: Dict[str, Any],
        git_evidence: Dict[str, Any],
        jira_evidence: Dict[str, Any]
    ) -> float:
        """Calculate final confidence based on evidence"""
        confidence = base_confidence

        # Boost from Loki evidence
        if loki_evidence.get("has_evidence"):
            if loki_evidence.get("stack_trace_count", 0) > 0:
                confidence += 10.0
            if loki_evidence.get("slow_query_count", 0) > 0:
                confidence += 5.0

        # Boost from Git evidence
        if git_evidence.get("has_commits"):
            confidence += 5.0
            if git_evidence.get("high_churn_count", 0) > 0:
                confidence += 5.0

        # Boost from Jira risk flags
        if jira_evidence.get("risk_flagged_count", 0) > 0:
            confidence += 10.0

        # Cap at 100%
        return min(confidence, 100.0)

    def _confidence_to_level(self, confidence: float) -> ConfidenceLevel:
        """Convert confidence percentage to level"""
        if confidence < 40.0:
            return ConfidenceLevel.LOW
        elif confidence < 70.0:
            return ConfidenceLevel.MEDIUM
        elif confidence < 85.0:
            return ConfidenceLevel.HIGH
        else:
            return ConfidenceLevel.CONFIRMED

    def _get_ruled_out_reason(
        self,
        category: FailureCategory,
        tool_results: Dict[str, ToolResult]
    ) -> tuple[str, str]:
        """Get reason why a category was ruled out"""
        # Generic reason based on lack of evidence
        reason = f"No strong evidence found for {category.value}"

        # Check Loki evidence
        loki_evidence = self._analyze_loki_evidence(tool_results)
        if loki_evidence.get("has_evidence"):
            evidence = f"Logs did not contain patterns matching {category.value}"
        else:
            evidence = "No log evidence available"

        return reason, evidence

    def _get_jira_data(self, tool_results: Dict[str, ToolResult]) -> Dict[str, Any]:
        """Extract Jira ticket data indexed by key"""
        if ToolName.JIRA.value not in tool_results:
            return {}

        jira_result = tool_results[ToolName.JIRA.value]
        if not jira_result.success or not jira_result.data:
            return {}

        tickets = jira_result.data.get("tickets", [])
        return {
            ticket["key"]: {
                "risk_flags": ticket.get("labels", [])
            }
            for ticket in tickets
        }

    def _extract_jira_from_message(self, message: str) -> Optional[str]:
        """Extract first Jira key from commit message"""
        import re
        match = re.search(r'\b([A-Z]{2,}-\d+)\b', message)
        return match.group(1) if match else None
