"""
Branch Manager - Git branch creation and push operations.

Creates fix branches in the app's repository at GIT_REPOS_ROOT/{app_name}.
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def _run_git(repo_path: Path, args: list[str]) -> str:
    """Run a git command in repo_path. Raises RuntimeError on failure."""
    cmd = ["git", "-C", str(repo_path)] + args
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def create_branch(repo_path: Path, branch_name: str) -> None:
    """
    Create and checkout a new branch in the repository.
    If the branch already exists, delete it first and recreate from HEAD.

    Args:
        repo_path: Path to the git repository
        branch_name: Name of the branch to create (e.g. fix/rca-rca-app-123)
    """
    logger.info(f"Creating branch '{branch_name}' in {repo_path}")
    try:
        await _run_git(repo_path, ["checkout", "-b", branch_name])
    except RuntimeError as e:
        if "already exists" in str(e):
            logger.warning(f"Branch '{branch_name}' already exists — deleting and recreating")
            # Make sure we're not on that branch before deleting
            await _run_git(repo_path, ["checkout", "-"])
            await _run_git(repo_path, ["branch", "-D", branch_name])
            await _run_git(repo_path, ["checkout", "-b", branch_name])
        else:
            raise
    logger.info(f"Branch '{branch_name}' created and checked out")


async def push_branch(repo_path: Path, branch_name: str, remote: str = "origin") -> None:
    """
    Push the branch to the remote.

    Args:
        repo_path: Path to the git repository
        branch_name: Branch name to push
        remote: Remote name (default: origin)
    """
    logger.info(f"Pushing branch '{branch_name}' to {remote}")
    await _run_git(repo_path, ["push", remote, branch_name])
    logger.info(f"Branch '{branch_name}' pushed to {remote}")


def make_branch_name(branch_prefix: str, report_id: str) -> str:
    """
    Generate a branch name from the report ID.

    Args:
        branch_prefix: Prefix from settings (e.g. "fix/rca")
        report_id: RCA report ID

    Returns:
        Branch name like fix/rca-rca-payment-service-1234567890
    """
    # Sanitize report_id for use in branch names (replace spaces/special chars)
    safe_id = report_id.replace(" ", "-").replace("/", "-")
    return f"{branch_prefix}-{safe_id}"
