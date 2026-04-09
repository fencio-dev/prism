"""Network policy storage service via db_infra."""

from __future__ import annotations

import json
import logging
from typing import Optional

from app.models import NetworkEndpointRule, NetworkPolicy
from app.services.db_infra_client import db_infra_client

logger = logging.getLogger(__name__)


def _row_to_network_policy(row: dict) -> NetworkPolicy:
    whitelist = [
        NetworkEndpointRule(**rule)
        for rule in json.loads(row["whitelist_json"])
    ]
    return NetworkPolicy(
        policy_id=row["policy_id"],
        tenant_id=row["tenant_id"],
        agent_id=row["agent_id"],
        name=row["name"],
        status=row["status"],
        mode=row["mode"],
        whitelist=whitelist,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _payload(policy: NetworkPolicy) -> dict:
    return {
        "tenant_id": policy.tenant_id,
        "policy_id": policy.policy_id,
        "agent_id": policy.agent_id,
        "name": policy.name,
        "status": policy.status,
        "mode": policy.mode,
        "whitelist_json": json.dumps(
            [rule.model_dump() for rule in policy.whitelist],
            separators=(",", ":"),
        ),
        "created_at": policy.created_at,
        "updated_at": policy.updated_at,
    }


def create_network_policy(policy: NetworkPolicy) -> NetworkPolicy:
    db_infra_client._request_json(
        "POST",
        "/api/v1/prism-management/network-policies",
        payload=_payload(policy),
    )
    return policy


def get_network_policy(tenant_id: str, policy_id: str) -> Optional[NetworkPolicy]:
    row = db_infra_client._request_json(
        "GET",
        f"/api/v1/prism-management/network-policies/{tenant_id}/{policy_id}",
        allow_not_found=True,
    )
    return _row_to_network_policy(row) if row else None


def list_network_policies(
    tenant_id: str,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
) -> list[NetworkPolicy]:
    params = {"tenant_id": tenant_id}
    if agent_id:
        params["agent_id"] = agent_id
    if status:
        params["status"] = status
    response = db_infra_client._request_json(
        "GET",
        "/api/v1/prism-management/network-policies",
        params=params,
    )
    return [_row_to_network_policy(row) for row in response.get("policies", [])]


def update_network_policy(policy: NetworkPolicy) -> NetworkPolicy:
    db_infra_client._request_json(
        "POST",
        "/api/v1/prism-management/network-policies",
        payload=_payload(policy),
    )
    return policy


def delete_network_policy(tenant_id: str, policy_id: str) -> bool:
    response = db_infra_client._request_json(
        "DELETE",
        f"/api/v1/prism-management/network-policies/{tenant_id}/{policy_id}",
        allow_not_found=True,
    )
    return bool(response.get("deleted"))
