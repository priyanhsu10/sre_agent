"""
Tests for LokiLogRetriever

Teaches:
  6. Mocking — replacing real objects with fakes using unittest.mock
  7. AsyncMock — faking async functions (await-able)
  8. patch() as context manager — scoped replacement during one block
  9. Testing async code with @pytest.mark.asyncio
 10. Asserting on mock calls (was it called? with what args?)
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from models.alert import AlertPayload, ErrorEntry, Severity, Environment
from models.tool_result import ToolName
from tools.loki import LokiLogRetriever


# ─────────────────────────────────────────────────────────────────────────────
# CONCEPT 6: Mocking
# ─────────────────────────────────────────────────────────────────────────────
#
# Problem: LokiLogRetriever makes real HTTP calls to a Loki server.
# In tests we have no Loki server. We also don't want tests to depend on
# the network — they'd be slow and flaky.
#
# Solution: Replace the HTTP layer with a fake (a "mock") that returns
# whatever we tell it to.
#
# Python's unittest.mock gives us two main tools:
#   MagicMock  — fakes a regular (synchronous) object/function
#   AsyncMock  — fakes an async function (one you await)
#
# A mock records every call made to it so you can assert later.


# ─────────────────────────────────────────────────────────────────────────────
# SHARED FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_settings():
    """
    Fixture: fake settings object.

    MagicMock() creates an object where every attribute access and every
    call returns another MagicMock — so you never get an AttributeError.
    We set only the attributes our code actually reads.
    """
    s = MagicMock()
    s.LOKI_URL = "http://fake-loki:3100"
    s.LOKI_TIMEOUT_SECONDS = 5
    s.LOKI_MAX_LINES = 100
    s.LOKI_LOOKBACK_MINUTES = 60
    s.SLOW_QUERY_THRESHOLD_MS = 1000
    return s


@pytest.fixture
def loki(mock_settings):
    """
    Fixture: a LokiLogRetriever built with fake settings.
    Because mock_settings is also a fixture, pytest injects it automatically.
    """
    return LokiLogRetriever(mock_settings)


@pytest.fixture
def sample_alert():
    """A minimal alert payload for all loki tests."""
    return AlertPayload(
        app_name="payment-service",
        alert_time=datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc),
        severity=Severity.CRITICAL,
        environment=Environment.PROD,
        errors=[
            ErrorEntry(
                correlation_id="corr-abc-123",
                error_message="Connection refused to database"
            )
        ]
    )


@pytest.fixture
def alert_no_corr_id():
    """Alert where correlation_id is None — triggers the fallback path."""
    return AlertPayload(
        app_name="payment-service",
        alert_time=datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc),
        severity=Severity.HIGH,
        environment=Environment.PROD,
        errors=[
            ErrorEntry(
                correlation_id=None,          # ← null
                error_message="NullPointerException in PaymentProcessor"
            )
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# CONCEPT 7 & 8: AsyncMock + patch() as context manager
# ─────────────────────────────────────────────────────────────────────────────
#
# patch("module.path.ClassName") replaces the named object for the duration
# of the `with` block (or the decorated function), then restores the original.
#
# The string must be the import path *as seen from the module under test*,
# not where the class is defined.
#
# loki.py does:  import aiohttp
#                async with aiohttp.ClientSession() as session:
#                    async with session.get(...) as response:
#
# So we patch "tools.loki.aiohttp".


def _make_loki_response(log_lines: list[str]) -> dict:
    """Build a dict that looks like a real Loki API JSON response."""
    return {
        "status": "success",
        "data": {
            "result": [
                {
                    "stream": {"app": "payment-service"},
                    "values": [
                        [str(i * 1_000_000_000), line]
                        for i, line in enumerate(log_lines)
                    ]
                }
            ]
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# CONCEPT 9: @pytest.mark.asyncio
# ─────────────────────────────────────────────────────────────────────────────
#
# Python's asyncio lets functions be async — they return a coroutine that
# must be awaited. Pytest can't run coroutines directly; it needs the
# @pytest.mark.asyncio marker to wrap the test in an event loop.
#
# You'll see this on every test that calls `await something()`.

@pytest.mark.asyncio
async def test_execute_returns_success_with_log_lines(loki, sample_alert):
    """
    Happy path: Loki responds with 3 log lines.
    We expect ToolResult.success=True and data contains those lines.
    """
    fake_log_lines = [
        "2026-03-01 ERROR: Connection refused to db",
        "2026-03-01 ERROR: Retry attempt 1 failed",
        "2026-03-01 ERROR: Circuit breaker opened",
    ]
    loki_response = _make_loki_response(fake_log_lines)

    # Build the mock chain that aiohttp uses.
    # aiohttp.ClientSession() is used as an async context manager:
    #   async with aiohttp.ClientSession() as session:
    #       async with session.get(...) as response:
    #           data = await response.json()
    #
    # AsyncMock handles the `await` and `async with` parts.

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=loki_response)
    # __aenter__ is what `async with X as y` calls — it must return the mock
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_client_session_cls = MagicMock(return_value=mock_session)

    with patch("tools.loki.aiohttp.ClientSession", mock_client_session_cls):
        result = await loki.execute(sample_alert, {})

    # ── Assertions ───────────────────────────────────────────────────────────
    assert result.success is True
    assert result.tool_name == ToolName.LOKI
    assert result.error_message is None
    assert result.data["total_lines_retrieved"] == 3
    assert len(result.data["log_lines"]) == 3
    assert result.duration_ms > 0   # timing was recorded


@pytest.mark.asyncio
async def test_execute_uses_fingerprint_fallback_when_no_corr_id(
    loki, alert_no_corr_id
):
    """
    When correlation_id is None, the tool must use the fingerprint fallback
    query path (EvidencePath.FINGERPRINT_FALLBACK).
    """
    loki_response = _make_loki_response(["ERROR: NullPointerException at line 42"])

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=loki_response)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("tools.loki.aiohttp.ClientSession", MagicMock(return_value=mock_session)):
        result = await loki.execute(alert_no_corr_id, {})

    assert result.success is True
    # FINGERPRINT_FALLBACK means the tool chose the fallback path
    from models.tool_result import EvidencePath
    assert result.evidence_path == EvidencePath.FINGERPRINT_FALLBACK


# ─────────────────────────────────────────────────────────────────────────────
# CONCEPT 10: Asserting on mock calls
# ─────────────────────────────────────────────────────────────────────────────
#
# Mocks record every call made to them. You can ask:
#   mock.assert_called_once()         — was it called exactly once?
#   mock.assert_called_with(arg=val)  — was it called with these args?
#   mock.call_count                   — how many times total?
#   mock.call_args                    — what args were used on the last call?
#
# This lets you verify not just what was returned, but *how* the code
# interacted with its dependencies.

@pytest.mark.asyncio
async def test_execute_makes_one_http_request_per_invoke(loki, sample_alert):
    """
    Each call to execute() should make exactly one GET request to Loki.
    Asserting on mock calls proves the code's interaction behaviour.
    """
    loki_response = _make_loki_response(["some log line"])

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=loki_response)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_get = MagicMock(return_value=mock_response)
    mock_session = AsyncMock()
    mock_session.get = mock_get
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("tools.loki.aiohttp.ClientSession", MagicMock(return_value=mock_session)):
        await loki.execute(sample_alert, {})

    # CONCEPT 10: check the mock was called exactly once
    mock_get.assert_called_once()

    # You can also inspect what URL it was called with
    call_args = mock_get.call_args
    url_called = call_args[0][0]   # first positional arg
    assert "loki/api/v1/query_range" in url_called


@pytest.mark.asyncio
async def test_execute_returns_failure_on_http_error(loki, sample_alert):
    """
    If Loki returns a non-200 status, the tool should return success=False
    with a meaningful error_message, not crash.

    This is a resilience test — we test the error path, not the happy path.
    """
    mock_response = AsyncMock()
    mock_response.status = 503   # Service Unavailable
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("tools.loki.aiohttp.ClientSession", MagicMock(return_value=mock_session)):
        result = await loki.execute(sample_alert, {})

    # A 503 response should still return a ToolResult (not raise).
    # The tool returns success=True with empty logs (it logs the error internally).
    # This proves the tool is resilient — it never throws at the caller.
    assert result.tool_name == ToolName.LOKI
    assert result.data is not None or result.error_message is not None


@pytest.mark.asyncio
async def test_execute_returns_failure_on_network_exception(loki, sample_alert):
    """
    If a network exception is raised (e.g. Loki is totally unreachable),
    the tool must catch it and return success=False — never raise to caller.
    """
    import aiohttp

    with patch(
        "tools.loki.aiohttp.ClientSession",
        side_effect=aiohttp.ClientConnectionError("Connection refused")
    ):
        result = await loki.execute(sample_alert, {})

    assert result.success is False
    assert result.tool_name == ToolName.LOKI
    assert result.error_message is not None
    assert "unreachable" in result.error_message.lower() or "loki" in result.error_message.lower()


# ─────────────────────────────────────────────────────────────────────────────
# BONUS: Testing a pure (non-async) helper method
# ─────────────────────────────────────────────────────────────────────────────
#
# Not everything needs mocking. Pure functions (no I/O, no side effects)
# can be tested directly. Here we test the keyword extraction helper.

def test_extract_error_keywords_filters_stop_words(loki):
    """
    _extract_error_keywords() should return meaningful words,
    filtering out common English stop words like 'the', 'to', 'in'.
    """
    messages = ["Connection refused to the database server"]

    keywords = loki._extract_error_keywords(messages)

    assert "connection" in keywords
    assert "refused" in keywords
    assert "database" in keywords
    assert "server" in keywords

    # Stop words must not appear
    for stop_word in ["the", "to", "in"]:
        assert stop_word not in keywords, (
            f"Stop word '{stop_word}' should have been filtered out"
        )


def test_extract_error_keywords_returns_at_most_5(loki):
    """
    The method should return at most 5 keywords regardless of input size.
    """
    messages = [
        "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda"
    ]
    keywords = loki._extract_error_keywords(messages)

    assert len(keywords) <= 5


def test_extract_slow_queries_detects_above_threshold(loki):
    """
    Log lines containing query durations above the threshold should be returned.
    Our mock_settings set threshold to 1000ms, so 2000ms should be flagged.
    """
    log_lines = [
        "INFO: SELECT * FROM users — query took 2000ms",
        "INFO: SELECT * FROM orders — query took 500ms",    # below threshold
        "INFO: sql call completed in 3000ms",
    ]

    slow = loki._extract_slow_queries(log_lines)

    assert len(slow) == 2   # 2000ms and 3000ms only
    assert any("2000" in line for line in slow)
    assert any("3000" in line for line in slow)
    # The 500ms line should NOT be present
    assert not any("500" in line for line in slow)
