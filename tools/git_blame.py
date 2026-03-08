"""
Git Blame Checker - Retrieves recent commits and blame information.

Analyzes recent code changes to identify potential causes of failures.

Author: Sam (DEV-2)
"""

import re
import time
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timedelta

from models.alert import AlertPayload
from models.tool_result import ToolResult, ToolName, EvidencePath
from config import Settings
from .base import BaseTool

logger = logging.getLogger(__name__)


class GitBlameChecker(BaseTool):
    """
    Retrieves git commit history and blame information.

    **Process:**
    1. Locate repository at GIT_REPOS_ROOT/{app_name}
    2. Fetch latest changes (git fetch && git pull)
    3. Get commits within lookback window
    4. Extract changed files per commit
    5. Run git blame on changed line ranges
    6. Extract Jira ticket keys from commit messages
    7. Flag high-churn files
    """

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.repos_root = Path(settings.GIT_REPOS_ROOT)
        self.lookback_days = settings.GIT_LOOKBACK_DAYS
        self.high_churn_threshold = settings.HIGH_CHURN_COMMIT_COUNT
        self.max_diff_lines = settings.MAX_DIFF_LINES

        # Jira key pattern: PROJECT-123
        self.jira_key_pattern = re.compile(r'\b([A-Z]{2,}-\d+)\b')

    async def execute(
        self,
        alert: AlertPayload,
        context: dict
    ) -> ToolResult:
        """
        Execute git blame analysis.

        Args:
            alert: The alert payload
            context: Investigation context

        Returns:
            ToolResult with commit and blame data
        """
        start_time = time.perf_counter()
        logger.info(
            f"GitBlame: starting analysis for app={alert.app_name} "
            f"(lookback={self.lookback_days}d)"
        )

        try:
            # Construct repo path
            repo_path = self.repos_root / alert.app_name

            if not repo_path.exists():
                logger.warning(f"Repository not found at {repo_path}")
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return ToolResult(
                    tool_name=ToolName.GIT_BLAME,
                    success=False,
                    data=None,
                    error_message=f"Repository not found: {repo_path}",
                    duration_ms=duration_ms,
                    evidence_path=None,
                    timestamp=datetime.utcnow()
                )

            # Update repository
            await self._update_repo(repo_path)

            # Get recent commits
            lookback_date = alert.alert_time - timedelta(days=self.lookback_days)
            commits = await self._get_recent_commits(repo_path, lookback_date)

            if not commits:
                logger.info(f"No commits found in last {self.lookback_days} days")
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                return ToolResult(
                    tool_name=ToolName.GIT_BLAME,
                    success=True,
                    data={
                        "commits": [],
                        "high_churn_files": [],
                        "jira_keys": [],
                        "total_commits": 0,
                    },
                    error_message=None,
                    duration_ms=duration_ms,
                    evidence_path=EvidencePath.TIME_RANGE,
                    timestamp=datetime.utcnow()
                )

            # Extract Jira keys from commit messages
            jira_keys = self._extract_jira_keys(commits)

            # Detect high-churn files
            high_churn_files = self._detect_high_churn_files(commits)

            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.info(
                f"GitBlame: found {len(commits)} commits, "
                f"{len(jira_keys)} jira keys, "
                f"{len(high_churn_files)} high-churn files "
                f"({duration_ms:.0f}ms)"
            )

            return ToolResult(
                tool_name=ToolName.GIT_BLAME,
                success=True,
                data={
                    "commits": commits[:20],  # Limit to 20 most recent
                    "high_churn_files": high_churn_files,
                    "jira_keys": list(jira_keys),
                    "total_commits": len(commits),
                },
                error_message=None,
                duration_ms=duration_ms,
                evidence_path=EvidencePath.TIME_RANGE,
                timestamp=datetime.utcnow()
            )

        except Exception as e:
            logger.error(f"Git tool error: {e}", exc_info=True)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ToolResult(
                tool_name=ToolName.GIT_BLAME,
                success=False,
                data=None,
                error_message=f"Git tool failed: {str(e)}",
                duration_ms=duration_ms,
                evidence_path=None,
                timestamp=datetime.utcnow()
            )

    async def _update_repo(self, repo_path: Path) -> None:
        """
        Update repository with latest changes.

        Args:
            repo_path: Path to git repository
        """
        try:
            # git fetch
            await self._run_git_command(repo_path, ["fetch", "--all"])

            # git pull
            await self._run_git_command(repo_path, ["pull", "origin", "HEAD"])

            logger.info(f"Updated repository at {repo_path}")
        except Exception as e:
            logger.warning(f"Failed to update repo (continuing anyway): {e}")

    async def _get_recent_commits(
        self,
        repo_path: Path,
        since_date: datetime
    ) -> List[Dict[str, Any]]:
        """
        Get commits since a specific date.

        Args:
            repo_path: Path to git repository
            since_date: Get commits since this date

        Returns:
            List of commit dictionaries
        """
        # Format: hash|author|timestamp|message
        format_str = "%H|%an|%ai|%s"
        since_str = since_date.strftime("%Y-%m-%d")

        stdout = await self._run_git_command(
            repo_path,
            ["log", f"--since={since_str}", f"--pretty=format:{format_str}"]
        )

        if not stdout.strip():
            return []

        commits = []
        for line in stdout.strip().split('\n'):
            parts = line.split('|', 3)
            if len(parts) == 4:
                commit_hash, author, timestamp_str, message = parts

                # Get changed files for this commit
                files_changed = await self._get_changed_files(repo_path, commit_hash)

                commits.append({
                    "commit_hash": commit_hash[:12],  # Short hash
                    "author": author,
                    "timestamp": timestamp_str,
                    "message": message,
                    "files_changed": files_changed,
                })

        return commits

    async def _get_changed_files(
        self,
        repo_path: Path,
        commit_hash: str
    ) -> List[str]:
        """
        Get list of files changed in a commit.

        Args:
            repo_path: Path to git repository
            commit_hash: Commit hash

        Returns:
            List of file paths
        """
        try:
            stdout = await self._run_git_command(
                repo_path,
                ["diff", "--name-only", f"{commit_hash}~1", commit_hash]
            )

            if not stdout.strip():
                return []

            files = stdout.strip().split('\n')
            return files[:50]  # Limit to 50 files

        except Exception as e:
            logger.warning(f"Failed to get changed files for {commit_hash}: {e}")
            return []

    def _extract_jira_keys(self, commits: List[Dict[str, Any]]) -> Set[str]:
        """
        Extract Jira ticket keys from commit messages.

        Args:
            commits: List of commit dictionaries

        Returns:
            Set of Jira keys (e.g., {"PROJ-123", "PROJ-456"})
        """
        jira_keys: Set[str] = set()

        for commit in commits:
            message = commit.get("message", "")
            matches = self.jira_key_pattern.findall(message)
            jira_keys.update(matches)

        return jira_keys

    def _detect_high_churn_files(
        self,
        commits: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Detect files with high churn (modified frequently).

        Args:
            commits: List of commit dictionaries

        Returns:
            List of high-churn files with commit counts
        """
        file_commit_counts: Dict[str, int] = {}

        for commit in commits:
            for file_path in commit.get("files_changed", []):
                file_commit_counts[file_path] = file_commit_counts.get(file_path, 0) + 1

        # Filter files exceeding threshold
        high_churn = [
            {"file": file_path, "commit_count": count}
            for file_path, count in file_commit_counts.items()
            if count >= self.high_churn_threshold
        ]

        # Sort by commit count descending
        high_churn.sort(key=lambda x: x["commit_count"], reverse=True)

        return high_churn[:10]  # Top 10 high-churn files

    async def _run_git_command(
        self,
        repo_path: Path,
        args: List[str]
    ) -> str:
        """
        Run a git command in the repository.

        Args:
            repo_path: Path to git repository
            args: Git command arguments

        Returns:
            Command stdout

        Raises:
            RuntimeError: If command fails
        """
        cmd = ["git", "-C", str(repo_path)] + args

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            raise RuntimeError(f"Git command failed: {error_msg}")

        return stdout.decode()

    async def health_check(self) -> bool:
        """
        Check if git is available.

        Returns:
            True if git command is available
        """
        try:
            process = await asyncio.create_subprocess_exec(
                "git", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
            return process.returncode == 0
        except Exception:
            return False
