"""Policy storage and anchor payload persistence."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

from app.chroma_client import get_rules_collection, upsert_rule_payload
from app.models import DesignBoundary, PolicyMatch, SliceThresholds, SliceWeights
from app.services.policy_encoder import RuleVector
from app.settings import config

logger = logging.getLogger(__name__)


def _sqlite_path() -> str:
    url = config.DATABASE_URL
    if url.startswith("sqlite:///"):
        raw_path = url[len("sqlite:///"):]
        if raw_path.startswith("/"):
            return raw_path
        return os.path.abspath(raw_path)
    raise ValueError("Only sqlite DATABASE_URLs are supported")


@contextmanager
def _get_connection() -> Iterator[sqlite3.Connection]:
    path = _sqlite_path()
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS policies_v2 (
            tenant_id TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            agent_id TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            policy_type TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            match_json TEXT NOT NULL,
            thresholds_json TEXT NOT NULL,
            scoring_mode TEXT NOT NULL CHECK (scoring_mode IN ('min', 'weighted-avg')),
            weights_json TEXT,
            drift_threshold REAL,
            modification_spec_json TEXT,
            notes TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (tenant_id, policy_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_policies_v2_tenant ON policies_v2(tenant_id)"
    )

    columns = {row[1] for row in conn.execute("PRAGMA table_info(policies_v2)").fetchall()}
    if "scoring_mode" not in columns:
        conn.execute(
            "ALTER TABLE policies_v2 ADD COLUMN scoring_mode TEXT NOT NULL DEFAULT 'weighted-avg'"
        )
    if "agent_id" not in columns:
        conn.execute(
            "ALTER TABLE policies_v2 ADD COLUMN agent_id TEXT NOT NULL DEFAULT ''"
        )

    # Network policies table (for deterministic API endpoint whitelisting)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS network_policies (
            tenant_id TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
            mode TEXT NOT NULL CHECK (mode IN ('Monitor', 'Enforce')),
            whitelist_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (tenant_id, policy_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_network_policies_tenant_agent ON network_policies(tenant_id, agent_id)"
    )

    conn.commit()


def _row_to_boundary(row: sqlite3.Row) -> DesignBoundary:
    match = PolicyMatch.model_validate(json.loads(row["match_json"]))
    thresholds = SliceThresholds.model_validate(json.loads(row["thresholds_json"]))
    weights = SliceWeights.model_validate(json.loads(row["weights_json"])) if row["weights_json"] else None
    modification_spec = json.loads(row["modification_spec_json"]) if row["modification_spec_json"] else None
    return DesignBoundary(
        id=row["policy_id"],
        name=row["name"],
        tenant_id=row["tenant_id"],
        agent_id=row["agent_id"] if row["agent_id"] else "",
        status=row["status"],
        policy_type=row["policy_type"],
        priority=row["priority"],
        match=match,
        thresholds=thresholds,
        scoring_mode=row["scoring_mode"],
        weights=weights,
        drift_threshold=row["drift_threshold"],
        modification_spec=modification_spec,
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def fetch_policy_record(tenant_id: str, policy_id: str) -> Optional[DesignBoundary]:
    with _get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM policies_v2
            WHERE tenant_id = ? AND policy_id = ?
            """,
            (tenant_id, policy_id),
        ).fetchone()
    return _row_to_boundary(row) if row else None


