"""db_infra-backed multi-turn agent session accumulator."""

from __future__ import annotations

import logging
import time

from app.services.db_infra_client import db_infra_client

logger = logging.getLogger(__name__)


def write_call(
    agent_id: str,
    call_id: str,
    action: str,
    prism_decision: str,
    enforced_decision: str,
) -> None:
    try:
        db_infra_client._request_json(
            "POST",
            "/api/v1/prism-management/sessions/write-call",
            payload={
                "agent_id": agent_id,
                "call_id": call_id,
                "action": action,
                "prism_decision": prism_decision,
                "enforced_decision": enforced_decision,
                "ts": time.time(),
            },
        )
    except Exception as exc:
        logger.error("session_store: write_call failed: %s", exc, exc_info=True)


def insert_call(
    call_id: str,
    agent_id: str,
    session_id: str | None,
    ts_ms: int,
    prism_decision: str,
    enforced_decision: str,
    op: str | None,
    t: str | None,
    enforcement_result_json: str,
    intent_event_json: str | None = None,
    is_dry_run: bool = False,
) -> None:
    try:
        db_infra_client._request_json(
            "POST",
            "/api/v1/prism-management/calls",
            payload={
                "call_id": call_id,
                "agent_id": agent_id,
                "session_id": session_id,
                "ts_ms": ts_ms,
                "prism_decision": prism_decision,
                "enforced_decision": enforced_decision,
                "op": op,
                "t": t,
                "enforcement_result": enforcement_result_json,
                "intent_event": intent_event_json,
                "is_dry_run": is_dry_run,
            },
        )
    except Exception as exc:
        logger.error("session_store: insert_call failed: %s", exc, exc_info=True)


def update_call_decision(
    agent_id: str,
    call_id: str,
    prism_decision: str,
    enforced_decision: str,
) -> None:
    try:
        db_infra_client._request_json(
            "PATCH",
            f"/api/v1/prism-management/sessions/{agent_id}/call-decision",
            payload={
                "call_id": call_id,
                "prism_decision": prism_decision,
                "enforced_decision": enforced_decision,
            },
        )
    except Exception as exc:
        logger.error(
            "session_store: update_call_decision failed: %s",
            exc,
            exc_info=True,
        )


def update_call_enforced_decision(call_id: str, enforced_decision: str) -> None:
    try:
        db_infra_client._request_json(
            "PATCH",
            f"/api/v1/prism-management/calls/{call_id}/enforced-decision",
            payload={"enforced_decision": enforced_decision},
        )
    except Exception as exc:
        logger.error(
            "session_store: update_call_enforced_decision failed: %s",
            exc,
            exc_info=True,
        )


def get_session(agent_id: str) -> dict | None:
    try:
        return db_infra_client._request_json(
            "GET",
            f"/api/v1/prism-management/sessions/{agent_id}",
            allow_not_found=True,
        ) or None
    except Exception as exc:
        logger.error("session_store: get_session failed: %s", exc, exc_info=True)
        return None


def cleanup_expired() -> int:
    try:
        response = db_infra_client._request_json(
            "POST",
            "/api/v1/prism-management/sessions/cleanup",
            payload={},
        )
        return int(response.get("deleted", 0))
    except Exception as exc:
        logger.error(
            "session_store: cleanup_expired failed: %s",
            exc,
            exc_info=True,
        )
        return 0


def initialize_session_vector(agent_id: str, vector: list[float]) -> None:
    try:
        db_infra_client._request_json(
            "POST",
            f"/api/v1/prism-management/sessions/{agent_id}/initialize-vector",
            payload={"vector": vector},
        )
    except Exception as exc:
        logger.error(
            "session_store: initialize_session_vector failed: %s",
            exc,
            exc_info=True,
        )


def compute_and_update_drift(agent_id: str, current_vector: list[float]) -> float:
    try:
        response = db_infra_client._request_json(
            "POST",
            f"/api/v1/prism-management/sessions/{agent_id}/compute-drift",
            payload={
                "vector": current_vector,
                "last_seen_at": time.time(),
            },
        )
        return float(response.get("drift", 0.0))
    except Exception as exc:
        logger.error(
            "session_store: compute_and_update_drift failed: %s",
            exc,
            exc_info=True,
        )
        return 0.0


