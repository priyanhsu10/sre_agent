"""
Fix Applier - Applies the identified fix to the current branch.

Supports three fix types:
  1. revert               - git revert the suspect commit (is_code_change=True)
  2. claude_agent_patch   - agentic multi-turn fix via CodeFixAgent (requires Claude)
  3. manual_instructions  - Claude unavailable fallback: creates FIX_INSTRUCTIONS.md
                            on the branch so a developer can apply the fix manually
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional

from models.report import RCAReport
from models.hypothesis import FailureCategory

logger = logging.getLogger(__name__)


def determine_fix_type(report: RCAReport, llm_enabled: bool, claude_available: bool) -> str:
    """
    Determine which fix type to apply based on report and Claude availability.

    Args:
        report: The completed RCA report
        llm_enabled: settings.LLM_ENABLED
        claude_available: Result of capability.check_claude_available()

    Returns:
        "revert"               - git revert the suspect commit
        "claude_agent_patch"   - Claude agentic fix (only when claude_available=True)
        "manual_instructions"  - Claude not available; create FIX_INSTRUCTIONS.md
        "none"                 - no actionable code fix possible
    """
    if report.is_code_change and report.code_changes:
        # Check if priority 1 fix is a revert
        if report.possible_fixes:
            top_fix = min(report.possible_fixes, key=lambda f: f.priority)
            if top_fix.action.lower().startswith("revert commit"):
                return "revert"

    if report.root_cause_category == FailureCategory.CODE_LOGIC_ERROR:
        if claude_available:
            return "claude_agent_patch"
        # Claude not available — create manual instructions branch
        return "manual_instructions"

    # Fallback: if is_code_change but no explicit revert action, still try revert
    if report.is_code_change and report.code_changes:
        return "revert"

    return "none"


async def apply_revert(
    repo_path: Path,
    commit_hash: str,
) -> Tuple[bool, str]:
    """
    Apply a git revert of the given commit hash.

    Args:
        repo_path: Path to the app's git repository
        commit_hash: Short or full commit hash to revert

    Returns:
        Tuple of (success, description)
    """
    logger.info(f"Applying git revert for commit {commit_hash} in {repo_path}")

    # Resolve to full hash (in case short hash was stored)
    full_hash = await _resolve_full_hash(repo_path, commit_hash)
    if not full_hash:
        return False, f"Could not resolve commit hash: {commit_hash}"

    try:
        process = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_path),
            "revert", "--no-edit", full_hash,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error = stderr.decode().strip()
            logger.error(f"git revert failed: {error}")
            return False, f"git revert failed: {error}"

        logger.info(f"Successfully reverted commit {full_hash}")
        return True, f"Reverted commit {full_hash} ({commit_hash[:12]})"

    except Exception as e:
        logger.error(f"Revert error: {e}", exc_info=True)
        return False, f"Revert error: {str(e)}"


async def apply_agent_patch(
    repo_path: Path,
    report: RCAReport,
    test_command: str,
    test_runtime: str,
    api_key: str,
    model: str,
    test_timeout_seconds: int,
    max_iterations: int,
) -> Tuple[bool, str, int]:
    """
    Apply a code fix using the multi-turn Claude agent.

    Returns:
        Tuple of (success, fix_description, iterations_used)
    """
    from remediation.code_fix_agent import CodeFixAgent

    stack_traces = []
    if report.log_evidence:
        stack_traces = report.log_evidence.stack_traces[:3]

    files_changed = []
    for cc in report.code_changes[:3]:
        files_changed.extend(cc.files_changed)

    agent = CodeFixAgent(
        api_key=api_key,
        model=model,
        repo_path=repo_path,
        test_command=test_command,
        test_timeout_seconds=test_timeout_seconds,
        max_iterations=max_iterations,
    )

    success, description, iterations = await agent.run(
        root_cause=report.root_cause,
        stack_traces=stack_traces,
        files_changed=files_changed,
        test_runtime=test_runtime,
    )

    # Commit written files if any changes were made
    if agent._files_written:
        try:
            agent.stage_and_commit()
        except Exception as e:
            logger.warning(f"Could not commit agent changes: {e}")

    return success, description, iterations


async def apply_manual_instructions(
    repo_path: Path,
    report: RCAReport,
    claude_unavailable_reason: str,
) -> Tuple[bool, str, str]:
    """
    Create a FIX_INSTRUCTIONS.md on the branch when Claude is not available.

    Writes a human-readable file containing:
      - Root cause summary
      - Stack traces
      - Suggested fixes from the RCA report
      - Instructions for a developer to apply the fix manually

    The file is staged and committed so it appears on the branch.

    Args:
        repo_path: Path to the app's git repository
        report: The completed RCA report
        claude_unavailable_reason: Why Claude couldn't be used

    Returns:
        Tuple of (success, fix_description, file_path_str)
    """
    instructions_path = repo_path / "FIX_INSTRUCTIONS.md"
    logger.info(f"Creating FIX_INSTRUCTIONS.md at {instructions_path}")

    stack_trace_text = ""
    if report.log_evidence and report.log_evidence.stack_traces:
        traces = "\n---\n".join(report.log_evidence.stack_traces[:3])
        stack_trace_text = f"\n## Stack Traces\n```\n{traces}\n```\n"

    fixes_text = ""
    for fix in sorted(report.possible_fixes, key=lambda f: f.priority):
        fixes_text += (
            f"### Priority {fix.priority}: {fix.action}\n"
            f"**Rationale**: {fix.rationale}\n"
            f"**Expected impact**: {fix.estimated_impact}\n\n"
        )

    code_changes_text = ""
    for cc in report.code_changes[:5]:
        files = ", ".join(cc.files_changed[:10])
        flags = f" ⚠️ Risk flags: {', '.join(cc.risk_flags)}" if cc.risk_flags else ""
        code_changes_text += (
            f"- `{cc.commit_hash[:12]}` by **{cc.author}** "
            f"— *{cc.message[:100]}*{flags}\n"
            f"  Files: `{files}`\n"
        )

    content = f"""# Fix Instructions — RCA Report `{report.report_id}`

