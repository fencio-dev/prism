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

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.auth import User, get_current_user_from_headers
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
from app.services.db_infra_client import DbInfraClientError, db_infra_client
from app.services.policies import list_policy_records

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["enforcement-v2"])


def _extract_bearer_token(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return value.strip() or None


def _runtime_identity_value(event: IntentEvent, key: str) -> str | None:
    direct = getattr(event, key, None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    runtime_identity = event.runtime_identity
    if isinstance(runtime_identity, dict):
        value = runtime_identity.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    params = event.params
    if isinstance(params, dict):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _canonicalize_event_agent(event: IntentEvent, platform_agent_id: str, tenant_id: str) -> None:
    event.tenant_id = tenant_id
    event.identity.agent_id = platform_agent_id
    if not event.identity.principal_id:
        event.identity.principal_id = event.id
    if not event.source_agent:
        event.source_agent = platform_agent_id
    if not event.destination_agent:
        event.destination_agent = platform_agent_id
    if event.source_agent != platform_agent_id:
        event.source_agent = platform_agent_id
    if event.destination_agent != platform_agent_id:
        event.destination_agent = platform_agent_id


async def _resolve_current_user_and_agent(
    *,
    event: IntentEvent,
    authorization: str | None,
    x_fencio_api_key: str | None,
    x_prism_api_key: str | None,
    x_tenant_id: str | None,
    x_user_id: str | None,
    x_prism_integration_type: str | None,
    x_prism_runtime_instance_id: str | None,
    x_prism_integration_agent_ref: str | None,
    x_prism_endpoint_fingerprint: str | None,
) -> tuple[User, str]:
    api_key = (
        _extract_bearer_token(authorization)
        or _extract_bearer_token(x_fencio_api_key)
        or _extract_bearer_token(x_prism_api_key)
    )
    if not api_key:
        current_user = get_current_user_from_headers(x_tenant_id, x_user_id)
        agent_id = event.identity.agent_id or ""
        event.tenant_id = current_user.id
        return current_user, agent_id

    try:
        runtime_auth = db_infra_client.validate_runtime_credential(api_key)
    except DbInfraClientError as exc:
        logger.warning("Runtime credential validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="invalid_runtime_key") from exc

    tenant_id = str(runtime_auth.get("tenant_id") or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=401, detail="runtime_key_missing_tenant")

    integration_type = (
        x_prism_integration_type
        or _runtime_identity_value(event, "integration_type")
        or "unknown"
    )
    runtime_instance_id = (
        x_prism_runtime_instance_id
        or _runtime_identity_value(event, "runtime_instance_id")
    )
    integration_agent_ref = (
        x_prism_integration_agent_ref
        or _runtime_identity_value(event, "integration_agent_ref")
    )
    endpoint_fingerprint = (
        x_prism_endpoint_fingerprint
        or _runtime_identity_value(event, "endpoint_fingerprint")
    )
    metadata = {
        "event_id": event.id,
        "operation": event.op,
        "source_layer": event.source_layer,
        "destination_layer": event.destination_layer,
        "runtime_identity": event.runtime_identity or {},
    }

    try:
        resolution = db_infra_client.resolve_runtime_agent(
            tenant_id=tenant_id,
            integration_type=integration_type,
            runtime_instance_id=runtime_instance_id,
            integration_agent_ref=integration_agent_ref,
            endpoint_fingerprint=endpoint_fingerprint,
            display_name=integration_agent_ref or runtime_instance_id,
            metadata=metadata,
        )
    except DbInfraClientError as exc:
        logger.error("Runtime agent resolution failed: %s", exc)
        raise HTTPException(status_code=502, detail="runtime_agent_resolution_failed") from exc

    status_value = str(resolution.get("status") or "")
    platform_agent_id = str(resolution.get("platform_agent_id") or "").strip()
    if status_value == "resolved" and platform_agent_id:
        _canonicalize_event_agent(event, platform_agent_id, tenant_id)
        return (
            User(
                id=tenant_id,
                aud="runtime-workspace-key",
                role="runtime",
                email=None,
            ),
            platform_agent_id,
        )
    if status_value == "ambiguous":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "agent_identity_ambiguous",
                "reason": resolution.get("reason"),
                "binding": resolution.get("binding"),
            },
        )
    raise HTTPException(
        status_code=403,
        detail={
            "code": "agent_identity_unresolved",
            "reason": resolution.get("reason"),
            "binding": resolution.get("binding"),
        },
    )


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
        session_store.update_call_decision(
            agent_id,
            event.id,
            decision_name,
            decision_name,
        )
    except Exception as exc:
        logger.error("session_store update_call_decision failed: %s", exc)

    try:
        session_store.insert_call(
            call_id=event.id,
            agent_id=agent_id,
            session_id=session_id,
            ts_ms=int(event.ts * 1000),
            prism_decision=decision_name,
            enforced_decision=decision_name,
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
    request: Request,
    dry_run: bool = False,
    authorization: str | None = Header(default=None),
    x_fencio_api_key: str | None = Header(default=None),
    x_prism_api_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_prism_integration_type: str | None = Header(default=None),
    x_prism_runtime_instance_id: str | None = Header(default=None),
    x_prism_integration_agent_ref: str | None = Header(default=None),
    x_prism_endpoint_fingerprint: str | None = Header(default=None),
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
    _ = request

    try:
        prism_enablement = db_infra_client.get_module_enablement("prism")
        if not prism_enablement.get("enabled", False):
            logger.info("Prism disabled. Allowing request %s without enforcement.", request_id)
            return EnforcementResponse(
                decision="ALLOW",
                modified_params=None,
                drift_score=0.0,
                drift_triggered=False,
                slice_similarities=[1.0, 1.0, 1.0, 1.0],
                evidence=[],
                evaluation_mode="unknown",
                reason="Prism disabled",
            )
    except DbInfraClientError as exc:
        logger.warning("Failed to read Prism enablement, continuing enforcement: %s", exc)

    current_user, agent_id = await _resolve_current_user_and_agent(
        event=event,
        authorization=authorization,
        x_fencio_api_key=x_fencio_api_key,
        x_prism_api_key=x_prism_api_key,
        x_tenant_id=x_tenant_id,
        x_user_id=x_user_id,
        x_prism_integration_type=x_prism_integration_type,
        x_prism_runtime_instance_id=x_prism_runtime_instance_id,
        x_prism_integration_agent_ref=x_prism_integration_agent_ref,
        x_prism_endpoint_fingerprint=x_prism_endpoint_fingerprint,
    )

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
        session_store.write_call(agent_id, event.id, action, "PENDING", "PENDING")
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
                        policy_mode=network_result.mode,
                        policy_type="network",
                        connection_result={
                            "matched": True,
                            "policy_mode": network_result.mode,
                            "policy_type": "network",
                            "policy_effect": "deny",
                            "matched_rule": network_result.matched_rule,
                            "reason": network_result.reason,
                        },
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
