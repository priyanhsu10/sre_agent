"""
Loki Log Retriever - Fetches logs from Loki using LogQL.

Supports correlation_id-based queries with fallback to fingerprint matching
when correlation_id is null.

Author: Sam (DEV-2)
"""

import re
import time
import logging
from typing import Optional, List, Dict, Any, Set
from datetime import datetime, timedelta
from collections import Counter

import aiohttp
from models.alert import AlertPayload
from models.tool_result import ToolResult, ToolName, EvidencePath
from config import Settings
from .base import BaseTool

logger = logging.getLogger(__name__)


class LokiLogRetriever(BaseTool):
    """
    Retrieves logs from Loki with intelligent fallback strategies.

    **Query Strategies:**
    1. Primary: Query by correlation_id if present
    2. Fallback: Query by app label + error fingerprint if correlation_id is null

    **Extraction:**
    - Stack traces (deduplicated by signature)
    - Slow queries (> threshold)
    - Key error log lines
    """

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.base_url = settings.LOKI_URL.rstrip('/')
        self.timeout = settings.LOKI_TIMEOUT_SECONDS
        self.max_lines = settings.LOKI_MAX_LINES
        self.lookback_minutes = settings.LOKI_LOOKBACK_MINUTES
        self.slow_query_threshold_ms = settings.SLOW_QUERY_THRESHOLD_MS

        # Stack trace patterns
        self.stack_trace_patterns = [
            re.compile(r'Traceback \(most recent call last\):.*?(?=\n\S|\Z)', re.DOTALL),
            re.compile(r'Exception in thread.*?(?=\n\S|\Z)', re.DOTALL),
            re.compile(r'Caused by:.*?(?=\n\S|\Z)', re.DOTALL),
            re.compile(r'\tat .*?\(.*?:\d+\)', re.MULTILINE),
        ]

        # Slow query patterns
        self.slow_query_pattern = re.compile(
            r'(query|sql).*?(\d+)\s*(ms|milliseconds)',
            re.IGNORECASE
        )

    async def execute(
        self,
        alert: AlertPayload,
        context: dict
    ) -> ToolResult:
        """
        Execute Loki log retrieval with correlation_id fallback.

        Args:
            alert: The alert payload
            context: Investigation context (not used currently)

        Returns:
            ToolResult with log evidence
        """
        start_time = time.perf_counter()

        try:
            # Check if we have any correlation IDs
            correlation_ids = [
                e.correlation_id for e in alert.errors
                if e.correlation_id is not None
            ]

            if correlation_ids:
                # Primary path: Query by correlation_id
                logger.info(
                    f"Querying Loki with {len(correlation_ids)} correlation_id(s)"
                )
                log_lines, evidence_path = await self._query_by_correlation_ids(
                    app_name=alert.app_name,
                    correlation_ids=correlation_ids,
                    alert_time=alert.alert_time
                )
            else:
                # Fallback path: Query by error fingerprint
                logger.warning(
                    f"No correlation_ids available. Using fingerprint fallback for {alert.app_name}"
                )
                log_lines, evidence_path = await self._query_by_fingerprint(
                    app_name=alert.app_name,
                    error_messages=[e.error_message for e in alert.errors],
                    alert_time=alert.alert_time
                )

            # Extract evidence from log lines
            stack_traces = self._extract_stack_traces(log_lines)
            slow_queries = self._extract_slow_queries(log_lines)
            key_log_lines = self._extract_key_log_lines(log_lines)

            duration_ms = (time.perf_counter() - start_time) * 1000.0

            return ToolResult(
                tool_name=ToolName.LOKI,
                success=True,
                data={
                    "log_lines": log_lines[:100],  # Limit for report size
                    "stack_traces": stack_traces,
                    "slow_queries": slow_queries,
                    "key_log_lines": key_log_lines,
                    "total_lines_retrieved": len(log_lines),
                    "total_error_count": len(log_lines),
                },
                error_message=None,
                duration_ms=duration_ms,
                evidence_path=evidence_path,
                timestamp=datetime.utcnow()
            )

        except aiohttp.ClientError as e:
            logger.error(f"Loki connection error: {e}")
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ToolResult(
                tool_name=ToolName.LOKI,
                success=False,
                data=None,
                error_message=f"Loki unreachable: {str(e)}",
                duration_ms=duration_ms,
                evidence_path=None,
                timestamp=datetime.utcnow()
            )

        except Exception as e:
            logger.error(f"Loki tool error: {e}", exc_info=True)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ToolResult(
                tool_name=ToolName.LOKI,
                success=False,
                data=None,
                error_message=f"Loki tool failed: {str(e)}",
                duration_ms=duration_ms,
                evidence_path=None,
                timestamp=datetime.utcnow()
            )

    async def _query_by_correlation_ids(
        self,
        app_name: str,
        correlation_ids: List[str],
        alert_time: datetime
    ) -> tuple[List[str], EvidencePath]:
        """
        Query Loki using correlation IDs.

        Args:
            app_name: Application name
            correlation_ids: List of correlation IDs
            alert_time: Alert timestamp

        Returns:
            (log_lines, evidence_path)
        """
        # Build LogQL query
        # {app="app_name"} |= "correlation_id_1" or "correlation_id_2"
        correlation_filters = ' or '.join([f'"{cid}"' for cid in correlation_ids])
        query = f'{{app="{app_name}"}} |= {correlation_filters}'

        log_lines = await self._execute_logql_query(query, alert_time)

        return log_lines, EvidencePath.CORRELATION_ID

    async def _query_by_fingerprint(
        self,
        app_name: str,
        error_messages: List[str],
        alert_time: datetime
    ) -> tuple[List[str], EvidencePath]:
        """
        Fallback query when correlation_id is null.
        Uses error message keywords to create a fingerprint.

        Args:
            app_name: Application name
            error_messages: Error messages from alert
            alert_time: Alert timestamp

        Returns:
            (log_lines, evidence_path)
        """
        # Extract key terms from error messages for fingerprinting
        # E.g., "Connection refused to database" -> ["connection", "refused", "database"]
        keywords = self._extract_error_keywords(error_messages)

        if not keywords:
            # If no keywords, just query by app and ERROR level
            query = f'{{app="{app_name}"}} |~ "(?i)(error|exception|fatal)"'
        else:
            # Query by app + any of the keywords
            keyword_filters = '|'.join(keywords[:3])  # Top 3 keywords
            query = f'{{app="{app_name}"}} |~ "(?i)({keyword_filters})"'

        log_lines = await self._execute_logql_query(query, alert_time)

        return log_lines, EvidencePath.FINGERPRINT_FALLBACK

    async def _execute_logql_query(
        self,
        query: str,
        alert_time: datetime
    ) -> List[str]:
        """
        Execute LogQL query against Loki API.

        Args:
            query: LogQL query string
            alert_time: Alert timestamp for time range

        Returns:
            List of log lines
        """
        # Calculate time range
        end_time = alert_time
        start_time = alert_time - timedelta(minutes=self.lookback_minutes)

        # Loki query_range API endpoint
        url = f"{self.base_url}/loki/api/v1/query_range"

        params = {
            "query": query,
            "start": int(start_time.timestamp() * 1e9),  # Nanoseconds
            "end": int(end_time.timestamp() * 1e9),
            "limit": self.max_lines,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                if response.status != 200:
                    logger.error(f"Loki API error: {response.status}")
                    return []

                data = await response.json()

                # Extract log lines from Loki response
                log_lines = []
                if data.get("status") == "success":
                    result = data.get("data", {}).get("result", [])
                    for stream in result:
                        values = stream.get("values", [])
                        for value in values:
                            # value = [timestamp_ns, log_line]
                            if len(value) >= 2:
                                log_lines.append(value[1])

                logger.info(f"Loki query returned {len(log_lines)} log lines")
                return log_lines

    def _extract_error_keywords(self, error_messages: List[str]) -> List[str]:
        """
        Extract significant keywords from error messages for fingerprinting.

        Args:
            error_messages: List of error messages

        Returns:
            List of keywords
        """
        # Common words to exclude
        stop_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'been', 'be',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
            'should', 'may', 'might', 'can'
        }

        keywords = []
        for msg in error_messages:
            # Extract words (alphanumeric sequences)
            words = re.findall(r'\b[a-z]{3,}\b', msg.lower())
            # Filter out stop words
            keywords.extend([w for w in words if w not in stop_words])

        # Count frequency and return top keywords
        word_counts = Counter(keywords)
        return [word for word, count in word_counts.most_common(5)]

    def _extract_stack_traces(self, log_lines: List[str]) -> List[str]:
        """
        Extract and deduplicate stack traces from log lines.

        Args:
            log_lines: Raw log lines

        Returns:
            List of unique stack traces
        """
        stack_traces: Set[str] = set()

        combined_logs = '\n'.join(log_lines)

        for pattern in self.stack_trace_patterns:
            matches = pattern.findall(combined_logs)
            for match in matches:
                # Normalize and deduplicate
                normalized = match.strip()
                if len(normalized) > 50:  # Ignore very short matches
                    stack_traces.add(normalized[:1000])  # Limit length

        return list(stack_traces)[:10]  # Top 10 unique stack traces

    def _extract_slow_queries(self, log_lines: List[str]) -> List[str]:
        """
        Extract slow queries (queries exceeding threshold).

        Args:
            log_lines: Raw log lines

        Returns:
            List of slow query log lines
        """
        slow_queries = []

        for line in log_lines:
            match = self.slow_query_pattern.search(line)
            if match:
                duration_str = match.group(2)
                try:
                    duration_ms = int(duration_str)
                    if duration_ms > self.slow_query_threshold_ms:
                        slow_queries.append(line[:500])  # Limit line length
                except ValueError:
                    continue

        return slow_queries[:20]  # Top 20 slow queries

    def _extract_key_log_lines(self, log_lines: List[str]) -> List[str]:
        """
        Extract key log lines (ERROR/FATAL level).

        Args:
            log_lines: Raw log lines

        Returns:
            List of key error log lines
        """
        key_lines = []
        error_pattern = re.compile(r'\b(ERROR|FATAL|CRITICAL|Exception|Error)\b', re.IGNORECASE)

        for line in log_lines:
            if error_pattern.search(line):
                key_lines.append(line[:500])  # Limit line length

        return key_lines[:50]  # Top 50 key lines

    async def health_check(self) -> bool:
        """
        Check if Loki is reachable.

        Returns:
            True if healthy, False otherwise
        """
        try:
            url = f"{self.base_url}/ready"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    return response.status == 200
        except Exception:
            return False
