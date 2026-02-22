"""
SQLite-backed multi-turn agent session accumulator.

Stores per-agent call history for structured logging and audit purposes.
Initialized at module import time; safe to import anywhere in the application.

Schema (agent_sessions):
  agent_id       TEXT PRIMARY KEY
  action_history TEXT  -- JSON array of {request_id, action, decision, ts}
  call_count     INTEGER DEFAULT 0
  last_seen_at   REAL   -- Unix timestamp float
  created_at     REAL   -- Unix timestamp float
"""

import json
import logging
import os
import sqlite3
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------

_SESSION_DB_PATH: str = os.getenv("SESSION_DB_PATH", "/var/lib/guard/sessions.db")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_connection() -> sqlite3.Connection:
    """Open a new connection to the session DB with WAL mode enabled."""
    conn = sqlite3.connect(_SESSION_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    """
    Create the sessions table if it does not exist.
    Called once at module import time.
    Ensures the parent directory exists before touching the file.
    """
    db_dir = os.path.dirname(_SESSION_DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    try:
        conn = _get_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    agent_id       TEXT PRIMARY KEY,
                    action_history TEXT    NOT NULL DEFAULT '[]',
                    call_count     INTEGER NOT NULL DEFAULT 0,
                    last_seen_at   REAL    NOT NULL,
                    created_at     REAL    NOT NULL
                )
                """
            )
            conn.commit()
            logger.info("session_store: DB initialized at %s", _SESSION_DB_PATH)
        finally:
            conn.close()
    except Exception as exc:
        logger.error(
            "session_store: failed to initialize DB at %s: %s",
            _SESSION_DB_PATH,
            exc,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_call(agent_id: str, request_id: str, action: str, decision: str) -> None:
    """
    Upsert the session row for agent_id, appending this call to action_history.

    On any exception: logs a structured error and returns without raising.
    """
    try:
        now = time.time()
        new_entry = {
            "request_id": request_id,
            "action": action,
            "decision": decision,
            "ts": now,
        }

        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT action_history, call_count FROM agent_sessions WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()

            if row is None:
                history = [new_entry]
                conn.execute(
                    """
                    INSERT INTO agent_sessions
                        (agent_id, action_history, call_count, last_seen_at, created_at)
                    VALUES (?, ?, 1, ?, ?)
                    """,
                    (agent_id, json.dumps(history), now, now),
                )
            else:
                history = json.loads(row["action_history"])
                history.append(new_entry)
                conn.execute(
                    """
                    UPDATE agent_sessions
                    SET action_history = ?,
                        call_count     = ?,
                        last_seen_at   = ?
                    WHERE agent_id = ?
                    """,
                    (json.dumps(history), row["call_count"] + 1, now, agent_id),
                )

            conn.commit()
        finally:
            conn.close()

    except Exception as exc:
        logger.error(
            "session_store: write_call failed for agent_id=%s request_id=%s: %s",
            agent_id,
            request_id,
            exc,
            exc_info=True,
        )


def get_session(agent_id: str) -> dict | None:
    """
    Return the full session row for agent_id as a dict, or None if not found.
    action_history is returned as a Python list (parsed from JSON).
    """
    try:
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM agent_sessions WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()

            if row is None:
                return None

            result = dict(row)
            result["action_history"] = json.loads(result["action_history"])
            return result
        finally:
            conn.close()

    except Exception as exc:
        logger.error(
            "session_store: get_session failed for agent_id=%s: %s",
            agent_id,
            exc,
            exc_info=True,
        )
        return None


def cleanup_expired() -> int:
    """
    Delete sessions stale by idle timeout (30 min) or absolute max age (24 hours).
    Returns the number of rows deleted. Returns 0 on any exception.
    """
    try:
        now = time.time()
        stale_cutoff = now - 1800   # 30 minutes
        old_cutoff = now - 86400    # 24 hours

        conn = _get_connection()
        try:
            cursor = conn.execute(
                """
                DELETE FROM agent_sessions
                WHERE last_seen_at < ?
                   OR created_at   < ?
                """,
                (stale_cutoff, old_cutoff),
            )
            deleted = cursor.rowcount
            conn.commit()
        finally:
            conn.close()

        if deleted > 0:
            logger.info("session_store: cleanup_expired removed %d row(s)", deleted)

        return deleted

    except Exception as exc:
        logger.error(
            "session_store: cleanup_expired failed: %s",
            exc,
            exc_info=True,
        )
        return 0


# ---------------------------------------------------------------------------
# Module-level initialization
# ---------------------------------------------------------------------------

_init_db()
