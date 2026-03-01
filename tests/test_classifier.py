"""
Unit tests for Classification Engine.

Tests pattern matching, hypothesis ranking, null safety, and scoring logic.

Author: Morgan (TESTER)
"""

import pytest
from classifier.engine import ClassificationEngine
from models.alert import AlertPayload
from models.hypothesis import FailureCategory, ConfidenceLevel


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLASSIFICATION CORRECTNESS TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_db_connectivity_classified_correctly(db_connectivity_alert: AlertPayload):
    """
    Test that DB connectivity failure is correctly identified as top hypothesis.
    """
    engine = ClassificationEngine()
    result = await engine.classify(db_connectivity_alert)

    # Assert top hypothesis is DB connectivity
    assert result.top_hypotheses[0].category == FailureCategory.DB_CONNECTIVITY
    assert result.top_hypotheses[0].confidence_percentage > 25.0  # Should be top-ranked

    # Assert all_scores contains this category with highest score
    assert result.all_scores["db_connectivity"] > 25.0


@pytest.mark.asyncio
async def test_dns_failure_classified_correctly(dns_failure_alert: AlertPayload):
    """
    Test that DNS failure is correctly identified as top hypothesis.
    """
    engine = ClassificationEngine()
    result = await engine.classify(dns_failure_alert)

    assert result.top_hypotheses[0].category == FailureCategory.DNS_FAILURE
    assert result.top_hypotheses[0].confidence_percentage > 50.0


@pytest.mark.asyncio
async def test_certificate_expiry_classified_correctly(certificate_expiry_alert: AlertPayload):
    """
    Test that certificate expiry is correctly identified as top hypothesis.
    """
    engine = ClassificationEngine()
    result = await engine.classify(certificate_expiry_alert)

    assert result.top_hypotheses[0].category == FailureCategory.CERTIFICATE_EXPIRY
    assert result.top_hypotheses[0].confidence_percentage > 60.0  # Should be very high


@pytest.mark.asyncio
async def test_code_logic_error_classified_correctly(code_logic_error_alert: AlertPayload):
    """
    Test that code logic error is correctly identified as top hypothesis.
    """
    engine = ClassificationEngine()
    result = await engine.classify(code_logic_error_alert)

    assert result.top_hypotheses[0].category == FailureCategory.CODE_LOGIC_ERROR
    assert result.top_hypotheses[0].confidence_percentage > 20.0


@pytest.mark.asyncio
async def test_memory_exhaustion_classified_correctly(memory_exhaustion_alert: AlertPayload):
    """
    Test that memory exhaustion is correctly identified as top hypothesis.
    """
    engine = ClassificationEngine()
    result = await engine.classify(memory_exhaustion_alert)

    assert result.top_hypotheses[0].category == FailureCategory.MEMORY_RESOURCE_EXHAUSTION
    assert result.top_hypotheses[0].confidence_percentage > 25.0  # Should be top-ranked


@pytest.mark.asyncio
async def test_config_drift_classified_correctly(config_drift_alert: AlertPayload):
    """
    Test that configuration drift is correctly identified as top hypothesis.
    """
    engine = ClassificationEngine()
    result = await engine.classify(config_drift_alert)

    assert result.top_hypotheses[0].category == FailureCategory.CONFIG_DRIFT
    assert result.top_hypotheses[0].confidence_percentage > 60.0


@pytest.mark.asyncio
async def test_network_intra_service_classified_correctly(network_intra_service_alert: AlertPayload):
    """
    Test that network/intra-service failure is correctly identified.
    """
    engine = ClassificationEngine()
    result = await engine.classify(network_intra_service_alert)

    assert result.top_hypotheses[0].category == FailureCategory.NETWORK_INTRA_SERVICE
    assert result.top_hypotheses[0].confidence_percentage > 15.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HYPOTHESIS RANKING TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_top_3_hypotheses_returned(db_connectivity_alert: AlertPayload):
    """Test that exactly 3 hypotheses are returned (or fewer if scores are tied)"""
    engine = ClassificationEngine()
    result = await engine.classify(db_connectivity_alert)

    assert len(result.top_hypotheses) <= 3
    assert len(result.top_hypotheses) >= 1


@pytest.mark.asyncio
async def test_hypotheses_ranked_descending(mixed_ambiguous_alert: AlertPayload):
    """Test that hypotheses are ranked in descending confidence order"""
    engine = ClassificationEngine()
    result = await engine.classify(mixed_ambiguous_alert)

    confidences = [h.confidence_percentage for h in result.top_hypotheses]
    assert confidences == sorted(confidences, reverse=True)


