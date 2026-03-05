"""
Remediation Agent - Orchestrates the full code fix workflow.

Pipeline:
  1. Determine fix type (revert vs claude_agent_patch)
  2. Validate app repo exists
  3. Detect test runtime
  4. Create fix branch
  5. Apply fix
  6. Run tests
  7. Push branch if tests pass
  8. Return RemediationResult

Never raises — always returns RemediationResult (with status=failed on error).
"""

import logging
from datetime import datetime
from pathlib import Path

from models.report import RCAReport
from models.remediation import RemediationResult, RemediationStatus
from config import settings

from remediation.branch_manager import create_branch, push_branch, make_branch_name
from remediation.test_runner import detect_runtime, run_tests
from remediation.fix_applier import (
    determine_fix_type, apply_revert, apply_agent_patch, apply_manual_instructions
)
from remediation.capability import check_claude_available

logger = logging.getLogger(__name__)


class RemediationAgent:
    """
    Orchestrates automated code fix workflow after RCA completes.

    Supports:
      - Git revert (when is_code_change=True)
      - Claude agentic patch (when code_logic_error + LLM_ENABLED)
    """

    async def run(self, report: RCAReport) -> RemediationResult:
        """
        Execute the full remediation workflow.

        Args:
            report: Completed RCA report

        Returns:
            RemediationResult with full status trail. Never raises.
        """
        started_at = datetime.utcnow()
        branch_name = make_branch_name(settings.REMEDIATION_BRANCH_PREFIX, report.report_id)

        logger.info(
            f"[{report.report_id}] RemediationAgent starting. "
            f"branch={branch_name}"
        )

        # Build a minimal result to return on any early failure
        base_result = RemediationResult(
            report_id=report.report_id,
            app_name=report.app_name,
            branch_name=branch_name,
            fix_type="unknown",
            fix_description="",
            test_runtime="unknown",
            test_command="",
            tests_passed=False,
            test_output="",
            branch_pushed=False,
            status=RemediationStatus.failed,
            created_at=started_at,
        )

        try:
            # ── Step 1: Check Claude availability ──────────────────────────
            claude_available, claude_reason = check_claude_available(
                llm_enabled=settings.LLM_ENABLED,
                api_key=settings.LLM_API_KEY,
            )
            if not claude_available:
                logger.warning(f"[{report.report_id}] Claude not available: {claude_reason}")

            base_result = base_result.model_copy(update={
                "claude_available": claude_available,
                "claude_unavailable_reason": None if claude_available else claude_reason,
            })

            # ── Step 2: Determine fix type ──────────────────────────────────
            fix_type = determine_fix_type(
                report,
                llm_enabled=settings.LLM_ENABLED,
                claude_available=claude_available,
            )
            if fix_type == "none":
                return base_result.model_copy(update={
                    "fix_type": fix_type,
                    "error_message": "No actionable code fix identified in report",
                    "completed_at": datetime.utcnow(),
                })

            base_result = base_result.model_copy(update={"fix_type": fix_type})
            logger.info(f"[{report.report_id}] Fix type: {fix_type}")

            # ── Step 3: Validate repo ───────────────────────────────────────
            repo_path = Path(settings.GIT_REPOS_ROOT) / report.app_name
            if not repo_path.exists():
                return base_result.model_copy(update={
                    "error_message": f"Repository not found: {repo_path}",
                    "completed_at": datetime.utcnow(),
                })

            # ── Step 4: Detect test runtime ─────────────────────────────────
            test_runtime, test_command = detect_runtime(repo_path)
            logger.info(f"[{report.report_id}] Detected runtime={test_runtime}, cmd='{test_command}'")

            base_result = base_result.model_copy(update={
                "test_runtime": test_runtime,
                "test_command": test_command,
            })

            # ── Step 5: Create branch ───────────────────────────────────────
            await create_branch(repo_path, branch_name)
            base_result = base_result.model_copy(update={"status": RemediationStatus.branch_created})
            logger.info(f"[{report.report_id}] Branch '{branch_name}' created")

            # ── Step 6: Apply fix ───────────────────────────────────────────
            fix_iterations = 1
            commit_hash_reverted = None

            if fix_type == "revert":
                commit_hash = report.code_changes[0].commit_hash
                fix_ok, fix_description = await apply_revert(repo_path, commit_hash)
                commit_hash_reverted = commit_hash if fix_ok else None

            elif fix_type == "claude_agent_patch":
                fix_ok, fix_description, fix_iterations = await apply_agent_patch(
                    repo_path=repo_path,
                    report=report,
                    test_command=test_command,
                    test_runtime=test_runtime,
                    api_key=settings.LLM_API_KEY,
                    model=settings.LLM_MODEL,
                    test_timeout_seconds=settings.REMEDIATION_TEST_TIMEOUT_SECONDS,
                    max_iterations=settings.REMEDIATION_MAX_FIX_ITERATIONS,
                    provider=settings.LLM_PROVIDER,
                    base_url=settings.LLM_BASE_URL or None,
                )

            else:  # manual_instructions — Claude is not available
                logger.info(
                    f"[{report.report_id}] Claude unavailable — creating FIX_INSTRUCTIONS.md "
                    f"on branch '{branch_name}' for manual review"
                )
                fix_ok, fix_description, instructions_file = await apply_manual_instructions(
                    repo_path=repo_path,
                    report=report,
                    claude_unavailable_reason=claude_reason,
                )
                if fix_ok:
                    # Push branch with instructions immediately (no tests needed — it's a guide)
                    try:
                        await push_branch(repo_path, branch_name, settings.REMEDIATION_REMOTE)
                        pushed = True
                    except Exception as push_err:
                        logger.warning(f"[{report.report_id}] Could not push instructions branch: {push_err}")
                        pushed = False

                    return base_result.model_copy(update={
                        "fix_description": fix_description,
                        "manual_instructions_file": instructions_file,
                        "branch_pushed": pushed,
                        "status": RemediationStatus.manual_instructions_created,
                        "completed_at": datetime.utcnow(),
                    })
                else:
                    return base_result.model_copy(update={
                        "fix_description": fix_description,
                        "error_message": fix_description,
                        "completed_at": datetime.utcnow(),
                    })

            if not fix_ok:
                return base_result.model_copy(update={
                    "fix_description": fix_description,
                    "fix_iterations": fix_iterations,
                    "commit_hash_reverted": commit_hash_reverted,
                    "error_message": f"Fix could not be applied: {fix_description}",
                    "completed_at": datetime.utcnow(),
                })

            base_result = base_result.model_copy(update={
                "status": RemediationStatus.fix_applied,
                "fix_description": fix_description,
                "fix_iterations": fix_iterations,
                "commit_hash_reverted": commit_hash_reverted,
            })
            logger.info(f"[{report.report_id}] Fix applied: {fix_description[:100]}")

            # ── Step 7: Run tests ───────────────────────────────────────────
            base_result = base_result.model_copy(update={"status": RemediationStatus.tests_running})
            tests_passed, test_output = await run_tests(
                repo_path, test_command, settings.REMEDIATION_TEST_TIMEOUT_SECONDS
            )

            base_result = base_result.model_copy(update={
                "tests_passed": tests_passed,
                "test_output": test_output,
                "status": RemediationStatus.tests_passed if tests_passed else RemediationStatus.tests_failed,
            })
            logger.info(f"[{report.report_id}] Tests {'passed' if tests_passed else 'failed'}")

            # ── Step 8: Push branch (only if tests pass) ────────────────────
            if tests_passed:
                try:
                    await push_branch(repo_path, branch_name, settings.REMEDIATION_REMOTE)
                    base_result = base_result.model_copy(update={
                        "branch_pushed": True,
                        "status": RemediationStatus.pushed,
                    })
                    logger.info(f"[{report.report_id}] Branch '{branch_name}' pushed")
                except Exception as push_err:
                    logger.warning(
                        f"[{report.report_id}] Push failed (no remote configured?): {push_err}. "
                        f"Branch '{branch_name}' is available locally."
                    )
                    base_result = base_result.model_copy(update={
                        "branch_pushed": False,
                        "status": RemediationStatus.tests_passed,
                        "error_message": f"Tests passed but push failed: {push_err}",
                    })
            else:
                logger.warning(
                    f"[{report.report_id}] Tests failed — branch NOT pushed. "
                    f"Branch '{branch_name}' is available locally for inspection."
                )

            return base_result.model_copy(update={"completed_at": datetime.utcnow()})

        except Exception as e:
            logger.error(f"[{report.report_id}] RemediationAgent failed: {e}", exc_info=True)
            return base_result.model_copy(update={
                "error_message": str(e),
                "status": RemediationStatus.failed,
                "completed_at": datetime.utcnow(),
            })
