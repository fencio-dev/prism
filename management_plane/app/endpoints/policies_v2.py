"""Policy CRUD endpoints for v2 management plane."""

import asyncio
import json
import logging
import os
import time
import uuid
from functools import lru_cache
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import User, get_current_tenant
from app.settings import config
from app.endpoints.enforcement_v2 import get_policy_encoder
from app.models import DesignBoundary, PolicyClearResponse, PolicyDeleteResponse, PolicyListResponse, PolicyWriteRequest
from app.services import DataPlaneClient, DataPlaneError
from app.chroma_client import delete_tenant_collection
from app.services.policies import (
    build_anchor_payload,
    create_policy_record,
    delete_all_policy_records,
    delete_policy_payload,
    delete_policy_record,
    fetch_policy_record,
    list_policy_records,
    update_policy_record,
    upsert_policy_payload,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/policies", tags=["policies-v2"])


def _write_policy_audit(entry: dict) -> None:
    from datetime import date
    log_dir = config.POLICY_AUDIT_LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"{date.today().isoformat()}.jsonl")
    try:
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.error("policy audit log write failed: %s", exc)


@lru_cache(maxsize=1)
def get_data_plane_client() -> DataPlaneClient:
    url = os.getenv("DATA_PLANE_URL", "localhost:50051")
    return DataPlaneClient(url=url, insecure=True)


def _boundary_from_request(
    request: PolicyWriteRequest,
    tenant_id: str,
    created_at: float,
    updated_at: float,
) -> DesignBoundary:
    return DesignBoundary(
        id=request.id,
        name=request.name,
        tenant_id=tenant_id,
        agent_id=request.agent_id,
        status=request.status,
        policy_type=request.policy_type,
        priority=request.priority,
        match=request.match,
        connection_match=request.connection_match,
        deterministic_conditions=request.deterministic_conditions,
        semantic_conditions=request.semantic_conditions,
        thresholds=request.thresholds,
        scoring_mode=request.scoring_mode,
        weights=request.weights,
        drift_threshold=request.drift_threshold,
        modification_spec=request.modification_spec,
        notes=request.notes,
        created_at=created_at,
        updated_at=updated_at,
    )


def _persist_anchor_payload(
    tenant_id: str,
    boundary: DesignBoundary,
) -> "RuleVector":
    from app.services.policy_encoder import RuleVector
    policy_encoder = get_policy_encoder()

    if not policy_encoder:
        raise HTTPException(status_code=500, detail="Service initialization failed")

    try:
        rule_vector = policy_encoder.encode(boundary)
    except Exception as exc:
        logger.error("Policy encoding failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Policy encoding failed") from exc

    payload = {
        "boundary": boundary.model_dump(),
        "anchors": build_anchor_payload(rule_vector),
    }
    metadata = cast(
        dict[str, object],
        {
            "policy_id": boundary.id,
            "boundary_name": boundary.name,
            "status": boundary.status,
            "policy_type": boundary.policy_type,
        },
    )

    try:
        upsert_policy_payload(tenant_id, boundary.id, payload, metadata)
    except Exception as exc:
        logger.error("Failed to persist policy payload: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Policy payload storage failed") from exc

    return rule_vector


def _install_to_dataplane(boundary: DesignBoundary, rule_vector: "RuleVector") -> None:
    client = get_data_plane_client()
    try:
        client.install_policies([boundary], [rule_vector])
        logger.info("Installed policy %s into data plane", boundary.id)
    except DataPlaneError as exc:
        logger.warning(
            "Data plane install failed for policy %s (startup sync is recovery path): %s",
            boundary.id, exc,
        )
    except Exception as exc:
        logger.warning(
            "Unexpected error installing policy %s to data plane: %s",
            boundary.id, exc,
        )