def get_session_drift(agent_id: str) -> float:
    try:
        response = db_infra_client._request_json(
            "GET",
            f"/api/v1/prism-management/sessions/{agent_id}/drift",
            allow_not_found=True,
        )
        return float(response.get("drift", 0.0))
    except Exception as exc:
        logger.error(
            "session_store: get_session_drift failed: %s",
            exc,
            exc_info=True,
        )
        return 0.0


def list_sessions(
    limit: int = 50,
    offset: int = 0,
    agent_id: str | None = None,
    decision: str | None = None,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
) -> dict:
    try:
        params = {
            "limit": limit,
            "offset": offset,
        }
        if agent_id is not None:
            params["agent_id"] = agent_id
        if decision is not None:
            params["decision"] = decision
        if start_time_ms is not None:
            params["start_time_ms"] = start_time_ms
        if end_time_ms is not None:
            params["end_time_ms"] = end_time_ms
        return db_infra_client._request_json(
            "GET",
            "/api/v1/prism-management/sessions",
            params=params,
        )
    except Exception as exc:
        logger.error("session_store: list_sessions failed: %s", exc, exc_info=True)
        return {"sessions": [], "total_count": 0, "limit": limit, "offset": offset}


def list_calls(
    limit: int = 50,
    offset: int = 0,
    agent_id: str | None = None,
    session_id: str | None = None,
    decision: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    is_dry_run: bool | None = None,
) -> tuple[list[dict], int]:
    try:
        params = {"limit": limit, "offset": offset}
        if agent_id is not None:
            params["agent_id"] = agent_id
        if session_id is not None:
            params["session_id"] = session_id
        if decision is not None:
            params["decision"] = decision
        if start_ms is not None:
            params["start_ms"] = start_ms
        if end_ms is not None:
            params["end_ms"] = end_ms
        if is_dry_run is not None:
            params["is_dry_run"] = is_dry_run
        response = db_infra_client._request_json(
            "GET",
            "/api/v1/prism-management/calls",
            params=params,
        )
        return response.get("calls", []), int(response.get("total_count", 0))
    except Exception as exc:
        logger.error("session_store: list_calls failed: %s", exc, exc_info=True)
        return [], 0


def get_call(call_id: str) -> dict | None:
    try:
        return db_infra_client._request_json(
            "GET",
            f"/api/v1/prism-management/calls/{call_id}",
            allow_not_found=True,
        ) or None
    except Exception as exc:
        logger.error("session_store: get_call failed: %s", exc, exc_info=True)
        return None


def delete_calls() -> int:
    try:
        response = db_infra_client._request_json(
            "DELETE",
            "/api/v1/prism-management/calls",
        )
        return int(response.get("deleted_count", 0))
    except Exception as exc:
        logger.error("session_store: delete_calls failed: %s", exc, exc_info=True)
        return 0


def list_call_runs(
    limit: int = 50,
    offset: int = 0,
    agent_id: str | None = None,
    decision: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    is_dry_run: bool | None = None,
) -> tuple[list[dict], int]:
    try:
        params = {"limit": limit, "offset": offset}
        if agent_id is not None:
            params["agent_id"] = agent_id
        if decision is not None:
            params["decision"] = decision
        if start_ms is not None:
            params["start_ms"] = start_ms
        if end_ms is not None:
            params["end_ms"] = end_ms
        if is_dry_run is not None:
            params["is_dry_run"] = is_dry_run
        response = db_infra_client._request_json(
            "GET",
            "/api/v1/prism-management/call-runs",
            params=params,
        )
        return response.get("runs", []), int(response.get("total_count", 0))
    except Exception as exc:
        logger.error(
            "session_store: list_call_runs failed: %s",
            exc,
            exc_info=True,
        )
        return [], 0
