"""
Telemetry response models for Management Plane API.

Pydantic models for telemetry query responses, matching the gRPC proto
definitions from rule_installation.proto.
"""

from pydantic import BaseModel, Field
from typing import Any, Optional


class SessionSummary(BaseModel):
    """
    Summary of an enforcement session.
    
    Matches EnforcementSessionSummary from proto.
    
    Example:
        {
            "agent_call_id": "call_001",
            "agent_id": "agent_123",
            "tenant_id": "tenant_abc",
            "layer": "L4",
            "timestamp_ms": 1700000000000,
            "final_decision": 1,
            "rules_evaluated_count": 3,
            "duration_us": 1250,
            "intent_summary": "web_search"
        }
    """
    session_id: str = Field(..., description="Legacy data-plane session identifier")
    agent_id: str = Field(..., description="Agent that triggered enforcement")
    tenant_id: str = Field(..., description="Tenant ID")
    layer: str = Field(..., description="Layer (L0-L6)")
    timestamp_ms: int = Field(..., description="Unix timestamp in milliseconds")
    final_decision: str = Field(..., description="ALLOW, DENY, MODIFY, STEP_UP, or DEFER")
    rules_evaluated_count: int = Field(..., description="Number of rules evaluated")
    duration_us: int = Field(..., description="Enforcement duration in microseconds")
    intent_summary: str = Field(..., description="Tool name or action summary")


class TelemetrySessionsResponse(BaseModel):
    """
    Response for GET /sessions endpoint.
    
    Contains paginated list of session summaries with total count.
    
    Example:
        {
            "sessions": [...],
            "total_count": 42,
            "limit": 50,
            "offset": 0
        }
    """
    sessions: list[SessionSummary] = Field(..., description="List of session summaries")
    total_count: int = Field(..., description="Total number of matching sessions")
    limit: int = Field(..., description="Pagination limit")
    offset: int = Field(..., description="Pagination offset")


class SessionDetail(BaseModel):
    """
    Full details for a specific enforcement session.

    Contains the complete session data including all rule evaluations,
    intent details, and timing information.

    Example:
        {
            "session": {
                "session_id": "session_001",
                "agent_id": "agent_123",
                "final_decision": 1,
                "rules_evaluated": [...],
                "intent": {...}
            }
        }
    """
    session: dict[str, Any] = Field(..., description="Full session data as JSON object")


class CallSummary(BaseModel):
    """
    Summary of a single enforce_calls row.

    Example:
        {
            "event_id": "evt_123",
            "agent_id": "agent_1",
            "ts_ms": 1700000000000,
            "decision": "ALLOW",
            "op": "tool_call",
            "t": "web_search"
        }
    """
    event_id: str = Field(..., description="Unique enforcement event identifier")
    agent_call_id: str = Field(..., description="Parent agent call identifier")
    agent_id: str = Field(..., description="Agent that triggered the call")
    ts_ms: int = Field(..., description="Unix timestamp in milliseconds")
    decision: str = Field(..., description="Proxy-enforced decision")
    prism_decision: str = Field(..., description="Raw Prism verdict")
    enforced_decision: str = Field(..., description="Proxy-enforced decision")
    op: Optional[str] = Field(None, description="Operation type")
    t: Optional[str] = Field(None, description="Tool name or action type")
    source_agent: Optional[str] = Field(None, description="Source agent for the intercepted path")
    source_layer: Optional[str] = Field(None, description="Source Prism layer for the intercepted path")
    destination_agent: Optional[str] = Field(None, description="Destination agent for the intercepted path")
    destination_layer: Optional[str] = Field(None, description="Destination Prism layer for the intercepted path")
    tool_name: Optional[str] = Field(None, description="Runtime tool identity")
    rag_source_id: Optional[str] = Field(None, description="Runtime RAG source identity")
    rag_source_name: Optional[str] = Field(None, description="Runtime RAG source display name")
    resource_identity_type: Optional[str] = Field(None, description="Generic runtime resource identity type")
    resource_identity_key: Optional[str] = Field(None, description="Generic runtime resource identity key")
    resource_identity_name: Optional[str] = Field(None, description="Generic runtime resource display name")
    is_dry_run: bool = Field(False, description="Whether this was a dry-run call")


class CallsResponse(BaseModel):
    """
    Response for GET /telemetry/calls endpoint.

    Example:
        {
            "calls": [...],
            "total_count": 120,
            "limit": 50,
            "offset": 0
        }
    """
    calls: list[CallSummary] = Field(..., description="List of call summaries")
    total_count: int = Field(..., description="Total number of matching calls")
    limit: int = Field(..., description="Pagination limit")
    offset: int = Field(..., description="Pagination offset")


class CallDetail(BaseModel):
    """
    Full detail for a single enforce_calls row.

    Example:
        {
            "call": {...},
            "enforcement_result": {...}
        }
    """
    call: CallSummary = Field(..., description="Call summary fields")
    enforcement_result: dict = Field(..., description="Deserialized enforcement result")


class TelemetryRunSummary(BaseModel):
    """
    Summary of one protected agent call grouped by agent_call_id.
    """

    agent_call_id: str = Field(..., description="Parent identifier for one agent request")
    agent_id: str = Field(..., description="Agent that produced the run")
    started_at_ms: int = Field(..., description="First call timestamp in milliseconds")
    last_seen_at_ms: int = Field(..., description="Most recent call timestamp in milliseconds")
    total_calls: int = Field(..., description="Total enforcement calls recorded")
    allow_count: int = Field(..., description="Count of ALLOW decisions")
    deny_count: int = Field(..., description="Count of DENY decisions")
    modify_count: int = Field(..., description="Count of MODIFY decisions")
    step_up_count: int = Field(..., description="Count of STEP_UP decisions")
    defer_count: int = Field(..., description="Count of DEFER decisions")
    final_decision: str = Field(..., description="Final proxy-enforced decision observed in the run")
    prism_final_decision: str = Field(..., description="Final raw Prism verdict observed in the run")
    last_op: Optional[str] = Field(None, description="Latest operation string")
    last_target: Optional[str] = Field(None, description="Latest target string")
    latest_drift_score: float = Field(
        0.0,
        description="Latest policy-relative drift score seen in the run",
    )
    latest_baseline_drift_score: Optional[float] = Field(
        None,
        description="Latest legacy session-baseline drift score seen in the run",
    )
    drift_source: str = Field(
        "policy",
        description="Source of the latest_drift_score value",
    )


class TelemetryRunsResponse(BaseModel):
    """
    Paginated runtime run summaries grouped by agent_call_id.
    """

    runs: list[TelemetryRunSummary] = Field(..., description="List of runtime runs")
    total_count: int = Field(..., description="Total number of matching runs")
    limit: int = Field(..., description="Pagination limit")
    offset: int = Field(..., description="Pagination offset")
