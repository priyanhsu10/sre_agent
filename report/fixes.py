"""
Possible Fixes Generator - Generate ordered fix recommendations.

Author: Riley (DEV-3)
"""

import logging
from typing import List, Dict, Any

from models.hypothesis import FailureCategory
from models.report import PossibleFix, CodeChange

logger = logging.getLogger(__name__)


class PossibleFixesGenerator:
    """
    Generates ordered fix recommendations based on root cause and evidence.

    **Fix Priority:**
    1. Immediate (priority 1-2): Revert, restart, scale
    2. Short-term (priority 3-4): Configuration changes, patches
    3. Long-term (priority 5+): Architectural improvements, monitoring
    """

    # Fix templates by category
    FIX_TEMPLATES = {
        FailureCategory.DB_CONNECTIVITY: [
            {
                "priority": 1,
                "action": "Check database server health and restart if necessary",
                "rationale": "Restore database connectivity immediately",
                "impact": "Immediate resolution if DB server is down"
            },
            {
                "priority": 2,
                "action": "Increase database connection pool size",
                "rationale": "Address connection pool exhaustion",
                "impact": "Allows more concurrent connections"
            },
            {
                "priority": 3,
                "action": "Review and optimize slow queries",
                "rationale": "Reduce connection hold time",
                "impact": "Improves connection availability"
            },
            {
                "priority": 5,
                "action": "Implement connection pooling health checks and alerts",
                "rationale": "Prevent future connection exhaustion",
                "impact": "Proactive monitoring"
            },
        ],

        FailureCategory.DNS_FAILURE: [
            {
                "priority": 1,
                "action": "Verify DNS server is reachable and responding",
                "rationale": "DNS resolution is critical for service communication",
                "impact": "Immediate resolution if DNS server is down"
            },
            {
                "priority": 2,
                "action": "Check /etc/resolv.conf or DNS configuration",
                "rationale": "Ensure correct DNS servers are configured",
                "impact": "Fixes misconfiguration"
            },
            {
                "priority": 3,
                "action": "Flush DNS cache and retry",
                "rationale": "Clear stale DNS entries",
                "impact": "Resolves cached bad entries"
            },
            {
                "priority": 4,
                "action": "Implement DNS caching at application level",
                "rationale": "Reduce dependency on external DNS",
                "impact": "Improves resilience"
            },
        ],

        FailureCategory.CERTIFICATE_EXPIRY: [
            {
                "priority": 1,
                "action": "Renew expired SSL/TLS certificate immediately",
                "rationale": "Certificate expiry blocks secure connections",
                "impact": "Immediate resolution"
            },
            {
                "priority": 2,
                "action": "Update certificate in all relevant services and restart",
                "rationale": "Ensure new certificate is loaded",
                "impact": "Restores secure connections"
            },
            {
                "priority": 4,
                "action": "Implement certificate expiry monitoring (30/14/7 day alerts)",
                "rationale": "Prevent future expiry incidents",
                "impact": "Proactive prevention"
            },
            {
                "priority": 5,
                "action": "Automate certificate renewal (e.g., Let's Encrypt, cert-manager)",
                "rationale": "Eliminate manual certificate management",
                "impact": "Long-term prevention"
            },
        ],

        FailureCategory.NETWORK_INTRA_SERVICE: [
            {
                "priority": 1,
                "action": "Check target service health and restart if down",
                "rationale": "Restore service availability",
                "impact": "Immediate resolution if service is down"
            },
            {
                "priority": 2,
                "action": "Increase timeout values for inter-service calls",
                "rationale": "Prevent premature timeout failures",
                "impact": "Reduces timeout errors"
            },
            {
                "priority": 3,
                "action": "Verify network connectivity between services",
                "rationale": "Check for network partitions or firewall rules",
                "impact": "Identifies network issues"
            },
            {
                "priority": 4,
                "action": "Implement circuit breaker pattern",
                "rationale": "Prevent cascading failures",
                "impact": "Improves system resilience"
            },
        ],

        FailureCategory.CODE_LOGIC_ERROR: [
            # Revert will be added dynamically if code change found
            {
                "priority": 2,
                "action": "Add null checks and defensive programming",
                "rationale": "Prevent NullPointerException and similar errors",
                "impact": "Fixes immediate error"
            },
            {
                "priority": 3,
                "action": "Add comprehensive error handling",
                "rationale": "Gracefully handle edge cases",
                "impact": "Improves stability"
            },
            {
                "priority": 4,
                "action": "Increase test coverage for affected code path",
                "rationale": "Prevent regression",
                "impact": "Long-term quality improvement"
            },
        ],

        FailureCategory.CONFIG_DRIFT: [
            {
                "priority": 1,
                "action": "Restore correct configuration values",
                "rationale": "Fix configuration mismatch",
                "impact": "Immediate resolution"
            },
            {
                "priority": 2,
                "action": "Restart services to load new configuration",
                "rationale": "Ensure configuration is applied",
                "impact": "Activates fix"
            },
            {
                "priority": 4,
                "action": "Implement configuration validation in deployment pipeline",
                "rationale": "Catch configuration errors before production",
                "impact": "Preventative"
            },
            {
                "priority": 5,
                "action": "Use configuration management tools (e.g., GitOps)",
                "rationale": "Track and version all configuration changes",
                "impact": "Long-term prevention"
            },
        ],

        FailureCategory.DEPENDENCY_FAILURE: [
            {
                "priority": 1,
                "action": "Check third-party service status and health",
                "rationale": "Identify if external dependency is down",
                "impact": "Identifies root cause"
            },
            {
                "priority": 2,
                "action": "Implement fallback or degraded mode",
                "rationale": "Continue operating without dependency",
                "impact": "Maintains partial functionality"
            },
            {
                "priority": 3,
                "action": "Add retry logic with exponential backoff",
                "rationale": "Handle transient failures",
                "impact": "Improves resilience"
            },
            {
                "priority": 4,
                "action": "Implement circuit breaker for external dependencies",
                "rationale": "Prevent resource exhaustion on repeated failures",
                "impact": "Protects system health"
            },
        ],

        FailureCategory.MEMORY_RESOURCE_EXHAUSTION: [
            {
                "priority": 1,
                "action": "Restart affected service to clear memory",
                "rationale": "Immediate recovery from OOM state",
                "impact": "Immediate resolution"
            },
            {
                "priority": 2,
                "action": "Increase memory limits for the service",
                "rationale": "Provide more headroom",
                "impact": "Short-term fix"
            },
            {
                "priority": 3,
                "action": "Profile application to identify memory leak",
                "rationale": "Find root cause of memory growth",
                "impact": "Enables targeted fix"
            },
            {
                "priority": 4,
                "action": "Implement memory usage monitoring and alerts",
                "rationale": "Detect memory issues before OOM",
                "impact": "Proactive prevention"
            },
        ],
    }

    def generate_fixes(
        self,
        root_cause_category: FailureCategory,
        is_code_change: bool,
        code_changes: List[CodeChange],
        evidence: Dict[str, Any]
    ) -> List[PossibleFix]:
        """
        Generate ordered list of possible fixes.

        Args:
            root_cause_category: The identified root cause category
            is_code_change: Whether a code change is involved
            code_changes: List of recent code changes
            evidence: Additional evidence for context

        Returns:
            List of PossibleFix objects ordered by priority
        """
        fixes = []

        # Add revert option if code change found (ALWAYS PRIORITY 1)
        if is_code_change and code_changes:
            most_recent = code_changes[0]
            fixes.append(
                PossibleFix(
                    priority=1,
                    action=f"Revert commit {most_recent.commit_hash} by {most_recent.author}",
                    rationale=(
                        f"Recent code change may have introduced {root_cause_category.value}. "
                        f"Commit message: '{most_recent.message[:100]}'"
                    ),
                    estimated_impact="Immediate resolution if code change is root cause"
                )
            )

        # Add category-specific fixes
        template_fixes = self.FIX_TEMPLATES.get(root_cause_category, [])
        for template in template_fixes:
            # Adjust priority if revert was added
            priority = template["priority"]
            if is_code_change and code_changes:
                priority += 1  # Shift all priorities down

            fixes.append(
                PossibleFix(
                    priority=priority,
                    action=template["action"],
                    rationale=template["rationale"],
                    estimated_impact=template["impact"]
                )
            )

        # Add monitoring improvement (always include)
        monitoring_priority = len(fixes) + 1
        fixes.append(
            PossibleFix(
                priority=monitoring_priority,
                action="Enhance monitoring and alerting for this failure pattern",
                rationale=f"Improve detection and response time for {root_cause_category.value}",
                estimated_impact="Long-term: Faster incident response"
            )
        )

        # Sort by priority
        fixes.sort(key=lambda f: f.priority)

        return fixes
