"""
Jira Ticket Getter - Fetches Jira tickets related to code changes.

Retrieves ticket information and flags risk indicators.

Author: Sam (DEV-2)
"""

import time
import logging
import asyncio
import base64
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timedelta

import aiohttp
from models.alert import AlertPayload
from models.tool_result import ToolResult, ToolName, EvidencePath
from config import Settings
from .base import BaseTool

logger = logging.getLogger(__name__)


class JiraTicketGetter(BaseTool):
    """
    Fetches Jira tickets related to code changes.

    **Process:**
    1. Accept Jira keys (from git commits or context)
    2. Batch fetch tickets via REST API
    3. Extract: summary, type, status, assignee, description, AC
    4. Flag risks: hotfix/emergency labels, In Progress status, missing AC
    5. Fallback: JQL search by project + date range
    """

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.jira_url = settings.JIRA_URL.rstrip('/')
        self.username = settings.JIRA_USERNAME
        self.api_token = settings.JIRA_API_TOKEN
        self.timeout = settings.JIRA_TIMEOUT_SECONDS
        self.max_concurrent = settings.JIRA_MAX_CONCURRENT_REQUESTS

        # Build auth header
        auth_str = f"{self.username}:{self.api_token}"
        auth_bytes = base64.b64encode(auth_str.encode()).decode()
        self.headers = {
            "Authorization": f"Basic {auth_bytes}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Risk flag patterns
        self.risk_labels = {"hotfix", "emergency", "critical-fix", "urgent"}
        self.risk_statuses = {"in progress", "in review", "in development"}

    async def execute(
        self,
        alert: AlertPayload,
        context: dict
    ) -> ToolResult:
        """
        Execute Jira ticket retrieval.

        Args:
            alert: The alert payload
            context: Investigation context (should contain jira_keys from git tool)

        Returns:
            ToolResult with ticket data
        """
        start_time = time.perf_counter()

        try:
            # Get Jira keys from context (passed from Git tool)
            jira_keys = context.get("jira_keys", [])

            if not jira_keys:
                # Fallback: Search by app name and date range
                logger.info("No Jira keys provided. Using JQL fallback.")
                tickets = await self._search_by_jql(
                    app_name=alert.app_name,
                    since_date=alert.alert_time - timedelta(days=7)
                )
            else:
                # Fetch tickets by keys
                logger.info(f"Fetching {len(jira_keys)} Jira ticket(s)")
                tickets = await self._fetch_tickets_batch(jira_keys)

            # Analyze risk flags
            risk_flagged_tickets = [
                ticket for ticket in tickets
                if self._has_risk_flags(ticket)
            ]

            duration_ms = (time.perf_counter() - start_time) * 1000.0

            return ToolResult(
                tool_name=ToolName.JIRA,
                success=True,
                data={
                    "tickets": tickets,
                    "risk_flagged_count": len(risk_flagged_tickets),
                    "risk_flagged_tickets": risk_flagged_tickets,
                    "total_tickets": len(tickets),
                },
                error_message=None,
                duration_ms=duration_ms,
                evidence_path=EvidencePath.LABEL_QUERY if not jira_keys else None,
                timestamp=datetime.utcnow()
            )

        except aiohttp.ClientError as e:
            logger.error(f"Jira connection error: {e}")
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ToolResult(
                tool_name=ToolName.JIRA,
                success=False,
                data=None,
                error_message=f"Jira unreachable: {str(e)}",
                duration_ms=duration_ms,
                evidence_path=None,
                timestamp=datetime.utcnow()
            )

        except Exception as e:
            logger.error(f"Jira tool error: {e}", exc_info=True)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ToolResult(
                tool_name=ToolName.JIRA,
                success=False,
                data=None,
                error_message=f"Jira tool failed: {str(e)}",
                duration_ms=duration_ms,
                evidence_path=None,
                timestamp=datetime.utcnow()
            )

    async def _fetch_tickets_batch(
        self,
        jira_keys: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Fetch multiple Jira tickets with concurrency control.

        Args:
            jira_keys: List of Jira ticket keys (e.g., ["PROJ-123", "PROJ-456"])

        Returns:
            List of ticket dictionaries
        """
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def fetch_one(key: str) -> Optional[Dict[str, Any]]:
            async with semaphore:
                return await self._fetch_ticket(key)

        tasks = [fetch_one(key) for key in jira_keys[:50]]  # Limit to 50
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out None and exceptions
        tickets = [
            result for result in results
            if result is not None and not isinstance(result, Exception)
        ]

        return tickets

    async def _fetch_ticket(self, jira_key: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single Jira ticket.

        Args:
            jira_key: Jira ticket key (e.g., "PROJ-123")

        Returns:
            Ticket dictionary or None if not found
        """
        url = f"{self.jira_url}/rest/api/2/issue/{jira_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    ssl=False
                ) as response:
                    if response.status == 404:
                        logger.warning(f"Jira ticket {jira_key} not found")
                        return None

                    if response.status != 200:
                        logger.error(f"Jira API error for {jira_key}: {response.status}")
                        return None

                    data = await response.json()

                    # Extract relevant fields
                    fields = data.get("fields", {})
                    return {
                        "key": jira_key,
                        "summary": fields.get("summary", ""),
                        "type": fields.get("issuetype", {}).get("name", ""),
                        "status": fields.get("status", {}).get("name", ""),
                        "assignee": fields.get("assignee", {}).get("displayName", "Unassigned") if fields.get("assignee") else "Unassigned",
                        "description": (fields.get("description", "") or "")[:500],  # Limit length
                        "labels": fields.get("labels", []),
                        "created": fields.get("created", ""),
                        "updated": fields.get("updated", ""),
                        # Try to get acceptance criteria (custom field varies by Jira instance)
                        "acceptance_criteria": self._extract_acceptance_criteria(fields),
                    }

        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching Jira ticket {jira_key}")
            return None
        except Exception as e:
            logger.error(f"Error fetching Jira ticket {jira_key}: {e}")
            return None

    async def _search_by_jql(
        self,
        app_name: str,
        since_date: datetime
    ) -> List[Dict[str, Any]]:
        """
        Fallback: Search Jira using JQL when no ticket keys provided.

        Args:
            app_name: Application name
            since_date: Search for tickets updated since this date

        Returns:
            List of ticket dictionaries
        """
        # Construct JQL query
        # Example: project = PROJ AND updated >= "2026-02-22" AND text ~ "app-name"
        since_str = since_date.strftime("%Y-%m-%d")
        jql = f'text ~ "{app_name}" AND updated >= "{since_str}"'

        url = f"{self.jira_url}/rest/api/2/search"

        params = {
            "jql": jql,
            "maxResults": 20,
            "fields": "summary,issuetype,status,assignee,description,labels,created,updated",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    ssl=False
                ) as response:
                    if response.status != 200:
                        logger.error(f"Jira JQL search error: {response.status}")
                        return []

                    data = await response.json()
                    issues = data.get("issues", [])

                    tickets = []
                    for issue in issues:
                        fields = issue.get("fields", {})
                        tickets.append({
                            "key": issue.get("key", ""),
                            "summary": fields.get("summary", ""),
                            "type": fields.get("issuetype", {}).get("name", ""),
                            "status": fields.get("status", {}).get("name", ""),
                            "assignee": fields.get("assignee", {}).get("displayName", "Unassigned") if fields.get("assignee") else "Unassigned",
                            "description": (fields.get("description", "") or "")[:500],
                            "labels": fields.get("labels", []),
                            "created": fields.get("created", ""),
                            "updated": fields.get("updated", ""),
                            "acceptance_criteria": self._extract_acceptance_criteria(fields),
                        })

                    return tickets

        except Exception as e:
            logger.error(f"JQL search failed: {e}")
            return []

    def _extract_acceptance_criteria(self, fields: Dict[str, Any]) -> Optional[str]:
        """
        Try to extract acceptance criteria from ticket fields.
        AC field name varies by Jira configuration.

        Args:
            fields: Jira ticket fields

        Returns:
            Acceptance criteria string or None
        """
        # Common custom field names for AC
        ac_field_names = [
            "customfield_10000",  # Common default
            "customfield_10100",
            "acceptance_criteria",
            "acceptanceCriteria",
        ]

        for field_name in ac_field_names:
            value = fields.get(field_name)
            if value:
                return str(value)[:500]  # Limit length

        return None

    def _has_risk_flags(self, ticket: Dict[str, Any]) -> bool:
        """
        Check if ticket has risk indicators.

        Risk flags:
        - Labels contain hotfix/emergency/critical-fix
        - Status was "In Progress" (may not be code-complete)
        - Missing acceptance criteria

        Args:
            ticket: Ticket dictionary

        Returns:
            True if ticket has risk flags
        """
        # Check labels
        labels = {label.lower() for label in ticket.get("labels", [])}
        if labels.intersection(self.risk_labels):
            return True

        # Check status
        status = ticket.get("status", "").lower()
        if status in self.risk_statuses:
            return True

        # Check for missing AC
        if not ticket.get("acceptance_criteria"):
            return True

        return False

    async def health_check(self) -> bool:
        """
        Check if Jira API is reachable.

        Returns:
            True if healthy, False otherwise
        """
        try:
            url = f"{self.jira_url}/rest/api/2/serverInfo"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                    ssl=False
                ) as response:
                    return response.status == 200
        except Exception:
            return False
