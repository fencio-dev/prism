"""
Data contract type definitions for the Management Plane.

This module defines Pydantic models that match the AARM policy engine schemas.
All types must remain synchronized across Python, TypeScript, and Rust components.
"""

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from typing import Literal, Optional


# ============================================================================
# AgentIdentity — exact-match identity fields (maps to AARM id)
# ============================================================================

class AgentIdentity(BaseModel):
    agent_id: str
    principal_id: str
    actor_type: Literal["user", "service", "llm", "agent"]
    service_account: Optional[str] = None
    role_scope: Optional[str] = None
    # All fields are exact-match only — not embedded


# ============================================================================
# SessionContext — accumulated session context (maps to AARM ctx)
# ============================================================================

class SessionContext(BaseModel):
    initial_request: Optional[str] = None   # NL, contributes to risk slice encoding
    data_classifications: Optional[list[str]] = None
    cumulative_drift: Optional[float] = None


# ============================================================================
# IntentEvent — AARM action tuple a = (t, op, p, id, ctx, ts)
# ============================================================================

class IntentEvent(BaseModel):
    event_type: Literal["tool_call", "reasoning"]
    id: str
    tenant_id: Optional[str] = None
    ts: float  # Unix timestamp — maps to AARM ts
    identity: AgentIdentity  # maps to AARM id
    t: str     # target tool/resource, free-form NL — maps to AARM t
    op: str    # operation, free-form NL — maps to AARM op
    p: Optional[str] = None    # parameter description, NL — maps to AARM p
    params: Optional[dict] = None  # structured params for MODIFY enforcement
    ctx: Optional[SessionContext] = None  # maps to AARM ctx


# ============================================================================
# DesignBoundary Types — AARM policy π: (a, C) → decision
# ============================================================================

class SliceThresholds(BaseModel):
    """
    Per-slice similarity thresholds (0.0 - 1.0).

    Each slot (action, resource, data, risk) has an independent threshold.

    Example:
        {"action": 0.85, "resource": 0.80, "data": 0.75, "risk": 0.70}
    """
    action: float = Field(ge=0.0, le=1.0)
    resource: float = Field(ge=0.0, le=1.0)
    data: float = Field(ge=0.0, le=1.0)
    risk: float = Field(ge=0.0, le=1.0)


class SliceWeights(BaseModel):
    """
    Per-slice weights for weighted-avg aggregation mode.

    Example:
        {"action": 1.0, "resource": 1.0, "data": 1.5, "risk": 0.5}
    """
    action: float = Field(default=1.0, ge=0.0)
    resource: float = Field(default=1.0, ge=0.0)
    data: float = Field(default=1.0, ge=0.0)
    risk: float = Field(default=1.0, ge=0.0)


ScoringMode = Literal["min", "weighted-avg"]


class PolicyMatch(BaseModel):
    """NL match predicate m(a,C) — one description per slice."""
    op: str          # NL anchor for action slice (maps to AARM op)
    t: str           # NL anchor for resource slice (maps to AARM t)
    p: Optional[str] = None    # NL anchor for data slice (maps to AARM p)
    ctx: Optional[str] = None  # NL anchor for risk/context slice


class DesignBoundary(BaseModel):
    id: str
    name: str
    tenant_id: str
    agent_id: str = ""
    status: Literal["active", "disabled"]
    policy_type: Literal["forbidden", "context_allow", "context_deny", "context_defer"]
    priority: int
    match: PolicyMatch      # m(a,C)
    thresholds: SliceThresholds   # keep these unchanged
    scoring_mode: ScoringMode
    weights: Optional[SliceWeights] = None  # keep these unchanged
    drift_threshold: Optional[float] = None
    modification_spec: Optional[dict] = None
    notes: Optional[str] = None
    created_at: float
    updated_at: float

    @field_validator("drift_threshold")
    @classmethod
    def drift_threshold_range(cls, v):
        if v is not None and not (0.0 < v <= 1.0):
            raise ValueError("drift_threshold must be between 0.0 (exclusive) and 1.0 (inclusive)")
        return v

    @model_validator(mode="after")
    def scoring_mode_weights_consistency(self):
        if self.scoring_mode == "min" and self.weights is not None:
            raise ValueError("weights must be omitted when scoring_mode is 'min'")
        if self.scoring_mode == "weighted-avg" and self.weights is None:
            raise ValueError("weights are required when scoring_mode is 'weighted-avg'")
        return self


