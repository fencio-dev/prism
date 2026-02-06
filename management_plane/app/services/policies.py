"""Policy storage and anchor payload persistence."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

from app.chroma_client import get_rules_collection, upsert_rule_payload
from app.models import (
    BoundaryRules,
    BoundaryScope,
    LooseBoundaryConstraints,
    LooseDesignBoundary,
)
from app.services.policy_encoder import RuleVector
from app.settings import config

logger = logging.getLogger(__name__)


def _sqlite_path() -> str:
    url = config.DATABASE_URL
    if url.startswith("sqlite:///"):
        raw_path = url[len("sqlite:///") :]
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
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            policy_type TEXT NOT NULL,
            boundary_schema_version TEXT NOT NULL,
            layer TEXT,
            scope_json TEXT NOT NULL,
            rules_json TEXT NOT NULL,
            constraints_json TEXT NOT NULL,
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
    conn.commit()


def _row_to_boundary(row: sqlite3.Row) -> LooseDesignBoundary:
    scope = BoundaryScope.model_validate(json.loads(row["scope_json"]))
    rules = BoundaryRules.model_validate(json.loads(row["rules_json"]))
    constraints = LooseBoundaryConstraints.model_validate(
        json.loads(row["constraints_json"])
    )
    return LooseDesignBoundary(
        id=row["policy_id"],
        name=row["name"],
        status=row["status"],
        type=row["policy_type"],
        boundarySchemaVersion=row["boundary_schema_version"],
        scope=scope,
        layer=row["layer"],
        rules=rules,
        constraints=constraints,
        notes=row["notes"],
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    )


def fetch_policy_record(tenant_id: str, policy_id: str) -> Optional[LooseDesignBoundary]:
    with _get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM policies_v2
            WHERE tenant_id = ? AND policy_id = ?
            """,
            (tenant_id, policy_id),
        ).fetchone()
    return _row_to_boundary(row) if row else None


def list_policy_records(tenant_id: str) -> list[LooseDesignBoundary]:
    with _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM policies_v2
            WHERE tenant_id = ?
            ORDER BY updated_at DESC
            """,
            (tenant_id,),
        ).fetchall()
    return [_row_to_boundary(row) for row in rows]


def create_policy_record(boundary: LooseDesignBoundary, tenant_id: str) -> LooseDesignBoundary:
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
                name,
                status,
                policy_type,
                boundary_schema_version,
                layer,
                scope_json,
                rules_json,
                constraints_json,
                notes,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                boundary.id,
                boundary.name,
                boundary.status,
                boundary.type,
                boundary.boundarySchemaVersion,
                boundary.layer,
                json.dumps(boundary.scope.model_dump(), separators=(",", ":")),
                json.dumps(boundary.rules.model_dump(), separators=(",", ":")),
                json.dumps(boundary.constraints.model_dump(), separators=(",", ":")),
                boundary.notes,
                boundary.createdAt,
                boundary.updatedAt,
            ),
        )
        conn.commit()
    return boundary


def update_policy_record(boundary: LooseDesignBoundary, tenant_id: str) -> LooseDesignBoundary:
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
                boundary_schema_version = ?,
                layer = ?,
                scope_json = ?,
                rules_json = ?,
                constraints_json = ?,
                notes = ?,
                updated_at = ?
            WHERE tenant_id = ? AND policy_id = ?
            """,
            (
                boundary.name,
                boundary.status,
                boundary.type,
                boundary.boundarySchemaVersion,
                boundary.layer,
                json.dumps(boundary.scope.model_dump(), separators=(",", ":")),
                json.dumps(boundary.rules.model_dump(), separators=(",", ":")),
                json.dumps(boundary.constraints.model_dump(), separators=(",", ":")),
                boundary.notes,
                boundary.updatedAt,
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
