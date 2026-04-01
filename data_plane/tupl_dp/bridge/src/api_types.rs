//! # API-Facing Data Contracts
//!
//! Rust representations of the canonical data contracts shared with the
//! Management Plane and SDK (IntentEvent v1.3 + ComparisonResult).
//! These structs must stay in sync with `management-plane/app/models.py`
//! and `tupl_sdk/python/tupl/types.py`.

#![allow(dead_code)] // These types will be consumed as the HTTP flow is wired.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::types::EnforcementDecision;

/// Actor initiating the intent (user/service/llm/agent).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Actor {
    pub id: String,
    #[serde(rename = "type")]
    pub actor_type: String,
}

/// Resource descriptor.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Resource {
    #[serde(rename = "type")]
    pub resource_type: String,
    pub name: Option<String>,
    pub location: Option<String>,
}

/// Data descriptor.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Data {
    pub sensitivity: Vec<String>,
    pub pii: Option<bool>,
    pub volume: Option<String>,
    pub content: Option<String>,
    pub size_bytes: Option<u64>,
    pub input_token_count: Option<u64>,
    pub record_count: Option<u64>,
}

/// Risk context descriptor.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Risk {
    pub authn: String,
    pub channel: Option<String>,
}

/// Rate limit tracking context (v1.3).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RateLimitContext {
    pub agent_id: String,
    pub window_start: f64,
    pub call_count: i32,
}

/// Canonical IntentEvent v1.3 schema.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct IntentEvent {
    pub id: String,
    #[serde(rename = "schemaVersion")]
    pub schema_version: String,
    #[serde(rename = "tenantId")]
    pub tenant_id: String,
    pub timestamp: f64,
    pub actor: Actor,
    pub action: String,
    pub source_agent: Option<String>,
    pub source_layer: Option<String>,
    pub destination_agent: Option<String>,
    pub destination_layer: Option<String>,
    pub llm_tool_intent: Option<String>,
    pub tool_call_count: Option<u64>,
    pub resource: Resource,
    pub data: Data,
    pub risk: Risk,
    pub context: Option<Value>,
    pub layer: Option<String>,
    pub tool_name: Option<String>,
    pub tool_method: Option<String>,
    pub tool_params: Option<Value>,
    pub rate_limit_context: Option<RateLimitContext>,
}

impl IntentEvent {
    /// Convenience accessor for the string layer identifier (e.g., "L4").
    pub fn layer_str(&self) -> Option<&str> {
        self.layer.as_deref()
    }
}

/// Boundary-level evidence emitted by comparisons.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct BoundaryEvidence {
    pub boundary_id: String,
    pub boundary_name: String,
    pub effect: String,
    pub decision: u8,
    pub similarities: [f32; 4],
}

/// Canonical comparison result returned by the Management Plane.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ComparisonResult {
    pub decision: u8,
    pub slice_similarities: [f32; 4],
    pub boundaries_evaluated: u32,
    pub timestamp: f64,
    pub evidence: Vec<BoundaryEvidence>,
    /// Populated by the v3 enforcement path. None on the legacy v2 path.
    #[serde(default)]
    pub enforcement_decision: Option<EnforcementDecision>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn intent_event_roundtrip_matches_schema() {
        let value = json!({
            "id": "evt-123",
            "schemaVersion": "v1.3",
            "tenantId": "tenant-1",
            "timestamp": 1699564800.0,
            "actor": {"id": "agent-1", "type": "agent"},
            "action": "read",
            "resource": {"type": "database", "name": "users_db", "location": "cloud"},
            "data": {"sensitivity": ["internal"], "pii": false, "volume": "single"},
            "risk": {"authn": "required"},
            "layer": "L4",
            "tool_name": "web_search",
            "tool_method": "query",
            "tool_params": {"query": "example"},
            "rate_limit_context": {"agent_id": "agent-1", "window_start": 1699564800.0, "call_count": 3}
        });

        let intent: IntentEvent = serde_json::from_value(value).unwrap();
        assert_eq!(intent.schema_version, "v1.3");
        let back = serde_json::to_value(&intent).unwrap();
        let reparsed: IntentEvent = serde_json::from_value(back).unwrap();
        assert_eq!(intent, reparsed);
    }

    #[test]
    fn comparison_result_roundtrip_matches_schema() {
        let value = json!({
            "decision": 1,
            "slice_similarities": [0.9, 0.88, 0.85, 0.87],
            "boundaries_evaluated": 2,
            "timestamp": 1699564800.0,
            "evidence": [{
                "boundary_id": "allow-read",
                "boundary_name": "Allow Read Ops",
                "effect": "allow",
                "decision": 1,
                "similarities": [0.91, 0.89, 0.86, 0.88]
            }]
        });

        let result: ComparisonResult = serde_json::from_value(value).unwrap();
        assert_eq!(result.boundaries_evaluated, 2);
        let back = serde_json::to_value(&result).unwrap();
        let reparsed: ComparisonResult = serde_json::from_value(back).unwrap();
        assert_eq!(result, reparsed);
    }
}
