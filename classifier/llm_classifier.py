"""
LLM-Enhanced Classifier - Hybrid pattern matching + LLM classification.

Uses rule-based patterns for known failures, LLM for novel/complex cases.

Author: Alex (ARCHITECT) - LLM Enhancement
"""

import json
import logging
from typing import Optional

from models.alert import AlertPayload
from models.hypothesis import ClassificationResult, Hypothesis, FailureCategory, ConfidenceLevel
from classifier.engine import ClassificationEngine
from classifier.patterns import get_confidence_level
from llm.client import LLMClient, LLMConfig
from llm.prompts import SREPrompts

logger = logging.getLogger(__name__)


class LLMEnhancedClassifier:
    """
    Hybrid classifier combining pattern matching with LLM intelligence.

    **Strategy:**
    1. Try pattern matching first (fast, deterministic)
    2. If confidence < threshold, use LLM (intelligent fallback)
    3. Combine results for best accuracy

    **Benefits:**
    - Fast for known patterns (rules)
    - Intelligent for novel patterns (LLM)
    - Cost-effective (LLM only when needed)
    """

    def __init__(self, llm_config: Optional[LLMConfig] = None):
        """
        Initialize hybrid classifier.

        Args:
            llm_config: LLM configuration (optional, disables LLM if None)
        """
        self.pattern_classifier = ClassificationEngine()
        self.llm_client = LLMClient(llm_config) if llm_config else None
        self.llm_threshold = 40.0  # Use LLM if pattern confidence < 40%

    async def classify(self, alert: AlertPayload) -> ClassificationResult:
        """
        Classify alert using hybrid approach.

        Args:
            alert: The alert payload

        Returns:
            ClassificationResult with best available classification
        """
        # Step 1: Try pattern matching first
        logger.info("Attempting pattern-based classification...")
        pattern_result = await self.pattern_classifier.classify(alert)

        top_confidence = pattern_result.top_hypotheses[0].confidence_percentage

        # Step 2: Check if we need LLM fallback
        if top_confidence < self.llm_threshold and self.llm_client:
            logger.warning(
                f"Pattern confidence ({top_confidence}%) below threshold "
                f"({self.llm_threshold}%). Using LLM fallback..."
            )

            try:
                llm_result = await self._classify_with_llm(alert)

                # Combine pattern and LLM results
                combined_result = self._combine_results(pattern_result, llm_result)

                logger.info(
                    f"LLM classification: {llm_result.top_hypotheses[0].category.value} "
                    f"({llm_result.top_hypotheses[0].confidence_percentage}%)"
                )

                return combined_result

            except Exception as e:
                logger.error(f"LLM classification failed: {e}. Using pattern result.", exc_info=True)
                # Fallback to pattern result
                return pattern_result

        else:
            logger.info(
                f"Pattern confidence ({top_confidence}%) sufficient. "
                f"Skipping LLM call (cost optimization)."
            )
            return pattern_result

    async def _classify_with_llm(self, alert: AlertPayload) -> ClassificationResult:
        """
        Classify using LLM.

        Args:
            alert: The alert payload

        Returns:
            ClassificationResult from LLM analysis
        """
        import time
        start_time = time.perf_counter()

        # Build prompt
        error_messages = [e.error_message for e in alert.errors]

        system_prompt = SREPrompts.classification_system_prompt()
        user_prompt = SREPrompts.classification_prompt(error_messages)

        # Call LLM
        response = await self.llm_client.complete(
            prompt=user_prompt,
            system_prompt=system_prompt,
            response_format="json"
        )

        # Parse response
        try:
            llm_data = json.loads(response.content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}")
            # Try to extract JSON from markdown code blocks
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            llm_data = json.loads(content)

        category = FailureCategory(llm_data["category"])
        confidence = float(llm_data["confidence"])
        reasoning = llm_data["reasoning"]

        # Build hypothesis
        hypothesis = Hypothesis(
            category=category,
            confidence_percentage=confidence,
            confidence_level=ConfidenceLevel(get_confidence_level(confidence)),
            reasoning=f"LLM Analysis: {reasoning}",
            supporting_evidence=["LLM-based classification"]
        )

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        # Build result (with only top hypothesis from LLM)
        return ClassificationResult(
            top_hypotheses=[hypothesis],
            all_scores={category.value: confidence},
            classification_reasoning=(
                f"LLM-based classification. {reasoning} "
                f"(Tokens: {response.tokens_used})"
            ),
            classification_duration_ms=duration_ms
        )

    def _combine_results(
        self,
        pattern_result: ClassificationResult,
        llm_result: ClassificationResult
    ) -> ClassificationResult:
        """
        Combine pattern and LLM results intelligently.

        **Strategy:**
        - Use LLM top hypothesis (it saw the full context)
        - Include pattern scores for transparency
        - Merge reasoning from both approaches

        Args:
            pattern_result: Result from pattern matching
            llm_result: Result from LLM

        Returns:
            Combined classification result
        """
        llm_hypothesis = llm_result.top_hypotheses[0]

        # Boost confidence slightly since both methods agree
        pattern_category = pattern_result.top_hypotheses[0].category
        if llm_hypothesis.category == pattern_category:
            # Both agree - boost confidence
            combined_confidence = min(llm_hypothesis.confidence_percentage + 10.0, 100.0)
            agreement_note = "Both pattern matching and LLM agree."
        else:
            # Disagree - use LLM but note disagreement
            combined_confidence = llm_hypothesis.confidence_percentage
            agreement_note = (
                f"LLM suggests {llm_hypothesis.category.value} "
                f"while patterns suggested {pattern_category.value}. "
                f"Using LLM result for novel patterns."
            )

        # Create combined hypothesis
        combined_hypothesis = Hypothesis(
            category=llm_hypothesis.category,
            confidence_percentage=combined_confidence,
            confidence_level=ConfidenceLevel(get_confidence_level(combined_confidence)),
            reasoning=(
                f"{llm_hypothesis.reasoning}\n\n"
                f"Pattern Analysis: {pattern_result.top_hypotheses[0].reasoning}\n\n"
                f"{agreement_note}"
            ),
            supporting_evidence=(
                llm_hypothesis.supporting_evidence +
                pattern_result.top_hypotheses[0].supporting_evidence
            )
        )

        # Merge all scores
        all_scores = {**pattern_result.all_scores}
        all_scores[llm_hypothesis.category.value] = combined_confidence

        return ClassificationResult(
            top_hypotheses=[combined_hypothesis] + pattern_result.top_hypotheses[1:],
            all_scores=all_scores,
            classification_reasoning=(
                f"Hybrid classification (Pattern + LLM). {agreement_note}\n\n"
                f"LLM: {llm_result.classification_reasoning}\n"
                f"Patterns: {pattern_result.classification_reasoning}"
            ),
            classification_duration_ms=(
                pattern_result.classification_duration_ms +
                llm_result.classification_duration_ms
            )
        )
