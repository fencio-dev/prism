"""Policy storage and anchor payload persistence via db_infra."""

from __future__ import annotations

import json
import logging
from typing import Optional

from app.chroma_client import get_rules_collection, upsert_rule_payload
from app.models import DesignBoundary
from app.services.db_infra_client import db_infra_client
from app.services.policy_encoder import RuleVector

logger = logging.getLogger(__name__)


def _row_to_boundary(row: dict) -> DesignBoundary:
    return DesignBoundary(
        id=row["policy_id"],
        name=row["name"],
        tenant_id=row["tenant_id"],
        agent_id=row.get("agent_id") or "",
        status=row["status"],
        policy_type=row["policy_type"],
        priority=row["priority"],
        match=json.loads(row["match_json"]),
        connection_match=(
            json.loads(row["connection_match_json"])
            if row.get("connection_match_json")
            else None
        ),
        deterministic_conditions=json.loads(
            row.get("deterministic_conditions_json") or "[]"
        ),
        semantic_conditions=json.loads(row.get("semantic_conditions_json") or "[]"),
        thresholds=json.loads(row["thresholds_json"]),
        scoring_mode=row["scoring_mode"],
        weights=json.loads(row["weights_json"]) if row.get("weights_json") else None,
        drift_threshold=row.get("drift_threshold"),
        modification_spec=(
            json.loads(row["modification_spec_json"])
            if row.get("modification_spec_json")
            else None
        ),
        notes=row.get("notes"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _boundary_payload(boundary: DesignBoundary) -> dict:
    return {
        "tenant_id": boundary.tenant_id,
        "policy_id": boundary.id,
        "agent_id": boundary.agent_id or "",
        "name": boundary.name,
        "status": boundary.status,
        "policy_type": boundary.policy_type,
        "priority": boundary.priority,
        "match_json": json.dumps(boundary.match.model_dump(), separators=(",", ":")),
        "connection_match_json": (
            json.dumps(boundary.connection_match.model_dump(), separators=(",", ":"))
            if boundary.connection_match
            else None
        ),
        "deterministic_conditions_json": json.dumps(
            [item.model_dump() for item in boundary.deterministic_conditions],
            separators=(",", ":"),
        ),
        "semantic_conditions_json": json.dumps(
            [item.model_dump() for item in boundary.semantic_conditions],
            separators=(",", ":"),
        ),
        "thresholds_json": json.dumps(
            boundary.thresholds.model_dump(),
            separators=(",", ":"),
        ),
        "scoring_mode": boundary.scoring_mode,
        "weights_json": (
            json.dumps(boundary.weights.model_dump(), separators=(",", ":"))
            if boundary.weights
            else None
        ),
        "drift_threshold": boundary.drift_threshold,
        "modification_spec_json": (
            json.dumps(boundary.modification_spec, separators=(",", ":"))
            if boundary.modification_spec
            else None
        ),
        "notes": boundary.notes,
        "created_at": boundary.created_at,
        "updated_at": boundary.updated_at,
    }


def fetch_policy_record(tenant_id: str, policy_id: str) -> Optional[DesignBoundary]:
    row = db_infra_client._request_json(
        "GET",
        f"/api/v1/prism-management/policies/{tenant_id}/{policy_id}",
        allow_not_found=True,
    )
    return _row_to_boundary(row) if row else None


def list_policy_records(
    tenant_id: str | None,
    agent_id: str = "",
    status: str | None = None,
) -> list[DesignBoundary]:
    params = {}
    if tenant_id is not None:
        params["tenant_id"] = tenant_id
    if agent_id:
        params["agent_id"] = agent_id
    if status is not None:
        params["status"] = status
    response = db_infra_client._request_json(
        "GET",
        "/api/v1/prism-management/policies",
        params=params or None,
    )
    return [_row_to_boundary(row) for row in response.get("policies", [])]


def create_policy_record(boundary: DesignBoundary, tenant_id: str) -> DesignBoundary:
    existing = fetch_policy_record(tenant_id, boundary.id)
    if existing:
        raise ValueError("Policy already exists")
    db_infra_client._request_json(
        "POST",
        "/api/v1/prism-management/policies",
        payload=_boundary_payload(boundary),
    )
    return boundary


def update_policy_record(boundary: DesignBoundary, tenant_id: str) -> DesignBoundary:
    existing = fetch_policy_record(tenant_id, boundary.id)
    if not existing:
        raise ValueError("Policy not found")
    db_infra_client._request_json(
        "POST",
        "/api/v1/prism-management/policies",
        payload=_boundary_payload(boundary),
    )
    return boundary


def delete_policy_record(tenant_id: str, policy_id: str) -> bool:
    response = db_infra_client._request_json(
        "DELETE",
        f"/api/v1/prism-management/policies/{tenant_id}/{policy_id}",
        allow_not_found=True,
    )
    return bool(response.get("deleted"))


def delete_all_policy_records(tenant_id: str) -> int:
    response = db_infra_client._request_json(
        "DELETE",
        f"/api/v1/prism-management/policies/{tenant_id}",
    )
    return int(response.get("deleted_count", 0))


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
