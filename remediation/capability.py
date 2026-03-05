"""
Capability Check - Validates whether automated code patching via Claude is available.

Called before attempting claude_agent_patch fix type.
If Claude is not available, the workflow falls back to creating the branch
with a FIX_INSTRUCTIONS.md so a developer can apply the fix manually.
"""

import logging
import socket

logger = logging.getLogger(__name__)


def check_claude_available(llm_enabled: bool, api_key: str) -> tuple[bool, str]:
    """
    Check if Claude API is configured and reachable for automated code patching.

    Checks (in order):
      1. LLM_ENABLED setting must be True
      2. LLM_API_KEY must be non-empty
      3. Anthropic API host must be DNS-resolvable (network connectivity check)

    Args:
        llm_enabled: Value of settings.LLM_ENABLED
        api_key: Value of settings.LLM_API_KEY

    Returns:
        (is_available: bool, reason: str)
        reason describes why capability is missing (if not available)
        or confirms it is ready (if available).
    """
    if not llm_enabled:
        reason = (
            "Claude agent patching is disabled. "
            "Set LLM_ENABLED=true in your .env to enable automated code fixes."
        )
        logger.warning(f"Claude capability check FAILED: {reason}")
        return False, reason

    if not api_key or api_key.strip() == "":
        reason = (
            "Claude API key is not configured. "
            "Set LLM_API_KEY=<your-anthropic-api-key> in your .env to enable automated code fixes."
        )
        logger.warning(f"Claude capability check FAILED: {reason}")
        return False, reason

    # Quick network connectivity check (DNS only, no actual API call)
    if not _is_anthropic_reachable():
        reason = (
            "Cannot reach api.anthropic.com — check network connectivity or firewall settings. "
            "Claude agent patching will be skipped."
        )
        logger.warning(f"Claude capability check FAILED: {reason}")
        return False, reason

    logger.info("Claude capability check PASSED: LLM_ENABLED=true, API key present, network reachable")
    return True, "Claude API is configured and reachable"


def _is_anthropic_reachable(host: str = "api.anthropic.com", timeout: float = 3.0) -> bool:
    """DNS resolution check for the Anthropic API host."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(host, 443)
        return True
    except (socket.gaierror, OSError):
        return False
