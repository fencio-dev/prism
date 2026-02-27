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
            "session_id": "session_001",
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
    session_id: str = Field(..., description="Unique session identifier")
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
            "call_id": "abc123",
            "agent_id": "agent_1",
            "ts_ms": 1700000000000,
            "decision": "ALLOW",
            "op": "tool_call",
            "t": "web_search"
        }
    """
    call_id: str = Field(..., description="Unique call identifier")
    agent_id: str = Field(..., description="Agent that triggered the call")
    ts_ms: int = Field(..., description="Unix timestamp in milliseconds")
    decision: str = Field(..., description="Enforcement decision")
    op: Optional[str] = Field(None, description="Operation type")
    t: Optional[str] = Field(None, description="Tool name or action type")
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
