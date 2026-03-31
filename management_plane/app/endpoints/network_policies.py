"""
Network policies API endpoints.

Provides CRUD operations for network policies. Network policies are
deterministic API endpoint whitelists that are evaluated BEFORE
semantic policies for fast, fail-closed enforcement.
"""

import logging
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.models import NetworkEndpointRule, NetworkPolicy
from app.services import network_policies as network_policy_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/network-policies", tags=["network-policies"])


# ============================================================================
# Request/Response Models
# ============================================================================

class NetworkPolicyCreateRequest(BaseModel):
    """
    Request model for creating a network policy.

    The policy_id will be auto-generated if not provided.
    """
    tenant_id: str
    agent_id: str
    name: str
    status: Literal["active", "inactive"] = "active"
    mode: Literal["Monitor", "Enforce"] = "Enforce"
    whitelist: list[NetworkEndpointRule] = Field(
        description="List of allowed API endpoints"
    )


class NetworkPolicyUpdateRequest(BaseModel):
    """
    Request model for updating a network policy.
    """
    agent_id: str
    name: str
    status: Literal["active", "inactive"]
    mode: Literal["Monitor", "Enforce"]
    whitelist: list[NetworkEndpointRule]


class NetworkPolicyResponse(BaseModel):
    """
    Response model for network policy operations.
    """
    success: bool
    policy_id: str
    message: str


class NetworkPolicyListResponse(BaseModel):
    """
    Response model for listing network policies.
    """
    policies: list[NetworkPolicy]
    total: int


# ============================================================================
# CRUD Endpoints
# ============================================================================

@router.post("", response_model=NetworkPolicyResponse, status_code=201)
async def create_network_policy(
    request: NetworkPolicyCreateRequest
) -> NetworkPolicyResponse:
    """
    Create a new network policy.

    Network policies provide deterministic API endpoint whitelisting
    evaluated before semantic policies. If no whitelist rules match
    the request, it is denied (fail-closed).

    Args:
        request: Network policy creation request

    Returns:
        Response with policy_id and status

    Raises:
        HTTPException: If creation fails
    """
    logger.info(
        f"Creating network policy for agent {request.agent_id}, "
        f"tenant {request.tenant_id}"
    )

    try:
        # Generate policy ID
        policy_id = str(uuid.uuid4())
        timestamp = time.time()

        # Create policy object
        policy = NetworkPolicy(
            policy_id=policy_id,
            tenant_id=request.tenant_id,
            agent_id=request.agent_id,
            name=request.name,
            status=request.status,
            mode=request.mode,
            whitelist=request.whitelist,
            created_at=timestamp,
            updated_at=timestamp,
        )

        # Store in database
        network_policy_service.create_network_policy(policy)

        logger.info(
            f"Successfully created network policy: {policy_id} "
            f"with {len(request.whitelist)} endpoint rules"
        )

        return NetworkPolicyResponse(
            success=True,
            policy_id=policy_id,  # Return Prism-generated ID
            message=f"Network policy created: {policy.name}",
        )

    except Exception as e:
        error_msg = f"Failed to create network policy: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg) from e


@router.get("", response_model=NetworkPolicyListResponse)
async def list_network_policies(
    tenant_id: str = Query(..., description="Tenant identifier"),
    agent_id: str = Query(None, description="Optional agent filter"),
    status: Literal["active", "inactive"] = Query(
        None,
        description="Optional status filter"
    ),
) -> NetworkPolicyListResponse:
    """
    List network policies with optional filtering.

    Args:
        tenant_id: Tenant identifier
        agent_id: Optional filter by agent
        status: Optional filter by status

    Returns:
        List of network policies
    """
    logger.info(
        f"Listing network policies for tenant {tenant_id}, "
        f"agent={agent_id}, status={status}"
    )

    try:
        policies = network_policy_service.list_network_policies(
            tenant_id=tenant_id,
            agent_id=agent_id,
            status=status,
        )

        logger.info(f"Found {len(policies)} network policies")

        return NetworkPolicyListResponse(
            policies=policies,
            total=len(policies),
        )

    except Exception as e:
        error_msg = f"Failed to list network policies: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg) from e


@router.get("/{policy_id}", response_model=NetworkPolicy)
async def get_network_policy(
    policy_id: str,
    tenant_id: str = Query(..., description="Tenant identifier"),
) -> NetworkPolicy:
    """
    Get a specific network policy by ID.

    Args:
        policy_id: Policy identifier
        tenant_id: Tenant identifier

    Returns:
        NetworkPolicy object

    Raises:
        HTTPException: If policy not found
    """
    logger.info(
        f"Fetching network policy: {policy_id} for tenant {tenant_id}"
    )

    policy = network_policy_service.get_network_policy(tenant_id, policy_id)

    if not policy:
        error_msg = f"Network policy not found: {policy_id}"
        logger.error(error_msg)
        raise HTTPException(status_code=404, detail=error_msg)

    logger.info(f"Found network policy: {policy.name}")
    return policy


@router.put("/{policy_id}", response_model=NetworkPolicyResponse)
async def update_network_policy(
    policy_id: str,
    request: NetworkPolicyUpdateRequest,
    tenant_id: str = Query(..., description="Tenant identifier"),
) -> NetworkPolicyResponse:
    """
    Update an existing network policy.

    Args:
        policy_id: Policy identifier
        request: Updated policy data
        tenant_id: Tenant identifier

    Returns:
        Response with status

    Raises:
        HTTPException: If policy not found or update fails
    """
    logger.info(f"Updating network policy: {policy_id}")

    try:
        # Fetch existing policy
        existing = network_policy_service.get_network_policy(
            tenant_id,
            policy_id
        )

        if not existing:
            raise HTTPException(
                status_code=404,
                detail=f"Network policy not found: {policy_id}",
            )

        # Update fields
        updated_policy = NetworkPolicy(
            policy_id=policy_id,
            tenant_id=tenant_id,
            agent_id=request.agent_id,
            name=request.name,
            status=request.status,
            mode=request.mode,
            whitelist=request.whitelist,
            created_at=existing.created_at,
            updated_at=time.time(),
        )

        # Save to database
        network_policy_service.update_network_policy(updated_policy)

        logger.info(f"Successfully updated network policy: {policy_id}")

        return NetworkPolicyResponse(
            success=True,
            policy_id=policy_id,
            message=f"Network policy updated: {updated_policy.name}",
        )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to update network policy: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg) from e


@router.delete("/{policy_id}", response_model=NetworkPolicyResponse)
async def delete_network_policy(
    policy_id: str,
    tenant_id: str = Query(..., description="Tenant identifier"),
) -> NetworkPolicyResponse:
    """
    Delete a network policy.

    Args:
        policy_id: Policy identifier
        tenant_id: Tenant identifier

    Returns:
        Response with status

    Raises:
        HTTPException: If policy not found
    """
    logger.info(f"Deleting network policy: {policy_id}")

    try:
        deleted = network_policy_service.delete_network_policy(
            tenant_id,
            policy_id
        )

        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"Network policy not found: {policy_id}",
            )

        logger.info(f"Successfully deleted network policy: {policy_id}")

        return NetworkPolicyResponse(
            success=True,
            policy_id=policy_id,
            message="Network policy deleted",
        )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Failed to delete network policy: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg) from e
