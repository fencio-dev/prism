"""Policy CRUD endpoints for v2 management plane."""

import asyncio
import logging
import os
import time
from functools import lru_cache
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import User, get_current_tenant
from app.endpoints.enforcement_v2 import get_canonicalizer, get_policy_encoder
from app.models import LooseDesignBoundary, PolicyClearResponse, PolicyDeleteResponse, PolicyListResponse, PolicyWriteRequest
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


@lru_cache(maxsize=1)
def get_data_plane_client() -> DataPlaneClient:
    url = os.getenv("DATA_PLANE_URL", "localhost:50051")
    insecure = "localhost" in url or "127.0.0.1" in url
    return DataPlaneClient(url=url, insecure=insecure)


def _boundary_from_request(
    request: PolicyWriteRequest,
    tenant_id: str,
    created_at: float,
    updated_at: float,
) -> LooseDesignBoundary:
    scope = request.scope.model_copy(update={"tenantId": tenant_id})
    return LooseDesignBoundary(
        id=request.id,
        name=request.name,
        status=request.status,
        type=request.type,
        boundarySchemaVersion=request.boundarySchemaVersion,
        scope=scope,
        layer=request.layer,
        rules=request.rules,
        constraints=request.constraints,
        notes=request.notes,
        createdAt=created_at,
        updatedAt=updated_at,
    )


def _persist_anchor_payload(
    tenant_id: str,
    boundary: LooseDesignBoundary,
) -> None:
    canonicalizer = get_canonicalizer()
    policy_encoder = get_policy_encoder()

    if not canonicalizer or not policy_encoder:
        raise HTTPException(status_code=500, detail="Service initialization failed")

    try:
        canonicalized = canonicalizer.canonicalize_boundary(boundary)
        canonical_boundary = canonicalized.canonical_boundary
    except Exception as exc:
        logger.error("Boundary canonicalization failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Canonicalization failed") from exc

    try:
        rule_vector = policy_encoder.encode(canonical_boundary)
    except Exception as exc:
        logger.error("Policy encoding failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Policy encoding failed") from exc

    payload = {
        "boundary": canonical_boundary.model_dump(),
        "anchors": build_anchor_payload(rule_vector),
    }
    metadata = cast(
        dict[str, object],
        {
            "policy_id": boundary.id,
            "boundary_name": boundary.name,
            "status": boundary.status,
            "policy_type": boundary.type,
            "layer": boundary.layer or "",
        },
    )

    try:
        upsert_policy_payload(tenant_id, boundary.id, payload, metadata)
    except Exception as exc:
        logger.error("Failed to persist policy payload: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Policy payload storage failed") from exc


@router.post("", response_model=LooseDesignBoundary, status_code=status.HTTP_201_CREATED)
async def create_policy(
    request: PolicyWriteRequest,
    current_user: User = Depends(get_current_tenant),
) -> LooseDesignBoundary:
    now = time.time()
    boundary = _boundary_from_request(request, current_user.id, now, now)

    try:
        create_policy_record(boundary, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        _persist_anchor_payload(current_user.id, boundary)
    except HTTPException:
        delete_policy_record(current_user.id, boundary.id)
        raise

    return boundary


@router.get("", response_model=PolicyListResponse, status_code=status.HTTP_200_OK)
async def list_policies(
    current_user: User = Depends(get_current_tenant),
) -> PolicyListResponse:
    policies = list_policy_records(current_user.id)
    return PolicyListResponse(policies=policies)


@router.get("/{policy_id}", response_model=LooseDesignBoundary, status_code=status.HTTP_200_OK)
async def get_policy(
    policy_id: str,
    current_user: User = Depends(get_current_tenant),
) -> LooseDesignBoundary:
    policy = fetch_policy_record(current_user.id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


@router.put("/{policy_id}", response_model=LooseDesignBoundary, status_code=status.HTTP_200_OK)
async def update_policy(
    policy_id: str,
    request: PolicyWriteRequest,
    current_user: User = Depends(get_current_tenant),
) -> LooseDesignBoundary:
    existing = fetch_policy_record(current_user.id, policy_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Policy not found")

    now = time.time()
    boundary = _boundary_from_request(request, current_user.id, existing.createdAt, now)
    if boundary.id != policy_id:
        raise HTTPException(status_code=400, detail="Policy ID mismatch")

    try:
        update_policy_record(boundary, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _persist_anchor_payload(current_user.id, boundary)
    return boundary


@router.delete("/{policy_id}", response_model=PolicyDeleteResponse, status_code=status.HTTP_200_OK)
async def delete_policy(
    policy_id: str,
    current_user: User = Depends(get_current_tenant),
) -> PolicyDeleteResponse:
    policy = fetch_policy_record(current_user.id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    client = get_data_plane_client()
    try:
        result = await asyncio.to_thread(
            client.remove_policy,
            policy_id,
            current_user.id,
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

    return PolicyClearResponse(
        success=True,
        policies_deleted=policies_deleted,
        rules_removed=rules_removed,
        message=f"Cleared {policies_deleted} policy records and {rules_removed} data-plane rules.",
    )