def list_policy_records(tenant_id: str, agent_id: str = "") -> list[DesignBoundary]:
    with _get_connection() as conn:
        if agent_id:
            rows = conn.execute(
                """
                SELECT * FROM policies_v2
                WHERE tenant_id = ? AND agent_id = ?
                ORDER BY updated_at DESC
                """,
                (tenant_id, agent_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM policies_v2
                WHERE tenant_id = ?
                ORDER BY updated_at DESC
                """,
                (tenant_id,),
            ).fetchall()
    return [_row_to_boundary(row) for row in rows]


def create_policy_record(boundary: DesignBoundary, tenant_id: str) -> DesignBoundary:
    with _get_connection() as conn:
        existing = conn.execute(
            """
            SELECT 1 FROM policies_v2
            WHERE tenant_id = ? AND policy_id = ?
            """,
            (tenant_id, boundary.id),
        ).fetchone()
        if existing:
            raise ValueError("Policy already exists")

        conn.execute(
            """
            INSERT INTO policies_v2 (
                tenant_id,
                policy_id,
                agent_id,
                name,
                status,
                policy_type,
                priority,
                match_json,
                thresholds_json,
                scoring_mode,
                weights_json,
                drift_threshold,
                modification_spec_json,
                notes,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                boundary.id,
                boundary.agent_id,
                boundary.name,
                boundary.status,
                boundary.policy_type,
                boundary.priority,
                json.dumps(boundary.match.model_dump(), separators=(",", ":")),
                json.dumps(boundary.thresholds.model_dump(), separators=(",", ":")),
                boundary.scoring_mode,
                json.dumps(boundary.weights.model_dump(), separators=(",", ":")) if boundary.weights else None,
                boundary.drift_threshold,
                json.dumps(boundary.modification_spec, separators=(",", ":")) if boundary.modification_spec else None,
                boundary.notes,
                boundary.created_at,
                boundary.updated_at,
            ),
        )
        conn.commit()
    return boundary


def update_policy_record(boundary: DesignBoundary, tenant_id: str) -> DesignBoundary:
    with _get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM policies_v2
            WHERE tenant_id = ? AND policy_id = ?
            """,
            (tenant_id, boundary.id),
        ).fetchone()
        if not row:
            raise ValueError("Policy not found")

        conn.execute(
            """
            UPDATE policies_v2
            SET name = ?,
                status = ?,
                policy_type = ?,
                priority = ?,
                match_json = ?,
                thresholds_json = ?,
                scoring_mode = ?,
                weights_json = ?,
                drift_threshold = ?,
                modification_spec_json = ?,
                notes = ?,
                updated_at = ?
            WHERE tenant_id = ? AND policy_id = ?
            """,
            (
                boundary.name,
                boundary.status,
                boundary.policy_type,
                boundary.priority,
                json.dumps(boundary.match.model_dump(), separators=(",", ":")),
                json.dumps(boundary.thresholds.model_dump(), separators=(",", ":")),
                boundary.scoring_mode,
                json.dumps(boundary.weights.model_dump(), separators=(",", ":")) if boundary.weights else None,
                boundary.drift_threshold,
                json.dumps(boundary.modification_spec, separators=(",", ":")) if boundary.modification_spec else None,
                boundary.notes,
                boundary.updated_at,
                tenant_id,
                boundary.id,
            ),
        )
        conn.commit()
    return boundary


def delete_policy_record(tenant_id: str, policy_id: str) -> bool:
    with _get_connection() as conn:
        result = conn.execute(
            """
            DELETE FROM policies_v2
            WHERE tenant_id = ? AND policy_id = ?
            """,
            (tenant_id, policy_id),
        )
        conn.commit()
        return result.rowcount > 0


def delete_all_policy_records(tenant_id: str) -> int:
    """Delete every policy row for *tenant_id*.  Returns the number of rows deleted."""
    with _get_connection() as conn:
        result = conn.execute(
            "DELETE FROM policies_v2 WHERE tenant_id = ?",
            (tenant_id,),
        )
        conn.commit()
        return result.rowcount


def build_anchor_payload(rule_vector: RuleVector) -> dict[str, object]:
    return {
        "action_anchors": rule_vector.layers["action"].tolist(),
        "action_count": rule_vector.anchor_counts["action"],
        "resource_anchors": rule_vector.layers["resource"].tolist(),
        "resource_count": rule_vector.anchor_counts["resource"],
        "data_anchors": rule_vector.layers["data"].tolist(),
        "data_count": rule_vector.anchor_counts["data"],
        "risk_anchors": rule_vector.layers["risk"].tolist(),
        "risk_count": rule_vector.anchor_counts["risk"],
    }


def upsert_policy_payload(
    tenant_id: str,
    policy_id: str,
    payload: dict[str, object],
    metadata: Optional[dict[str, object]] = None,
) -> None:
    upsert_rule_payload(tenant_id, policy_id, payload, metadata)


def delete_policy_payload(tenant_id: str, policy_id: str) -> None:
    collection = get_rules_collection(tenant_id)
    try:
        collection.delete(ids=[policy_id])
    except Exception as exc:
        logger.warning("Failed to delete policy payload %s: %s", policy_id, exc)
        raise
