"""
Base tool interface for all investigation tools.

All tools (Loki, Git, Jira) must inherit from this base class.

Author: Alex (ARCHITECT)
"""

from abc import ABC, abstractmethod
from models.tool_result import ToolResult
from models.alert import AlertPayload
from config import Settings


class BaseTool(ABC):
    """
    Abstract base class for all investigation tools.

    Sam: All your tools (Loki, Git, Jira) MUST inherit from this
    and implement the execute() method.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    async def execute(
        self,
        alert: AlertPayload,
        context: dict
    ) -> ToolResult:
        """
        Execute the tool and return a standardized result.

        Args:
            alert: The original alert payload
            context: Additional context from previous investigation steps
                     (e.g., current hypotheses, previous tool results)

        Returns:
            ToolResult with success status, data, and metadata

        Raises:
            Never raises exceptions — return ToolResult with success=False instead
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Check if the tool is available and healthy.
        Used by circuit breaker logic.

        Returns:
            True if tool is healthy, False otherwise
        """
        pass
