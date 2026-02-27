"""
SQLite-backed multi-turn agent session accumulator.

Stores per-agent call history for structured logging and audit purposes.
Initialized at module import time; safe to import anywhere in the application.

Schema (agent_sessions):
  agent_id         TEXT PRIMARY KEY
  action_history   TEXT    -- JSON array of {request_id, action, decision, ts}
  call_count       INTEGER DEFAULT 0
  last_seen_at     REAL    -- Unix timestamp float
  created_at       REAL    -- Unix timestamp float
  initial_vector   BLOB    -- 128 x float32 little-endian bytes (AARM baseline r0)
  cumulative_drift REAL    -- running sum of per-call semantic distances
  last_vector      BLOB    -- most recent intent vector (128 x float32 bytes)
"""

import json
import logging
import os
import sqlite3
import time

import numpy as np

from app.settings import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------

_SESSION_DB_PATH: str = config.SESSION_DB_PATH

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
                    agent_id         TEXT PRIMARY KEY,
                    action_history   TEXT NOT NULL DEFAULT '[]',
                    call_count       INTEGER NOT NULL DEFAULT 0,
                    last_seen_at     REAL NOT NULL,
                    created_at       REAL NOT NULL,
                    initial_vector   BLOB,
                    cumulative_drift REAL NOT NULL DEFAULT 0.0,
                    last_vector      BLOB
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS enforce_calls (
                    call_id            TEXT PRIMARY KEY,
                    agent_id           TEXT NOT NULL,
                    ts_ms              INTEGER NOT NULL,
                    decision           TEXT NOT NULL,
                    op                 TEXT,
                    t                  TEXT,
                    enforcement_result TEXT NOT NULL,
                    intent_event       TEXT,
                    is_dry_run         INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(enforce_calls)").fetchall()
            }
            if "intent_event" not in columns:
                conn.execute("ALTER TABLE enforce_calls ADD COLUMN intent_event TEXT")
            # Idempotent: add is_dry_run column if it doesn't exist
            try:
                conn.execute("ALTER TABLE enforce_calls ADD COLUMN is_dry_run INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass  # column already exists
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


def insert_call(
    call_id: str,
    agent_id: str,
    ts_ms: int,
    decision: str,
    op: str | None,
    t: str | None,
    enforcement_result_json: str,
    intent_event_json: str | None = None,
    is_dry_run: bool = False,
) -> None:
    """
    Insert a row into enforce_calls for every POST /enforce call.

    Uses INSERT OR REPLACE so replayed call_ids are idempotent.
    On any exception: logs a structured error and returns without raising.
    """
    try:
        conn = _get_connection()
        try:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO enforce_calls
                        (call_id, agent_id, ts_ms, decision, op, t, enforcement_result, intent_event, is_dry_run)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        call_id,
                        agent_id,
                        ts_ms,
                        decision,
                        op,
                        t,
                        enforcement_result_json,
                        intent_event_json,
                        1 if is_dry_run else 0,
                    ),
                )
            except sqlite3.OperationalError as exc:
                if "intent_event" not in str(exc):
                    raise
                conn.execute(
                    """
                    INSERT OR REPLACE INTO enforce_calls
                        (call_id, agent_id, ts_ms, decision, op, t, enforcement_result)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (call_id, agent_id, ts_ms, decision, op, t, enforcement_result_json),
                )
            conn.commit()
        finally:
            conn.close()

    except Exception as exc:
        logger.error(
            "session_store: insert_call failed for call_id=%s agent_id=%s: %s",
            call_id,
            agent_id,
            exc,
            exc_info=True,
        )


def update_call_decision(agent_id: str, request_id: str, decision: str) -> None:
    """
    Update the decision field of the action_history entry matching request_id.

    Finds the entry in action_history for agent_id that matches request_id
    and updates its decision field in-place. Never appends a new entry.
    Fail-soft: logs on any error, does not raise.
    """
    if not agent_id:
        return
    try:
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT action_history FROM agent_sessions WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()

            if row is None:
                return

            history = json.loads(row["action_history"])
            for entry in history:
                if entry.get("request_id") == request_id:
                    entry["decision"] = decision
                    break

            conn.execute(
                "UPDATE agent_sessions SET action_history = ? WHERE agent_id = ?",
                (json.dumps(history), agent_id),
            )
            conn.commit()
        finally:
            conn.close()

    except Exception as exc:
        logger.error(
            "session_store: update_call_decision failed for agent_id=%s request_id=%s: %s",
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
# AARM vector methods
# ---------------------------------------------------------------------------


def initialize_session_vector(agent_id: str, vector: list[float]) -> None:
    """
    Set initial_vector for the session if and only if it is currently NULL.

    Uses a conditional UPDATE so the first caller wins and sets the AARM
    baseline r0; any subsequent call for the same agent is a silent no-op.
    On any exception: logs a structured error and returns without raising.
    """
    try:
        blob = np.array(vector, dtype=np.float32).tobytes()

        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE agent_sessions SET initial_vector = ? WHERE agent_id = ? AND initial_vector IS NULL",
                (blob, agent_id),
            )
            conn.commit()
        finally:
            conn.close()

    except Exception as exc:
        logger.error(
            "session_store: initialize_session_vector failed for agent_id=%s: %s",
            agent_id,
            exc,
            exc_info=True,
        )


def compute_and_update_drift(agent_id: str, current_vector: list[float]) -> float:
    """
    Compute the per-call drift score against the AARM baseline, update the
    session, and return the per-call drift value.

    Steps:
    1. Read initial_vector from the session row.
    2. If initial_vector is NULL (legacy session or first-call race loser):
       return 0.0 without touching cumulative_drift.
    3. Compute drift = 1 - dot(initial_vector, current_vector).
       Vectors are per-slot L2-normalized so dot == cosine similarity.
    4. Add drift to cumulative_drift, store current_vector as last_vector,
       and update last_seen_at.
    5. Return the per-call drift (not the cumulative total).

    On any exception: logs a structured error and returns 0.0.
    """
    try:
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT initial_vector FROM agent_sessions WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()

            if row is None or row["initial_vector"] is None:
                return 0.0

            iv = np.frombuffer(row["initial_vector"], dtype=np.float32)
            cv = np.array(current_vector, dtype=np.float32)
            drift = float(1.0 - np.dot(iv, cv))
            drift = max(0.0, drift)

            last_blob = cv.tobytes()
            now = time.time()

            conn.execute(
                """
                UPDATE agent_sessions
                SET cumulative_drift = cumulative_drift + ?,
                    last_vector      = ?,
                    last_seen_at     = ?
                WHERE agent_id = ?
                """,
                (drift, last_blob, now, agent_id),
            )
            conn.commit()
        finally:
            conn.close()

        return drift

    except Exception as exc:
        logger.error(
            "session_store: compute_and_update_drift failed for agent_id=%s: %s",
            agent_id,
            exc,
            exc_info=True,
        )
        return 0.0


def get_session_drift(agent_id: str) -> float:
    """
    Return cumulative_drift for the agent, or 0.0 if no session exists.
    On any exception: logs a structured error and returns 0.0.
    """
    try:
        conn = _get_connection()
        try:
            row = conn.execute(
                "SELECT cumulative_drift FROM agent_sessions WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return 0.0
        return float(row["cumulative_drift"])

    except Exception as exc:
        logger.error(
            "session_store: get_session_drift failed for agent_id=%s: %s",
            agent_id,
            exc,
            exc_info=True,
        )
        return 0.0


def list_sessions(
    limit: int = 50,
    offset: int = 0,
    agent_id: str | None = None,
    decision: str | None = None,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
) -> dict:
    """
    Return a paginated list of sessions with optional filters.

    Filters:
      - agent_id: exact match on agent_id
      - decision: match on the last action_history entry's "decision" field
      - start_time_ms / end_time_ms: filter on last_seen_at (stored as seconds)

    Returns a dict with keys: sessions, total_count, limit, offset.
    """
    try:
        conditions = []
        params: list = []

        if agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(agent_id)

        if start_time_ms is not None:
            conditions.append("last_seen_at >= ?")
            params.append(start_time_ms / 1000.0)

        if end_time_ms is not None:
            conditions.append("last_seen_at <= ?")
            params.append(end_time_ms / 1000.0)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        conn = _get_connection()
        try:
            rows = conn.execute(
                f"SELECT * FROM agent_sessions {where_clause} ORDER BY last_seen_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

            total_row = conn.execute(
                f"SELECT COUNT(*) FROM agent_sessions {where_clause}",
                params,
            ).fetchone()
        finally:
            conn.close()

        total_count = total_row[0] if total_row else 0

        sessions = []
        for row in rows:
            history = json.loads(row["action_history"])
            final_decision = history[-1]["decision"] if history else None

            if decision is not None and final_decision != decision:
                continue

            sessions.append(
                {
                    "session_id": row["agent_id"],
                    "agent_id": row["agent_id"],
                    "call_count": row["call_count"],
                    "created_at_ms": int(row["created_at"] * 1000),
                    "last_seen_at_ms": int(row["last_seen_at"] * 1000),
                    "final_decision": final_decision,
                    "cumulative_drift": float(row["cumulative_drift"]),
                }
            )

        # If decision filter was applied post-query, total_count needs recomputing
        if decision is not None:
            total_count = len(sessions)
            sessions = sessions[offset : offset + limit]

        return {
            "sessions": sessions,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
        }

    except Exception as exc:
        logger.error("session_store: list_sessions failed: %s", exc, exc_info=True)
        return {"sessions": [], "total_count": 0, "limit": limit, "offset": offset}


def list_calls(
    limit: int = 50,
    offset: int = 0,
    agent_id: str | None = None,
    decision: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    is_dry_run: bool | None = None,
) -> tuple[list[dict], int]:
    """
    Return a paginated list of enforce_calls rows with optional filters.

    Filters:
      - agent_id: exact match on agent_id
      - decision: exact match on decision
      - start_ms / end_ms: filter on ts_ms (stored as integer milliseconds)

    Returns (rows, total_count). Rows contain: call_id, agent_id, ts_ms,
    decision, op, t — enforcement_result is excluded from the list view.
    On any exception: logs a structured error and returns ([], 0).
    """
    try:
        conditions = []
        params: list = []

        if agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(agent_id)

        if decision is not None:
            conditions.append("decision = ?")
            params.append(decision)

        if start_ms is not None:
            conditions.append("ts_ms >= ?")
            params.append(start_ms)

        if end_ms is not None:
            conditions.append("ts_ms <= ?")
            params.append(end_ms)

        if is_dry_run is not None:
            conditions.append("is_dry_run = ?")
            params.append(1 if is_dry_run else 0)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        conn = _get_connection()
        try:
            rows = conn.execute(
                f"SELECT call_id, agent_id, ts_ms, decision, op, t, is_dry_run FROM enforce_calls {where_clause} ORDER BY ts_ms DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

            total_row = conn.execute(
                f"SELECT COUNT(*) FROM enforce_calls {where_clause}",
                params,
            ).fetchone()
        finally:
            conn.close()

        total_count = total_row[0] if total_row else 0
        return [dict(row) for row in rows], total_count

    except Exception as exc:
        logger.error("session_store: list_calls failed: %s", exc, exc_info=True)
        return [], 0


def get_call(call_id: str) -> dict | None:
    """
    Return the full enforce_calls row for call_id, or None if not found.
    enforcement_result is returned as a raw JSON string; callers deserialize it.
    On any exception: logs a structured error and returns None.
    """
    try:
        conn = _get_connection()
        try:
            try:
                row = conn.execute(
                    """
                    SELECT call_id, agent_id, ts_ms, decision, op, t, enforcement_result, intent_event
                    FROM enforce_calls
                    WHERE call_id = ?
                    """,
                    (call_id,),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if "intent_event" not in str(exc):
                    raise
                row = conn.execute(
                    """
                    SELECT call_id, agent_id, ts_ms, decision, op, t, enforcement_result
                    FROM enforce_calls
                    WHERE call_id = ?
                    """,
                    (call_id,),
                ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        result = dict(row)
        result.setdefault("intent_event", None)
        return result

    except Exception as exc:
        logger.error(
            "session_store: get_call failed for call_id=%s: %s",
            call_id,
            exc,
            exc_info=True,
        )
        return None


def delete_calls() -> int:
    """
    Delete all rows from enforce_calls.
    Returns the number of deleted rows. Returns 0 on any exception.
    """
    try:
        conn = _get_connection()
        try:
            cursor = conn.execute("DELETE FROM enforce_calls")
            deleted = cursor.rowcount
            conn.commit()
        finally:
            conn.close()
        return deleted

    except Exception as exc:
        logger.error("session_store: delete_calls failed: %s", exc, exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Module-level initialization
# ---------------------------------------------------------------------------

_init_db()