class PolicyWriteRequest(BaseModel):
    """
    Policy payload for create/update operations.

    Wraps DesignBoundary directly.
    """
    id: str
    name: str
    tenant_id: str
    agent_id: str = ""
    status: Literal["active", "disabled"]
    policy_type: Literal["forbidden", "context_allow", "context_deny", "context_defer"]
    priority: int
    match: PolicyMatch
    thresholds: SliceThresholds
    scoring_mode: ScoringMode
    weights: Optional[SliceWeights] = None
    drift_threshold: Optional[float] = None
    modification_spec: Optional[dict] = None
    notes: Optional[str] = None

    @field_validator("drift_threshold")
    @classmethod
    def drift_threshold_range(cls, v):
        if v is not None and not (0.0 < v <= 1.0):
            raise ValueError("drift_threshold must be between 0.0 (exclusive) and 1.0 (inclusive)")
        return v

    @model_validator(mode="after")
    def scoring_mode_weights_consistency(self):
        if self.scoring_mode == "min" and self.weights is not None:
            raise ValueError("weights must be omitted when scoring_mode is 'min'")
        if self.scoring_mode == "weighted-avg" and self.weights is None:
            raise ValueError("weights are required when scoring_mode is 'weighted-avg'")
        return self


class PolicyListResponse(BaseModel):
    policies: list[DesignBoundary]


class PolicyDeleteResponse(BaseModel):
    success: bool
    policy_id: str
    rules_removed: int
    message: str


class PolicyClearResponse(BaseModel):
    success: bool
    policies_deleted: int
    rules_removed: int
    message: str


# ============================================================================
# FFI Boundary Types — gRPC response mapping
# ============================================================================

class BoundaryEvidence(BaseModel):
    """
    Evidence about a boundary's evaluation for debugging and audit purposes.

    Fields:
    - boundary_id: Unique identifier for the boundary
    - boundary_name: Human-readable boundary name
    - effect: Policy effect (allow or deny)
    - decision: Individual boundary decision (0 = block, 1 = allow)
    - similarities: Per-slot similarity scores [action, resource, data, risk]
    """
    boundary_id: str
    boundary_name: str
    effect: Literal["allow", "deny"]
    decision: Literal[0, 1]
    similarities: list[float] = Field(min_length=4, max_length=4)
    triggering_slice: str = Field(default="")
    anchor_matched: str = Field(default="")
    thresholds: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    scoring_mode: ScoringMode


class ComparisonResult(BaseModel):
    """
    Result from Rust semantic sandbox comparison with boundary evidence.

    Fields:
    - decision: 0 = block, 1 = allow
    - slice_similarities: Per-slot similarity scores [action, resource, data, risk]
    - boundaries_evaluated: Number of boundaries evaluated (for diagnostics)
    - timestamp: Unix timestamp of comparison
    - evidence: List of boundary evaluations (for debugging/audit)
    """
    decision: int = Field(ge=0, le=1)  # 0 = block, 1 = allow
    slice_similarities: list[float] = Field(min_length=4, max_length=4)
    boundaries_evaluated: int = Field(default=0, ge=0)
    timestamp: float = Field(default=0.0)
    evidence: list[BoundaryEvidence] = Field(default_factory=list)
    decision_name: str = Field(default="")
    modified_params: Optional[dict] = Field(default=None)
    drift_triggered: bool = Field(default=False)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "decision": 1,
                "slice_similarities": [0.92, 0.88, 0.85, 0.90],
                "boundaries_evaluated": 3,
                "timestamp": 1699564800.0,
                "evidence": []
            }
        }
    )


class EnforcementResponse(BaseModel):
    """
    Response model for the enforce endpoint.

    Fields:
    - decision: Policy outcome — one of ALLOW, DENY, MODIFY, STEP_UP, DEFER
    - modified_params: Replacement tool params when decision == MODIFY; None otherwise
    - drift_score: Per-call semantic distance from baseline (>= 0.0)
    - drift_triggered: True when drift caused the enforcement outcome
    - slice_similarities: Per-slot cosine similarities [action, resource, data, risk]
    - evidence: Per-boundary evaluation details
    """
    decision: Literal["ALLOW", "DENY", "MODIFY", "STEP_UP", "DEFER"]
    modified_params: Optional[dict] = Field(default=None)
    drift_score: float = Field(ge=0.0)
    drift_triggered: bool
    slice_similarities: list[float] = Field(min_length=4, max_length=4)
    evidence: list[BoundaryEvidence] = Field(default_factory=list)