> **Auto-generated** by SRE Remediation Agent on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
>
> ⚠️ **Automated patching was skipped** because Claude is not available:
> *{claude_unavailable_reason}*
>
> A developer should apply the fix below manually, then delete this file and push.

---

## Root Cause Summary

| Field | Value |
|-------|-------|
| **Category** | `{report.root_cause_category.value}` |
| **Confidence** | `{report.confidence_level.value}` |
| **Code change involved** | `{report.is_code_change}` |
| **Application** | `{report.app_name}` |
| **Severity** | `{report.severity}` |

**Root cause**: {report.root_cause}

---

## Suggested Fixes (apply in priority order)

{fixes_text}
---

## Recent Code Changes (suspects)

{code_changes_text if code_changes_text else '_No recent code changes recorded._'}
{stack_trace_text}
---

## How to Apply the Fix

1. Check out this branch locally:
   ```bash
   git fetch origin
   git checkout {repo_path.name}  # replace with actual branch name
   ```

2. Apply the priority-1 fix listed above.

3. Run the test suite to verify:
   ```bash
   # For Python:    pytest
   # For Maven:     ./mvnw test
   # For Gradle:    ./gradlew test
   # For React:     npm test -- --watchAll=false
   ```

4. If tests pass, delete this file and commit:
   ```bash
   rm FIX_INSTRUCTIONS.md
   git add -A
   git commit -m "fix: apply manual fix for {report.root_cause_category.value}"
   git push origin <branch-name>
   ```

5. Open a pull request for review.

---

> To enable automated code fixing, set `LLM_ENABLED=true` and `LLM_API_KEY=<your-key>` in `.env`.
"""

    try:
        instructions_path.write_text(content)

        # Stage and commit the file
        process = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_path), "add", "FIX_INSTRUCTIONS.md",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()

        process = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_path),
            "commit", "-m",
            f"chore: add fix instructions for RCA {report.report_id} [skip ci]",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            err = stderr.decode().strip()
            logger.warning(f"Could not commit FIX_INSTRUCTIONS.md: {err}")
            return False, f"Created FIX_INSTRUCTIONS.md but commit failed: {err}", str(instructions_path)

        logger.info(f"FIX_INSTRUCTIONS.md created and committed at {instructions_path}")
        return True, "FIX_INSTRUCTIONS.md created with manual fix guidance", str(instructions_path)

    except Exception as e:
        logger.error(f"Failed to create FIX_INSTRUCTIONS.md: {e}", exc_info=True)
        return False, f"Failed to create fix instructions: {str(e)}", ""


async def _resolve_full_hash(repo_path: Path, short_hash: str) -> Optional[str]:
    """Resolve a short commit hash to a full 40-char hash."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo_path),
            "rev-parse", short_hash,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0:
            return stdout.decode().strip()
        return None
    except Exception:
        return None
