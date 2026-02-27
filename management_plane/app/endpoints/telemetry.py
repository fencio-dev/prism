"""Telemetry query endpoints for management plane."""

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import session_store
from app.telemetry_models import (
    TelemetrySessionsResponse,
    SessionDetail,
    CallsResponse,
    CallSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["telemetry"])


class CallSummaryWithIntentEvent(CallSummary):
    intent_event: dict[str, Any] | None = Field(
        None,
        description="Deserialized persisted intent event, if available",
    )


class CallDetailWithIntentEvent(BaseModel):
    call: CallSummaryWithIntentEvent = Field(..., description="Call summary fields")
    enforcement_result: dict[str, Any] = Field(
        ...,
        description="Deserialized enforcement result",
    )


@router.get("/telemetry/sessions", response_model=TelemetrySessionsResponse)
def query_sessions(
    agent_id: str | None = Query(None),
    tenant_id: str | None = Query(None),
    decision: str | None = Query(None),
    layer: str | None = Query(None),
    start_time_ms: int | None = Query(None),
    end_time_ms: int | None = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
):
    result = session_store.list_sessions(
        limit=limit,
        offset=offset,
        agent_id=agent_id,
        decision=decision,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
    )

    sessions = []
    for s in result["sessions"]:
        sessions.append(
            {
                "session_id": s["session_id"],
                "agent_id": s["agent_id"],
                "tenant_id": s.get("tenant_id") or tenant_id or "",
                "layer": s.get("layer") or layer or "",
                "timestamp_ms": s["last_seen_at_ms"],
                "final_decision": (s["final_decision"] or "").upper() or "DENY",
                "rules_evaluated_count": s["call_count"],
                "duration_us": 0,
                "intent_summary": s["final_decision"] or "",
            }
        )

    return TelemetrySessionsResponse(
        sessions=sessions,
        total_count=result["total_count"],
        limit=result["limit"],
        offset=result["offset"],
    )


@router.get("/telemetry/sessions/{agent_id}", response_model=SessionDetail)
def get_session_detail(
    agent_id: str,
):
    session = session_store.get_session(agent_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    session.pop("initial_vector", None)
    session.pop("last_vector", None)

    return SessionDetail(session=session)


@router.get("/telemetry/calls", response_model=CallsResponse)
def query_calls(
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    agent_id: str | None = Query(None),
    decision: str | None = Query(None),
    start_ms: int | None = Query(None),
    end_ms: int | None = Query(None),
    is_dry_run: bool | None = Query(None),
):
    rows, total_count = session_store.list_calls(
        limit=limit,
        offset=offset,
        agent_id=agent_id,
        decision=decision,
        start_ms=start_ms,
        end_ms=end_ms,
        is_dry_run=is_dry_run,
    )

    calls = [CallSummary(**row) for row in rows]

    return CallsResponse(
        calls=calls,
        total_count=total_count,
        limit=limit,
        offset=offset,
    )


@router.delete("/telemetry/calls")
def delete_calls():
    deleted = session_store.delete_calls()
    return {"deleted_count": deleted}


@router.get("/telemetry/calls/{call_id}", response_model=CallDetailWithIntentEvent)
def get_call_detail(
    call_id: str,
):
    row = session_store.get_call(call_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Call not found")

    intent_event = None
    if row.get("intent_event"):
        intent_event = json.loads(row["intent_event"])

    call = CallSummaryWithIntentEvent(
        call_id=row["call_id"],
        agent_id=row["agent_id"],
        ts_ms=row["ts_ms"],
        decision=row["decision"],
        op=row.get("op"),
        t=row.get("t"),
        intent_event=intent_event,
    )
    enforcement_result = json.loads(row["enforcement_result"])

    return CallDetailWithIntentEvent(call=call, enforcement_result=enforcement_result)
