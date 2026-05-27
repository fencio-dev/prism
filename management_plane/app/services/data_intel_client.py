from __future__ import annotations

from fencio_logger import get_logger

import asyncio
import time
from typing import Any, Literal

import httpx

from app.models import DesignBoundary, EnforcementResponse, IntentEvent
from app.services.db_infra_client import DbInfraClientError, db_infra_client
from app.settings import config

logger = get_logger(__name__, service_name="prism")

PrismIntelEventType = Literal[
    "prism.enforcement.completed",
    "prism.dry_run.completed",
    "prism.policy.upserted",
    "prism.policy.deleted",
    "prism.policy.anchors.encoded",
    "prism.policy.installed",
]


def _now_ms() -> int:
    return int(time.time() * 1000)


class DataIntelClient:
    """Best-effort Prism emitter for the shared data intelligence layer."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        schema_version: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._schema_version = schema_version

    def emit_event_async_best_effort(
        self,
        *,
        event_id: str,
        event_type: PrismIntelEventType,
        tenant_id: str,
        agent_id: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        occurred_at_ms: int | None = None,
    ) -> None:
        if not config.DATA_INTEL_ENABLED:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                asyncio.to_thread(
                    self.emit_event_best_effort,
                    event_id=event_id,
                    event_type=event_type,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    payload=payload,
                    occurred_at_ms=occurred_at_ms,
                )
            )
        except RuntimeError:
            self.emit_event_best_effort(
                event_id=event_id,
                event_type=event_type,
                tenant_id=tenant_id,
                agent_id=agent_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
                occurred_at_ms=occurred_at_ms,
            )

    def emit_event_best_effort(
        self,
        *,
        event_id: str,
        event_type: PrismIntelEventType,
        tenant_id: str,
        agent_id: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        occurred_at_ms: int | None = None,
    ) -> None:
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "payload": payload,
            "schema_version": self._schema_version,
            "occurred_at_ms": occurred_at_ms or _now_ms(),
        }
        try:
            self._send_direct(event)
            return
        except Exception as exc:
            logger.warning("data_intel direct emit failed for %s: %s", event_id, exc)

        if not config.DATA_INTEL_FALLBACK_OUTBOX:
            return

        try:
            db_infra_client.enqueue_intel_outbox_event(
                event_id=event_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
            )
        except (DbInfraClientError, OSError) as exc:
            logger.error("data_intel outbox enqueue failed for %s: %s", event_id, exc)

    def _send_direct(self, event: dict[str, Any]) -> None:
        with httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
        ) as client:
            response = client.post(
                "/api/v1/prism/events/batch",
                json={
                    "events": [event],
                    "normalize_on_ingest": True,
                },
            )
        response.raise_for_status()


data_intel_client = DataIntelClient(
    config.DATA_INTEL_BASE_URL,
    config.DATA_INTEL_TIMEOUT_SECONDS,
    config.DATA_INTEL_SCHEMA_VERSION,
)


def emit_enforcement_completed(
    *,
    agent_id: str,
    event: IntentEvent,
    enforcement_response: EnforcementResponse,
    decision_name: str,
    dry_run: bool,
    session_id: str,
) -> None:
    tenant_id = event.tenant_id or ""
    resolved_agent_id = agent_id or event.identity.agent_id or ""
    if not tenant_id or not resolved_agent_id:
        logger.debug("Skipping data_intel enforcement event without tenant_id/agent_id")
        return

    event_type: PrismIntelEventType = (
        "prism.dry_run.completed" if dry_run else "prism.enforcement.completed"
    )
    payload = {
        "call_id": event.id,
        "session_id": session_id,
        "tenant_id": tenant_id,
        "agent_id": resolved_agent_id,
        "decision": decision_name,
        "dry_run": dry_run,
        "source_agent": event.source_agent,
        "source_layer": event.source_layer,
        "destination_agent": event.destination_agent,
        "destination_layer": event.destination_layer,
        "op": event.op,
        "t": event.t,
        "tool_name": event.tool_name,
        "tool_method": event.tool_method,
        "payload_text": event.payload_text,
        "llm_tool_intent": event.llm_tool_intent,
        "policy_drift_score": enforcement_response.drift_score,
        "baseline_drift_score": enforcement_response.baseline_drift_score,
        "drift_triggered": enforcement_response.drift_triggered,
        "slice_similarities": enforcement_response.slice_similarities,
        "evaluation_mode": enforcement_response.evaluation_mode,
        "reason": enforcement_response.reason,
        "intent_event": event.model_dump(mode="json"),
        "enforcement_result": enforcement_response.model_dump(mode="json"),
    }
    data_intel_client.emit_event_async_best_effort(
        event_id=f"{event_type}:{event.id}",
        event_type=event_type,
        tenant_id=tenant_id,
        agent_id=resolved_agent_id,
        aggregate_type="dry_run" if dry_run else "enforcement_call",
        aggregate_id=event.id,
        payload=payload,
        occurred_at_ms=int(event.ts * 1000),
    )


def emit_policy_event(
    *,
    event_type: PrismIntelEventType,
    tenant_id: str,
    boundary: DesignBoundary,
    payload_extra: dict[str, Any] | None = None,
) -> None:
    agent_id = boundary.agent_id or tenant_id
    payload = boundary.model_dump(mode="json")
    if payload_extra:
        payload.update(payload_extra)
    data_intel_client.emit_event_async_best_effort(
        event_id=f"{event_type}:{tenant_id}:{boundary.id}:{int(time.time() * 1000)}",
        event_type=event_type,
        tenant_id=tenant_id,
        agent_id=agent_id,
        aggregate_type="policy",
        aggregate_id=boundary.id,
        payload=payload,
    )


def emit_policy_deleted(
    *,
    tenant_id: str,
    policy_id: str,
    agent_id: str,
) -> None:
    resolved_agent_id = agent_id or tenant_id
    data_intel_client.emit_event_async_best_effort(
        event_id=f"prism.policy.deleted:{tenant_id}:{policy_id}:{int(time.time() * 1000)}",
        event_type="prism.policy.deleted",
        tenant_id=tenant_id,
        agent_id=resolved_agent_id,
        aggregate_type="policy",
        aggregate_id=policy_id,
        payload={
            "tenant_id": tenant_id,
            "agent_id": resolved_agent_id,
            "policy_id": policy_id,
        },
    )
