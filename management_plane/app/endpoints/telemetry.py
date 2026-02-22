"""Telemetry query endpoints for management plane."""

import logging
import os
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services import DataPlaneClient
from app.telemetry_models import TelemetrySessionsResponse, SessionDetail

logger = logging.getLogger(__name__)

router = APIRouter(tags=["telemetry"])


@lru_cache(maxsize=1)
def get_data_plane_client() -> DataPlaneClient:
    url = os.getenv("DATA_PLANE_URL", "localhost:50051")
    insecure = "localhost" in url or "127.0.0.1" in url
    return DataPlaneClient(url=url, insecure=insecure)


@router.get("/telemetry/sessions", response_model=TelemetrySessionsResponse)
def query_sessions(
    agent_id: str | None = Query(None),
    tenant_id: str | None = Query(None),
    decision: int | None = Query(None),
    layer: str | None = Query(None),
    start_time_ms: int | None = Query(None),
    end_time_ms: int | None = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
    client: DataPlaneClient = Depends(get_data_plane_client),
):
    try:
        return client.query_telemetry(
            agent_id=agent_id,
            tenant_id=tenant_id,
            decision=decision,
            layer=layer,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.error("query_sessions failed: %s", exc)
        raise HTTPException(status_code=502, detail="Data plane unavailable")


@router.get("/telemetry/sessions/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: str,
    client: DataPlaneClient = Depends(get_data_plane_client),
):
    try:
        return client.get_session(session_id)
    except Exception as exc:
        logger.error("get_session failed for %s: %s", session_id, exc)
        raise HTTPException(status_code=502, detail="Data plane unavailable")
