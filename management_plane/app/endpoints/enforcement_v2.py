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
from app.models import (
    BoundaryEvidence,
    ComparisonResult,
    EnforcementResponse,
    IntentEvent,
)
from app.services import (
    DataPlaneClient,
    DataPlaneError,
    IntentEncoder,
    PolicyEncoder,
)
from app.services import session_store
from app.services.policies import list_policy_records

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
    return DataPlaneClient(url=url, insecure=True)


def _persist_enforcement_record(
    *,
    agent_id: str,
    request_id: str,
    event: IntentEvent,
    enforcement_response: EnforcementResponse,
    decision_name: str,
    dry_run: bool,
    session_id: str,
) -> None:
    """
    Persist enforcement output for telemetry and session history.
    """
    try:
        session_store.update_call_decision(agent_id, request_id, decision_name)
    except Exception as exc:
        logger.error("session_store update_call_decision failed: %s", exc)

    try:
        session_store.insert_call(
            call_id=event.id,
            agent_id=agent_id,
            session_id=session_id,
            ts_ms=int(event.ts * 1000),
            decision=decision_name,
            op=event.op,
            t=event.t,
            enforcement_result_json=json.dumps(
                enforcement_response.model_dump(mode="json")
            ),
            intent_event_json=json.dumps(event.model_dump(mode="json")),
            is_dry_run=dry_run,
        )
    except Exception as exc:
        logger.error("session_store insert_call failed: %s", exc)


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

    logger.info(
        "V2 enforce request: %s, op=%s, t=%s, agent_id=%s, "
        "source_layer=%s, destination_layer=%s",
        request_id,
        event.op,
        event.t,
        agent_id,
        event.source_layer,
        event.destination_layer,
    )
    logger.info(
        "V2 enforce intent payload for %s: %s",
        request_id,
        json.dumps(
            event.model_dump(mode="json", exclude_none=False),
            ensure_ascii=False,
        ),
    )

    session_id = event.identity.principal_id or agent_id or event.id
    action = event.op or ""

    try:
        session_store.write_call(agent_id, request_id, action, "pending")
    except Exception as exc:
        logger.error("session_store write_call failed: %s", exc)

    try:
        # ====================================================================
        # STAGE 0: NETWORK POLICY ENFORCEMENT (if enabled)
        # ====================================================================
        if event.enforce_network and event.network_context:
            logger.info(f"Network policy enforcement enabled for agent {agent_id}")

            try:
                from app.services.network_policy_evaluator import evaluate_network_policies

                network_result = evaluate_network_policies(
                    tenant_id=event.tenant_id,
                    agent_id=agent_id,
                    network_ctx=event.network_context,
                    selected_policy_ids=event.dry_run_rule_ids,
                )

                logger.info(
                    f"Network policy result: {network_result.decision} - "
                    f"{network_result.reason}"
                )

                # If network policy denies and mode is Enforce, block immediately
                if network_result.decision == "DENY":
                    logger.warning(
                        f"Network policy DENIED: "
                        f"{event.network_context.method} {event.network_context.url}"
                    )

                    # Build evidence for network policy denial
                    network_evidence = BoundaryEvidence(
                        boundary_id=network_result.policy_id or "network-policy",
                        boundary_name=network_result.policy_name or "Network Policy",
                        effect="deny",
                        decision=0,
                        similarities=[0.0, 0.0, 0.0, 0.0],
                        triggering_slice="network",
                        anchor_matched=f"{event.network_context.method} {event.network_context.url}",
                        thresholds=[0.0, 0.0, 0.0, 0.0],
                        scoring_mode="min",
                        evaluation_mode="network",
                        connection_result=None,
                        deterministic_results=[],
                    )

                    # Return immediate DENY without semantic evaluation
                    enforcement_response = EnforcementResponse(
                        decision="DENY",
                        modified_params=None,
                        drift_score=0.0,
                        drift_triggered=False,
                        slice_similarities=[0.0, 0.0, 0.0, 0.0],
                        evidence=[network_evidence],
                        evaluation_mode="network",
                        reason=network_result.reason,
                    )
                    _persist_enforcement_record(
                        agent_id=agent_id,
                        request_id=request_id,
                        event=event,
                        enforcement_response=enforcement_response,
                        decision_name="DENY",
                        dry_run=dry_run,
                        session_id=session_id,
                    )
                    return enforcement_response

                logger.info(
                    f"Network policy check passed, proceeding to semantic enforcement"
                )

            except Exception as e:
                logger.error(
                    f"Network policy evaluation failed: {e}",
                    exc_info=True
                )
                # On evaluation error, log and continue to semantic
                # (fail-open for network policy errors to avoid blocking)

        # ====================================================================
        # STAGE 1: SEMANTIC POLICY ENFORCEMENT
        # ====================================================================

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
        # Determine which policy namespace to enforce against:
        # prefer per-agent policies; fall back to tenant-wide policies.
        enforce_namespace = event.tenant_id
        if agent_id:
            per_agent_policies = list_policy_records(event.tenant_id, agent_id=agent_id)
            if per_agent_policies:
                enforce_namespace = agent_id
        client = get_data_plane_client()

        try:
            result: ComparisonResult = await asyncio.to_thread(
                client.enforce,
                event,
                current_vector,
                request_id,
                drift_score,
                enforce_namespace,
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

        # Step 9: Build and persist EnforcementResponse, then return it
        enforcement_response = EnforcementResponse(
            decision=decision_name,
            modified_params=result.modified_params,
            drift_score=drift_score,
            drift_triggered=result.drift_triggered,
            slice_similarities=result.slice_similarities,
            evidence=result.evidence,
            evaluation_mode=result.evaluation_mode,
            reason=result.reason,
        )
        _persist_enforcement_record(
            agent_id=agent_id,
            request_id=request_id,
            event=event,
            enforcement_response=enforcement_response,
            decision_name=decision_name,
            dry_run=dry_run,
            session_id=session_id,
        )

        return enforcement_response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unhandled error in V2 enforce: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e
