"""
Classification Engine for failure categorization.

Analyzes alert error messages and produces ranked hypotheses about
the root cause category using weighted pattern matching.

Author: Jordan (DEV-1)
"""

import time
from typing import List, Dict, Tuple
from models.alert import AlertPayload
from models.hypothesis import (
    ClassificationResult,
    Hypothesis,
    FailureCategory,
    ConfidenceLevel,
)
from .patterns import COMPILED_PATTERNS, get_confidence_level


class ClassificationEngine:
    """
    Classifies alerts into failure categories using pattern matching.

    Think-first component: This MUST run before any tools are called.
    """

    def __init__(self):
        self.patterns = COMPILED_PATTERNS

    async def classify(self, alert: AlertPayload) -> ClassificationResult:
        """
        Classify the alert into ranked hypotheses.

        This is the FIRST step in any investigation — enforced by orchestrator.

        Args:
            alert: The incoming alert payload

        Returns:
            ClassificationResult with top 3 hypotheses and full scoring matrix
        """
        start_time = time.perf_counter()

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 1: Extract all error messages (null-safe)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        error_texts = [error.error_message for error in alert.errors]
        combined_text = " ".join(error_texts).lower()

        # Track which patterns matched for evidence
        evidence_by_category: Dict[str, List[str]] = {
            category: [] for category in self.patterns.keys()
        }

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 2: Score all categories
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        raw_scores: Dict[str, float] = {
            category: 0.0 for category in self.patterns.keys()
        }

        max_possible_scores: Dict[str, float] = {}

        for category, patterns in self.patterns.items():
            category_score = 0.0
            max_category_score = sum(weight for _, weight, _ in patterns)
            max_possible_scores[category] = max_category_score

            for pattern, weight, description in patterns:
                # Check all error messages for this pattern
                for error_text in error_texts:
                    if pattern.search(error_text.lower()):
                        category_score += weight
                        evidence_by_category[category].append(description)
                        break  # Count pattern only once per category

            raw_scores[category] = category_score

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 3: Normalize to percentages
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        percentage_scores: Dict[str, float] = {}

        for category, raw_score in raw_scores.items():
            max_score = max_possible_scores[category]
            if max_score > 0:
                percentage = (raw_score / max_score) * 100.0
            else:
                percentage = 0.0
            percentage_scores[category] = round(percentage, 2)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 4: Rank and select top 3 hypotheses
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        ranked_categories = sorted(
            percentage_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )

        top_3 = ranked_categories[:3]

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 5: Build hypothesis objects
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        hypotheses: List[Hypothesis] = []

        for category_str, confidence_pct in top_3:
            category = FailureCategory(category_str)
            confidence_level = ConfidenceLevel(get_confidence_level(confidence_pct))

            # Build reasoning
            evidence = evidence_by_category[category_str]
            if evidence:
                evidence_summary = ", ".join(evidence[:3])  # Top 3 pieces of evidence
                reasoning = (
                    f"Matched {len(evidence)} pattern(s) for {category_str}: "
                    f"{evidence_summary}"
                )
            else:
                reasoning = f"No strong patterns matched for {category_str}"

            hypotheses.append(
                Hypothesis(
                    category=category,
                    confidence_percentage=confidence_pct,
                    confidence_level=confidence_level,
                    reasoning=reasoning,
                    supporting_evidence=evidence
                )
            )

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 6: Build classification reasoning
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        top_category = top_3[0][0] if top_3 else "unknown"
        top_confidence = top_3[0][1] if top_3 else 0.0

        classification_reasoning = (
            f"Analyzed {len(error_texts)} error message(s) across {len(self.patterns)} "
            f"failure categories. Top hypothesis: {top_category} ({top_confidence}%). "
        )

        if top_confidence >= 70.0:
            classification_reasoning += "High confidence classification based on strong pattern matches."
        elif top_confidence >= 40.0:
            classification_reasoning += "Medium confidence - multiple categories show similar patterns."
        else:
            classification_reasoning += "Low confidence - error patterns are ambiguous or novel."

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 7: Calculate duration and return result
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return ClassificationResult(
            top_hypotheses=hypotheses,
            all_scores=percentage_scores,
            classification_reasoning=classification_reasoning,
            classification_duration_ms=round(duration_ms, 2)
        )

    def _extract_error_text(self, alert: AlertPayload) -> str:
        """
        Extract and combine all error messages from alert.
        Null-safe: correlation_id is optional and not used here.

        Args:
            alert: The alert payload

        Returns:
            Combined error text for pattern matching
        """
        return " ".join([error.error_message for error in alert.errors])
