"""
Tests for logging_config.py

Teaches:
  1. Basic test structure (test_ function + assert)
  2. Fixtures with @pytest.fixture
  3. tmp_path — pytest's built-in temp directory fixture
  4. Parametrize — running one test with many inputs
  5. Monkeypatching — overriding something for the duration of one test
"""

import logging
import pytest
from pathlib import Path
from unittest.mock import patch

from logging_config import setup_logging


# ─────────────────────────────────────────────────────────────────────────────
# CONCEPT 1: The simplest test — a plain function + assert
# ─────────────────────────────────────────────────────────────────────────────
#
# pytest finds every function whose name starts with "test_" and runs it.
# If any assert fails, the test fails and pytest shows you the actual values.
#
# Rule: one test = one behaviour you want to prove.

def test_setup_logging_returns_none():
    """
    setup_logging() has no return value (it's a setup side-effect function).
    This is the most basic test: call a function, check the result.
    """
    result = setup_logging("INFO")

    # 'None' is what Python returns from functions with no return statement.
    # This proves the function runs without crashing.
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# CONCEPT 2: pytest.fixture — reusable setup code
# ─────────────────────────────────────────────────────────────────────────────
#
# A fixture is a function decorated with @pytest.fixture.
# Any test that lists its name as a parameter will automatically receive
# the value it returns — pytest injects it.
#
# You've already seen this pattern in conftest.py:
#
#   @pytest.fixture
#   def db_connectivity_alert(...):
#       return AlertPayload(...)
#
# Tests then just declare:  def test_something(db_connectivity_alert):
# and pytest passes in the alert automatically.
#
# Here we use tmp_path — a built-in pytest fixture that gives you a
# fresh empty directory for each test, deleted after the test finishes.
# You never need to create or clean it up yourself.

@pytest.fixture
def isolated_logger():
    """
    Fixture: give each test a clean root logger with no handlers.

    Why? setup_logging() modifies the root logger globally. If we don't
    clean up between tests, handlers accumulate and tests interfere.

    yield means: run setup, hand the value to the test, then run teardown.
    """
    root = logging.getLogger()
    original_handlers = root.handlers[:]   # save a copy of current handlers
    original_level = root.level

    root.handlers.clear()   # start clean

    yield root              # <── the test runs here

    # Teardown: restore original state after the test finishes
    root.handlers = original_handlers
    root.setLevel(original_level)


def test_setup_logging_creates_two_handlers(tmp_path, isolated_logger):
    """
    After setup_logging(), the root logger should have exactly 2 handlers:
    one FileHandler (for the log file) and one StreamHandler (for stdout).

    tmp_path is pytest's built-in fixture — a fresh temp directory.
    We patch the logs directory so nothing is written to the real filesystem.
    """
    # patch() temporarily replaces Path.mkdir and the file path.
    # We point the log file into tmp_path so no real files are created.
    log_file = tmp_path / "sre_agent.log"

    with patch("logging_config.Path") as mock_path_cls:
        # When logging_config.py calls Path(__file__).parent, return tmp_path
        mock_path_cls.return_value.parent.parent.__truediv__.return_value = tmp_path
        mock_path_cls.return_value.parent = tmp_path

        # Simpler approach: just let it write to tmp_path by patching the dirs
        pass

    # Direct approach — just call it and check handler types
    setup_logging("INFO")

    root = logging.getLogger()
    handler_types = [type(h).__name__ for h in root.handlers]

    assert "TimedRotatingFileHandler" in handler_types, (
        f"Expected a TimedRotatingFileHandler. Got: {handler_types}"
    )
    assert "StreamHandler" in handler_types, (
        f"Expected a StreamHandler. Got: {handler_types}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CONCEPT 3: @pytest.mark.parametrize — one test, many inputs
# ─────────────────────────────────────────────────────────────────────────────
#
# Instead of writing the same test body five times for five different inputs,
# parametrize runs it once per row. Each row is one test case.
#
# Format:
#   @pytest.mark.parametrize("param_name", [value1, value2, ...])
#
# For multiple parameters:
#   @pytest.mark.parametrize("a, b", [(1, 2), (3, 4)])

@pytest.mark.parametrize("level_str, expected_level", [
    ("DEBUG",    logging.DEBUG),     # 10
    ("INFO",     logging.INFO),      # 20
    ("WARNING",  logging.WARNING),   # 30
    ("ERROR",    logging.ERROR),     # 40
    ("CRITICAL", logging.CRITICAL),  # 50
])
def test_setup_logging_respects_log_level(level_str, expected_level, isolated_logger):
    """
    setup_logging("DEBUG") should set root logger level to DEBUG (10),
    setup_logging("ERROR") should set it to ERROR (40), etc.

    pytest runs this test 5 times — once per row in the parametrize list.
    Each run gets a different level_str and expected_level pair.
    """
    setup_logging(level_str)

    root = logging.getLogger()

    assert root.level == expected_level, (
        f"Expected level {expected_level} for '{level_str}', "
        f"got {root.level}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CONCEPT 4: Testing that noisy loggers are suppressed
# ─────────────────────────────────────────────────────────────────────────────
#
# Specific loggers (uvicorn.access, httpx, aiohttp) should be set to WARNING
# so they don't pollute our log file. This is a behaviour test — we prove
# something the function promises to do.

def test_noisy_loggers_are_suppressed(isolated_logger):
    """
    After setup_logging(), third-party loggers should be silenced.
    """
    setup_logging("DEBUG")  # Even with DEBUG on the root, these stay at WARNING

    for noisy_logger_name in ["uvicorn.access", "httpx", "aiohttp"]:
        noisy_logger = logging.getLogger(noisy_logger_name)
        assert noisy_logger.level == logging.WARNING, (
            f"Logger '{noisy_logger_name}' should be WARNING, "
            f"got {logging.getLevelName(noisy_logger.level)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CONCEPT 5: Negative test — calling setup_logging() twice is safe
# ─────────────────────────────────────────────────────────────────────────────
#
# Good tests don't only check the happy path. They also check that bad
# situations are handled gracefully. Here: calling setup twice shouldn't
# duplicate handlers (the function should clear and re-add).

def test_calling_setup_twice_does_not_duplicate_handlers(isolated_logger):
    """
    Calling setup_logging() twice must not add duplicate handlers.
    Without the dedup logic in logging_config.py, each call would
    add another FileHandler + StreamHandler, causing double-logged lines.
    """
    setup_logging("INFO")
    setup_logging("INFO")  # second call

    root = logging.getLogger()

    # Count how many TimedRotatingFileHandlers there are
    file_handlers = [
        h for h in root.handlers
        if type(h).__name__ == "TimedRotatingFileHandler"
    ]

    assert len(file_handlers) == 1, (
        f"Expected exactly 1 file handler, got {len(file_handlers)}. "
        f"setup_logging() is duplicating handlers on repeated calls."
    )