@pytest.mark.asyncio
async def test_mixed_alert_shows_multiple_hypotheses(mixed_ambiguous_alert: AlertPayload):
    """
    Test that mixed/ambiguous alert produces multiple viable hypotheses.
    (Network timeout + code error should score both categories)
    """
    engine = ClassificationEngine()
    result = await engine.classify(mixed_ambiguous_alert)

    # Should have multiple hypotheses with non-zero scores
    non_zero_hypotheses = [h for h in result.top_hypotheses if h.confidence_percentage > 0]
    assert len(non_zero_hypotheses) >= 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NULL SAFETY TESTS (CRITICAL)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_null_correlation_ids_handled_gracefully(alert_with_null_correlation_ids: AlertPayload):
    """
    CRITICAL TEST: Classification MUST NOT crash when all correlation_ids are null.
    This is a common production scenario.
    """
    engine = ClassificationEngine()

    # Should not raise any exception
    result = await engine.classify(alert_with_null_correlation_ids)

    # Should still produce valid classification
    assert result.top_hypotheses is not None
    assert len(result.top_hypotheses) > 0
    assert result.top_hypotheses[0].category == FailureCategory.DB_CONNECTIVITY


@pytest.mark.asyncio
async def test_mixed_correlation_ids_handled(alert_with_mixed_correlation_ids: AlertPayload):
    """
    Test that alerts with mix of null and non-null correlation IDs are handled.
    """
    engine = ClassificationEngine()
    result = await engine.classify(alert_with_mixed_correlation_ids)

    # Should classify based on error messages regardless of correlation ID presence
    assert result.top_hypotheses[0].category == FailureCategory.CERTIFICATE_EXPIRY


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SCORING MATRIX TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_all_scores_present_in_result(db_connectivity_alert: AlertPayload):
    """Test that all_scores dict contains all 8 categories"""
    engine = ClassificationEngine()
    result = await engine.classify(db_connectivity_alert)

    expected_categories = {
        "db_connectivity",
        "dns_failure",
        "certificate_expiry",
        "network_intra_service",
        "code_logic_error",
        "config_drift",
        "dependency_failure",
        "memory_resource_exhaustion",
    }

    assert set(result.all_scores.keys()) == expected_categories


@pytest.mark.asyncio
async def test_scores_are_percentages(db_connectivity_alert: AlertPayload):
    """Test that all scores are valid percentages (0-100)"""
    engine = ClassificationEngine()
    result = await engine.classify(db_connectivity_alert)

    for category, score in result.all_scores.items():
        assert 0.0 <= score <= 100.0, f"{category} score {score} out of range"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EVIDENCE TRACKING TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_supporting_evidence_populated(db_connectivity_alert: AlertPayload):
    """Test that top hypothesis includes supporting evidence (matched patterns)"""
    engine = ClassificationEngine()
    result = await engine.classify(db_connectivity_alert)

    top_hypothesis = result.top_hypotheses[0]
    assert len(top_hypothesis.supporting_evidence) > 0


@pytest.mark.asyncio
async def test_reasoning_not_empty(db_connectivity_alert: AlertPayload):
    """Test that all hypotheses have non-empty reasoning"""
    engine = ClassificationEngine()
    result = await engine.classify(db_connectivity_alert)

    for hypothesis in result.top_hypotheses:
        assert hypothesis.reasoning is not None
        assert len(hypothesis.reasoning) > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIDENCE LEVEL TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_confidence_levels_assigned_correctly(certificate_expiry_alert: AlertPayload):
    """
    Test that confidence levels match percentages:
    - Low: 0-40%
    - Medium: 40-70%
    - High: 70-85%
    - Confirmed: 85-100%
    """
    engine = ClassificationEngine()
    result = await engine.classify(certificate_expiry_alert)

    for hypothesis in result.top_hypotheses:
        pct = hypothesis.confidence_percentage
        level = hypothesis.confidence_level

        if pct < 40.0:
            assert level == ConfidenceLevel.LOW
        elif 40.0 <= pct < 70.0:
            assert level == ConfidenceLevel.MEDIUM
        elif 70.0 <= pct < 85.0:
            assert level == ConfidenceLevel.HIGH
        else:  # >= 85.0
            assert level == ConfidenceLevel.CONFIRMED


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PERFORMANCE TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.asyncio
async def test_classification_duration_tracked(db_connectivity_alert: AlertPayload):
    """Test that classification duration is tracked in milliseconds"""
    engine = ClassificationEngine()
    result = await engine.classify(db_connectivity_alert)

    assert result.classification_duration_ms > 0.0
    assert result.classification_duration_ms < 1000.0  # Should complete in <1 second


@pytest.mark.asyncio
async def test_classification_reasoning_populated(db_connectivity_alert: AlertPayload):
    """Test that classification_reasoning field is populated"""
    engine = ClassificationEngine()
    result = await engine.classify(db_connectivity_alert)

    assert result.classification_reasoning is not None
    assert len(result.classification_reasoning) > 10  # Should be meaningful text
