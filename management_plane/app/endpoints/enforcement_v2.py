"""
API v2 Enforcement Endpoints.

Endpoints:
- POST /api/v2/enforce - Enforce intent against active policies

Features:
- Direct NL intent encoding (no canonicalization step)
- Drift computation and session tracking
- AARM policy decision: ALLOW, DENY, MODIFY, STEP_UP, DEFER
"""

import asyncio
import json
import logging
import os
import time
import uuid
from functools import lru_cache
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import User, get_current_tenant
from app.models import ComparisonResult, EnforcementResponse, IntentEvent
from app.services import (
    DataPlaneClient,
    DataPlaneError,
    IntentEncoder,
    PolicyEncoder,
)
from app.services import session_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["enforcement-v2"])


# ============================================================================
# Lazy-loaded Service Instances
# ============================================================================


@lru_cache(maxsize=1)
def get_intent_encoder() -> Optional[IntentEncoder]:
    """
    Get singleton intent encoder.

    Lazy-loads model and projection matrices on first access.
    """
    try:
        encoder = IntentEncoder()
        logger.info("Intent encoder initialized")
        return encoder
    except Exception as e:
        logger.error(f"Failed to initialize intent encoder: {e}")
        return None


@lru_cache(maxsize=1)
def get_policy_encoder() -> Optional[PolicyEncoder]:
    """
    Get singleton policy encoder.

    Lazy-loads model and projection matrices on first access.
    """
    try:
        encoder = PolicyEncoder()
        logger.info("Policy encoder initialized")
        return encoder
    except Exception as e:
        logger.error(f"Failed to initialize policy encoder: {e}")
        return None


@lru_cache(maxsize=1)
def get_data_plane_client():
    """Get singleton Data Plane gRPC client."""
    url = os.getenv("DATA_PLANE_URL", "localhost:50051")
    insecure = "localhost" in url or "127.0.0.1" in url
    return DataPlaneClient(url=url, insecure=insecure)


# ============================================================================
# V2 Endpoints
# ============================================================================


@router.post("/enforce", response_model=EnforcementResponse, status_code=status.HTTP_200_OK)
async def enforce_v2(
    event: IntentEvent,
    current_user: User = Depends(get_current_tenant),
    dry_run: bool = False,
) -> EnforcementResponse:
    """
    Enforce intent against active policies.

    Flow:
    1. Validate IntentEvent (FastAPI handles via request body type)
    2. Extract agent_id from identity.agent_id
    3. Encode intent to 128d current_vector
    4. Ensure session row exists (write_call with decision="pending")
    5. Initialize baseline vector if first call for this agent
    6. Compute drift BEFORE gRPC call
    7. Call gRPC enforce with drift_score and session_id
    8. Derive decision_name from result
    9. Return EnforcementResponse

    Args:
        event: IntentEvent (AARM action tuple)
        current_user: Authenticated user

    Returns:
        EnforcementResponse with decision, drift, and evidence

    Raises:
        HTTPException: On encoding, enforcement, or service errors
    """
    request_id = str(uuid.uuid4())

    # Set tenant_id
    event.tenant_id = current_user.id

    # Step 2: Extract agent_id from identity.agent_id
    try:
        agent_id = event.identity.agent_id or ""
    except Exception:
        agent_id = ""

    logger.info(f"V2 enforce request: {request_id}, op={event.op}, t={event.t}, agent_id={agent_id}")

    try:
        # Get services
        intent_encoder = get_intent_encoder()

        if not intent_encoder:
            logger.error("Required services not initialized")
            raise HTTPException(status_code=500, detail="Service initialization failed")

        # Step 3: Encode intent to current_vector
        try:
            vector = intent_encoder.encode(event)
        except Exception as e:
            logger.error(f"Intent encoding failed: {e}", exc_info=True)
            raise HTTPException(status_code=503, detail="Intent encoding failed")

        current_vector = vector.tolist()

        # Step 4: Ensure session row exists
        action = event.op or ""
        try:
            session_store.write_call(agent_id, request_id, action, "pending")
        except Exception as exc:
            logger.error("session_store write_call failed: %s", exc)

        # Step 5: Initialize baseline vector (first call only, no-op after)
        if agent_id:
            try:
                session_store.initialize_session_vector(agent_id, current_vector)
            except Exception as exc:
                logger.error("session_store initialize_session_vector failed: %s", exc)

        # Step 6: Compute drift BEFORE gRPC
        if agent_id:
            try:
                drift_score = session_store.compute_and_update_drift(agent_id, current_vector)
            except Exception as exc:
                logger.error("session_store compute_and_update_drift failed: %s", exc)
                drift_score = 0.0
        else:
            drift_score = 0.0

        # Step 7: Call gRPC enforce
        client = get_data_plane_client()

        try:
            result: ComparisonResult = await asyncio.to_thread(
                client.enforce,
                event,
                current_vector,
                request_id,
                drift_score,
                agent_id,
            )
        except Exception as e:
            logger.error(f"Data Plane enforcement failed: {e}", exc_info=True)

            if isinstance(e, DataPlaneError):
                raise HTTPException(
                    status_code=502,
                    detail=f"Data Plane error: {e}",
                ) from e
            raise HTTPException(status_code=500, detail="Enforcement failed") from e

        # Step 8: Derive decision_name
        if result.decision_name:
            decision_name = result.decision_name
        else:
            decision_name = "ALLOW" if result.decision == 1 else "DENY"

        # Step 8.5: Persist final decision to session store
        try:
            session_store.update_call_decision(agent_id, request_id, decision_name)
        except Exception as exc:
            logger.error("session_store update_call_decision failed: %s", exc)

        # Step 9: Build and persist EnforcementResponse, then return it
        enforcement_response = EnforcementResponse(
            decision=decision_name,
            modified_params=result.modified_params,
            drift_score=drift_score,
            drift_triggered=result.drift_triggered,
            slice_similarities=result.slice_similarities,
            evidence=result.evidence,
        )

        try:
            session_store.insert_call(
                call_id=event.id,
                agent_id=agent_id,
                ts_ms=int(event.ts * 1000),
                decision=decision_name,
                op=event.op,
                t=event.t,
                enforcement_result_json=json.dumps(enforcement_response.model_dump()),
                intent_event_json=json.dumps(event.model_dump(mode="json")),
                is_dry_run=dry_run,
            )
        except Exception as exc:
            logger.error("session_store insert_call failed: %s", exc)

        return enforcement_response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error in V2 enforce: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e
