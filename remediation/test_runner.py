"""
Test Runner - Auto-detects project runtime and executes tests.

Supports: Python (pytest), Spring Boot (Maven/Gradle), React/Node (npm).
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


def detect_runtime(repo_path: Path) -> Tuple[str, str]:
    """
    Auto-detect project runtime and return appropriate test command.

    Args:
        repo_path: Path to the repository root

    Returns:
        Tuple of (runtime_name, test_command)
        runtime_name: "python" | "spring_boot" | "react" | "unknown"
    """
    # Spring Boot — Maven
    if (repo_path / "pom.xml").exists():
        if (repo_path / "mvnw").exists():
            return "spring_boot", "./mvnw test -B"
        return "spring_boot", "mvn test -B"

    # Spring Boot — Gradle
    if (repo_path / "build.gradle").exists() or (repo_path / "build.gradle.kts").exists():
        if (repo_path / "gradlew").exists():
            return "spring_boot", "./gradlew test"
        return "spring_boot", "gradle test"

    # React / Node
    if (repo_path / "package.json").exists():
        if (repo_path / "yarn.lock").exists():
            return "react", "yarn test --watchAll=false"
        return "react", "npm test -- --watchAll=false"

    # Python — use the current interpreter so venv pytest is picked up
    for indicator in ("pytest.ini", "pyproject.toml", "setup.py", "requirements.txt"):
        if (repo_path / indicator).exists():
            return "python", f"{sys.executable} -m pytest"

    # Fallback
    return "unknown", "make test"


async def run_tests(repo_path: Path, test_command: str, timeout_seconds: int = 300) -> Tuple[bool, str]:
    """
    Run the test command in the repository.

    Args:
        repo_path: Path to the repository
        test_command: Command to execute
        timeout_seconds: Max seconds to wait

    Returns:
        Tuple of (passed, output_tail)
    """
    logger.info(f"Running tests: '{test_command}' in {repo_path}")

    try:
        args = test_command.split()
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return False, f"Tests timed out after {timeout_seconds}s"

        output = stdout.decode(errors="replace")
        output_tail = output[-2000:] if len(output) > 2000 else output
        passed = process.returncode == 0

        logger.info(f"Tests {'passed' if passed else 'failed'} (exit code {process.returncode})")
        return passed, output_tail

    except Exception as e:
        logger.error(f"Failed to run tests: {e}", exc_info=True)
        return False, f"Test execution error: {str(e)}"
