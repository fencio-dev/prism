"""
Asynchronous JSONL logging for canonicalization predictions.

Logs all canonicalization and enforcement outcomes to file-based JSONL format
with daily rotation and retention policy.

Log Format:
  {
    "timestamp": "2025-01-25T12:34:56.789Z",
    "request_id": "uuid-string",
    "field": "action|resource_type|sensitivity",
    "raw_input": "raw-term",
    "prediction": {
      "canonical": "canonical-term",
      "confidence": 0.95,
      "source": "bert_high|bert_medium|passthrough|error"
    },
    "enforcement_outcome": "ALLOW|DENY|UNKNOWN"  # Optional
  }

Implementation:
- Async background task logging (non-blocking)
- Optional buffering for high-volume scenarios
- Daily file rotation with /var/log/guard/canonicalization/{date}.jsonl naming
- 90-day retention policy with automatic cleanup
- Thread-safe queue for concurrent logging
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CanonicalizedPredictionLog:
    """Single log entry for a canonicalization prediction."""

    def __init__(
        self,
        request_id: str,
        field: str,
        raw_input: str,
        canonical: str,
        confidence: float,
        source: str,
        enforcement_outcome: Optional[str] = None,
    ):
        """
        Initialize log entry.

        Args:
            request_id: Unique request identifier (UUID or similar)
            field: Field name (action, resource_type, sensitivity)
            raw_input: Raw input value
            canonical: Canonicalized value
            confidence: Confidence score [0.0, 1.0]
            source: Source of prediction (bert_high, bert_medium, passthrough, error)
            enforcement_outcome: Optional enforcement result (ALLOW, DENY, UNKNOWN)
        """
        self.timestamp = datetime.utcnow().isoformat() + "Z"
        self.request_id = request_id
        self.field = field
        self.raw_input = raw_input
        self.canonical = canonical
        self.confidence = confidence
        self.source = source
        self.enforcement_outcome = enforcement_outcome

    def to_jsonl(self) -> str:
        """Convert to JSONL line."""
        data = {
            "timestamp": self.timestamp,
            "request_id": self.request_id,
            "field": self.field,
            "raw_input": self.raw_input,
            "prediction": {
                "canonical": self.canonical,
                "confidence": self.confidence,
                "source": self.source,
            },
        }
        if self.enforcement_outcome:
            data["enforcement_outcome"] = self.enforcement_outcome

        return json.dumps(data)


class CanonicalizedPredictionLogger:
    """
    Asynchronous JSONL logger for canonicalization predictions.

    Features:
    - Non-blocking async logging
    - Daily file rotation
    - Automatic retention cleanup (90 days)
    - Thread-safe queue buffering
    """

    def __init__(
        self,
        log_dir: str = "/var/log/guard/canonicalization",
        retention_days: int = 90,
        buffer_size: int = 1000,
        flush_interval_sec: float = 5.0,
    ):
        """
        Initialize logger.

        Args:
            log_dir: Directory for JSONL logs
            retention_days: Number of days to keep logs (default 90)
            buffer_size: Number of entries to buffer before flush
            flush_interval_sec: Maximum time between flushes

        Raises:
            ValueError: If log_dir cannot be created
        """
        self.log_dir = Path(log_dir)
        self.retention_days = retention_days
        self.buffer_size = buffer_size
        self.flush_interval_sec = flush_interval_sec

        # Create log directory
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Canonicalization log directory: {self.log_dir}")
        except Exception as e:
            logger.error(f"Failed to create log directory {self.log_dir}: {e}")
            raise ValueError(f"Cannot create log directory: {e}")

        # Buffer for batching writes
        self.buffer: list[str] = []
        self.buffer_lock = asyncio.Lock()

        # File handle for current day
        self.current_file: Optional[Path] = None
        self.current_file_handle: Optional[object] = None  # Keep type open

        # Background flush task
        self.flush_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start background flush task."""
        if self.flush_task is None:
            self.flush_task = asyncio.create_task(self._flush_loop())
            logger.info("Canonicalization logger started")

    async def stop(self) -> None:
        """Stop background flush task and flush remaining entries."""
        if self.flush_task:
            self.flush_task.cancel()
            try:
                await self.flush_task
            except asyncio.CancelledError:
                pass

        # Final flush
        await self.flush()
        logger.info("Canonicalization logger stopped")

    def _get_current_logfile(self) -> Path:
        """
        Get logfile path for today.

        Returns:
            Path: /var/log/guard/canonicalization/{YYYY-MM-DD}.jsonl
        """
        today = datetime.utcnow().date()
        return self.log_dir / f"{today.isoformat()}.jsonl"

    def _should_rotate(self) -> bool:
        """Check if we need to rotate to a new day's logfile."""
        current_expected = self._get_current_logfile()
        return self.current_file != current_expected

    async def log_prediction(
        self,
        request_id: str,
        field: str,
        raw_input: str,
        canonical: str,
        confidence: float,
        source: str,
        enforcement_outcome: Optional[str] = None,
    ) -> None:
        """
        Log a canonicalization prediction.

        Non-blocking: adds to buffer for async write.

        Args:
            request_id: Unique request identifier
            field: Field name (action, resource_type, sensitivity)
            raw_input: Raw input value
            canonical: Canonicalized value
            confidence: Confidence score [0.0, 1.0]
            source: Source of prediction
            enforcement_outcome: Optional enforcement result
        """
        try:
            entry = CanonicalizedPredictionLog(
                request_id=request_id,
                field=field,
                raw_input=raw_input,
                canonical=canonical,
                confidence=confidence,
                source=source,
                enforcement_outcome=enforcement_outcome,
            )

            jsonl_line = entry.to_jsonl()

            async with self.buffer_lock:
                self.buffer.append(jsonl_line)

                # Flush if buffer is full
                if len(self.buffer) >= self.buffer_size:
                    await self.flush()

        except Exception as e:
            logger.error(f"Error logging prediction: {e}", exc_info=True)

    async def flush(self) -> None:
        """Flush buffer to disk."""
        async with self.buffer_lock:
            if not self.buffer:
                return

            try:
                # Rotate if needed
                if self._should_rotate():
                    self.current_file = self._get_current_logfile()

                # Write to file
                with open(self.current_file, "a") as f:
                    for line in self.buffer:
                        f.write(line + "\n")

                logger.debug(f"Flushed {len(self.buffer)} entries to {self.current_file}")
                self.buffer.clear()

            except Exception as e:
                logger.error(f"Error flushing canonicalization logs: {e}", exc_info=True)

    async def _flush_loop(self) -> None:
        """Periodic flush task."""
        try:
            while True:
                await asyncio.sleep(self.flush_interval_sec)
                await self.flush()
        except asyncio.CancelledError:
            pass

    async def cleanup_old_logs(self) -> None:
        """Remove logs older than retention_days."""
        try:
            cutoff_date = datetime.utcnow().date() - timedelta(days=self.retention_days)

            removed_count = 0
            for logfile in self.log_dir.glob("*.jsonl"):
                try:
                    # Parse date from filename (YYYY-MM-DD.jsonl)
                    date_str = logfile.stem
                    log_date = datetime.fromisoformat(date_str).date()

                    if log_date < cutoff_date:
                        os.remove(logfile)
                        logger.info(f"Removed old canonicalization log: {logfile}")
                        removed_count += 1

                except Exception as e:
                    logger.warning(f"Error processing logfile {logfile}: {e}")

            if removed_count > 0:
                logger.info(f"Cleaned up {removed_count} old canonicalization logs")

        except Exception as e:
            logger.error(f"Error cleaning up old logs: {e}", exc_info=True)

    def get_log_stats(self) -> dict:
        """Get current logging statistics."""
        return {
            "buffer_size": len(self.buffer),
            "buffer_max": self.buffer_size,
            "log_dir": str(self.log_dir),
            "retention_days": self.retention_days,
            "logfiles": len(list(self.log_dir.glob("*.jsonl"))),
        }