@router.post("", response_model=DesignBoundary, status_code=status.HTTP_201_CREATED)
async def create_policy(
    request: PolicyWriteRequest,
    current_user: User = Depends(get_current_tenant),
) -> DesignBoundary:
    request_id = str(uuid.uuid4())
    now = time.time()
    boundary = _boundary_from_request(request, current_user.id, now, now)

    try:
        create_policy_record(boundary, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        rule_vector = _persist_anchor_payload(current_user.id, boundary)
    except HTTPException:
        delete_policy_record(current_user.id, boundary.id)
        raise

    _install_to_dataplane(boundary, rule_vector)

    _write_policy_audit({
        "ts": now,
        "request_id": request_id,
        "operation": "create_policy",
        "policy_id": boundary.id,
        "tenant_id": current_user.id,
        "result": "ok",
    })
    return boundary


@router.get("", response_model=PolicyListResponse, status_code=status.HTTP_200_OK)
async def list_policies(
    agent_id: str = Query(default=""),
    current_user: User = Depends(get_current_tenant),
) -> PolicyListResponse:
    policies = list_policy_records(current_user.id, agent_id=agent_id)
    return PolicyListResponse(policies=policies)


@router.get("/{policy_id}", response_model=DesignBoundary, status_code=status.HTTP_200_OK)
async def get_policy(
    policy_id: str,
    current_user: User = Depends(get_current_tenant),
) -> DesignBoundary:
    policy = fetch_policy_record(current_user.id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@router.put("/{policy_id}", response_model=DesignBoundary, status_code=status.HTTP_200_OK)
async def update_policy(
    policy_id: str,
    request: PolicyWriteRequest,
    current_user: User = Depends(get_current_tenant),
) -> DesignBoundary:
    request_id = str(uuid.uuid4())
    existing = fetch_policy_record(current_user.id, policy_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Policy not found")

    now = time.time()
    boundary = _boundary_from_request(request, current_user.id, existing.created_at, now)
    if boundary.id != policy_id:
        raise HTTPException(status_code=400, detail="Policy ID mismatch")

    try:
        update_policy_record(boundary, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    rule_vector = _persist_anchor_payload(current_user.id, boundary)
    _install_to_dataplane(boundary, rule_vector)
    _write_policy_audit({
        "ts": now,
        "request_id": request_id,
        "operation": "update_policy",
        "policy_id": boundary.id,
        "tenant_id": current_user.id,
        "result": "ok",
    })
    return boundary


@router.patch("/{policy_id}/toggle", response_model=DesignBoundary, status_code=status.HTTP_200_OK)
async def toggle_policy_status(
    policy_id: str,
    current_user: User = Depends(get_current_tenant),
) -> DesignBoundary:
    existing = fetch_policy_record(current_user.id, policy_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Policy not found")

    now = time.time()
    new_status = "disabled" if existing.status == "active" else "active"
    updated = existing.model_copy(update={"status": new_status, "updated_at": now})

    try:
        update_policy_record(updated, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    rule_vector = _persist_anchor_payload(current_user.id, updated)
    _install_to_dataplane(updated, rule_vector)

    _write_policy_audit({
        "ts": now,
        "request_id": str(uuid.uuid4()),
        "operation": "toggle_policy_status",
        "policy_id": policy_id,
        "tenant_id": current_user.id,
        "result": new_status,
    })
    return updated


@router.delete("/{policy_id}", response_model=PolicyDeleteResponse, status_code=status.HTTP_200_OK)
async def delete_policy(
    policy_id: str,
    current_user: User = Depends(get_current_tenant),
) -> PolicyDeleteResponse:
    request_id = str(uuid.uuid4())
    policy = fetch_policy_record(current_user.id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    client = get_data_plane_client()
    try:
        result = await asyncio.to_thread(
            client.remove_policy,
            policy_id,
            policy.agent_id,
        )
    except DataPlaneError as exc:
        raise HTTPException(status_code=502, detail=f"Data Plane error: {exc}") from exc
    except Exception as exc:
        logger.error("Policy uninstall failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Policy uninstall failed") from exc

    if not result.get("success"):
        message = result.get("message", "Policy uninstall failed")
        raise HTTPException(status_code=502, detail=message)

    removed = delete_policy_record(current_user.id, policy_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Policy not found")

    try:
        delete_policy_payload(current_user.id, policy_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Policy payload deletion failed") from exc

    _write_policy_audit({
        "ts": time.time(),
        "request_id": request_id,
        "operation": "delete_policy",
        "policy_id": policy_id,
        "tenant_id": current_user.id,
        "result": "ok",
    })
    return PolicyDeleteResponse(
        success=True,
        policy_id=policy_id,
        rules_removed=result.get("rules_removed", 0),
        message=result.get("message", ""),
    )


@router.delete("", response_model=PolicyClearResponse, status_code=status.HTTP_200_OK)
async def clear_all_policies(
    current_user: User = Depends(get_current_tenant),
) -> PolicyClearResponse:
    """Remove every policy for the authenticated tenant across all three stores."""
    request_id = str(uuid.uuid4())
    client = get_data_plane_client()

    # 1. Evict all rules from the Data Plane (cold_storage)
    try:
        dp_result = await asyncio.to_thread(client.remove_agent_rules, current_user.id)
        rules_removed = dp_result.get("rules_removed", 0)
    except DataPlaneError as exc:
        raise HTTPException(status_code=502, detail=f"Data Plane error: {exc}") from exc
    except Exception as exc:
        logger.error("RemoveAgentRules failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Data Plane rule removal failed") from exc

    # 2. Wipe policies_v2 SQLite rows
    policies_deleted = delete_all_policy_records(current_user.id)

    # 3. Best-effort: drop the tenant's ChromaDB collection
    try:
        delete_tenant_collection(current_user.id)
    except Exception as exc:
        logger.warning("ChromaDB collection teardown failed (non-fatal): %s", exc)

    _write_policy_audit({
        "ts": time.time(),
        "request_id": request_id,
        "operation": "clear_all_policies",
        "policy_id": "",
        "tenant_id": current_user.id,
        "result": "ok",
    })
    return PolicyClearResponse(
        success=True,
        policies_deleted=policies_deleted,
        rules_removed=rules_removed,
        message=f"Cleared {policies_deleted} policy records and {rules_removed} data-plane rules.",
    )
