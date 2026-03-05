"""
Alert Deduplicator - Prevents duplicate investigations for the same alert.

An alert is considered a duplicate when:
  - Same fingerprint (app_name + environment + sorted error messages)
  - AND the existing investigation is in-flight OR completed within DEDUP_WINDOW_MINUTES

Retries ARE allowed when:
  - The previous investigation failed (status="failed")
  - The dedup window has expired

Fingerprint is content-based — alert_time is intentionally excluded so that
the same error firing multiple times in quick succession is detected as a duplicate
even if the timestamps differ.
"""

import hashlib
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DedupEntry:
    investigation_id: str
    status: str          # "in_progress" | "completed" | "failed"
    registered_at: datetime
    alert_time: datetime
    app_name: str
    environment: str


class AlertDeduplicator:
    """
    In-memory dedup registry for alert fingerprints.

    Thread-safe for asyncio (single-threaded event loop) — no locks needed.
    Entries expire after `window_minutes` unless an investigation is in-flight.
    """

    def __init__(self, window_minutes: int = 30):
        self.window_minutes = window_minutes
        self._registry: dict[str, DedupEntry] = {}

    # ── Public API ───────────────────────────────────────────────────────────

    def check(self, fingerprint: str) -> Optional[DedupEntry]:
        """
        Check whether this alert fingerprint is a duplicate.

        Returns:
            DedupEntry if this is a duplicate that should be suppressed.
            None if the alert is new / should be investigated.
        """
        self._evict_expired()
        entry = self._registry.get(fingerprint)
        if entry is None:
            return None

        # Always block in-flight investigations (regardless of window)
        if entry.status == "in_progress":
            logger.info(
                f"Duplicate alert suppressed — investigation {entry.investigation_id} "
                f"is already in progress for {entry.app_name}/{entry.environment}"
            )
            return entry

        # Block completed investigations within the dedup window
        if entry.status == "completed":
            age = datetime.utcnow() - entry.registered_at
            if age < timedelta(minutes=self.window_minutes):
                logger.info(
                    f"Duplicate alert suppressed — investigation {entry.investigation_id} "
                    f"completed {int(age.total_seconds() // 60)}m ago "
                    f"(window={self.window_minutes}m) for {entry.app_name}/{entry.environment}"
                )
                return entry

        # Failed investigations → allow retry
        if entry.status == "failed":
            logger.info(
                f"Previous investigation {entry.investigation_id} failed — "
                f"allowing retry for {entry.app_name}/{entry.environment}"
            )
            return None

        return None

    def register(self, fingerprint: str, investigation_id: str, alert) -> None:
        """
        Register a new in-flight investigation.

        Args:
            fingerprint: Alert content fingerprint
            investigation_id: Investigation ID being started
            alert: AlertPayload
        """
        self._registry[fingerprint] = DedupEntry(
            investigation_id=investigation_id,
            status="in_progress",
            registered_at=datetime.utcnow(),
            alert_time=alert.alert_time,
            app_name=alert.app_name,
            environment=alert.environment.value,
        )
        logger.debug(f"Registered fingerprint {fingerprint[:12]}… → {investigation_id}")

    def mark_completed(self, fingerprint: str) -> None:
        """Mark investigation as completed (blocks duplicates for window_minutes)."""
        if fingerprint in self._registry:
            self._registry[fingerprint].status = "completed"
            logger.debug(f"Marked {fingerprint[:12]}… as completed")

    def mark_failed(self, fingerprint: str) -> None:
        """Mark investigation as failed (allows retry on next alert)."""
        if fingerprint in self._registry:
            self._registry[fingerprint].status = "failed"
            logger.debug(f"Marked {fingerprint[:12]}… as failed")

    def stats(self) -> dict:
        """Return current registry stats (for /webhook/health endpoint)."""
        self._evict_expired()
        by_status: dict[str, int] = {}
        for e in self._registry.values():
            by_status[e.status] = by_status.get(e.status, 0) + 1
        return {
            "total_tracked": len(self._registry),
            "by_status": by_status,
            "window_minutes": self.window_minutes,
        }

    # ── Internal ─────────────────────────────────────────────────────────────

    def _evict_expired(self) -> None:
        """Remove completed/failed entries older than window_minutes."""
        cutoff = datetime.utcnow() - timedelta(minutes=self.window_minutes)
        expired = [
            fp for fp, entry in self._registry.items()
            if entry.status in ("completed", "failed")
            and entry.registered_at < cutoff
        ]
        for fp in expired:
            logger.debug(f"Evicted expired dedup entry {fp[:12]}…")
            del self._registry[fp]


# ── Fingerprinting ────────────────────────────────────────────────────────────

def make_fingerprint(alert) -> str:
    """
    Generate a stable content fingerprint for an alert.

    Includes: app_name, environment, sorted+normalized error messages.
    Excludes: alert_time, correlation_ids (so retries with new timestamps
              are still recognised as duplicates).

    Returns:
        12-character hex digest (first 12 chars of SHA-256)
    """
    # Normalise each error: lowercase, strip whitespace
    messages = sorted(
        e.error_message.lower().strip()
        for e in alert.errors
        if e.error_message
    )
    raw = "|".join([
        alert.app_name.lower().strip(),
        alert.environment.value.lower(),
        *messages,
    ])
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return digest  # full 64 chars — truncated in logs only


# ── Module-level singleton (shared across all requests in this process) ───────

_deduplicator: Optional[AlertDeduplicator] = None


def get_deduplicator() -> AlertDeduplicator:
    """Return the module-level deduplicator, initialised on first call."""
    global _deduplicator
    if _deduplicator is None:
        from config import settings
        _deduplicator = AlertDeduplicator(
            window_minutes=getattr(settings, "DEDUP_WINDOW_MINUTES", 30)
        )
    return _deduplicator
