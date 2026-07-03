//! # Enforcement Engine for Layer-Based Rule Enforcement (v1.3)
//!
//! Orchestrates the enforcement pipeline:
//! 1. Receives IntentEvent from SDK via gRPC
//! 2. Calls Management Plane to encode intent to 128d vector
//! 3. Queries rules from Bridge for the specified layer
//! 4. Compares intent vector directly against rule anchors using in-process comparison
//! 5. Implements short-circuit evaluation (first ALLOW match stops evaluation; fail-closed if none match)
//! 6. Returns enforcement decision with evidence
//! 7. Records complete telemetry to /var/hitlogs for audit trail

use std::collections::HashSet;
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::api_types::IntentEvent;
use regex::{Regex, RegexBuilder};
use reqwest::{header::CONTENT_TYPE, Client};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::bridge::Bridge;
use crate::rule_vector::RuleVector;
use crate::telemetry::session::SliceComparisonDetail;
use crate::telemetry::{EnforcementSession, RuleEvaluationEvent, SessionEvent, TelemetryRecorder};
use crate::types::{Decision, EnforcementDecision, PolicyType, RuleInstance};
use crate::vector_comparison::{compare_intent_vs_rule, ComparisonResult, DecisionMode};

const CONNECT_TIMEOUT_MS: u64 = 500;
const REQUEST_TIMEOUT_MS: u64 = 1_500;

// Per-slot thresholds for ToolWhitelist family
// [Action, Resource, Data, Risk]
// Resource slot (0.88) is most critical for tool identity matching
// Calibrated to distinguish exact tool matches from semantic similarities
// Action threshold lowered to 0.60 to account for semantic variation (read/query/search)

// Default thresholds for other rule families
const DEFAULT_THRESHOLDS: [f32; 4] = [0.75, 0.75, 0.75, 0.75];

#[derive(Debug, Deserialize)]
struct SliceThresholdsPayload {
    action: f32,
    resource: f32,
    data: f32,
    risk: f32,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
struct ConnectionMatchPayload {
    source_agent: String,
    source_layer: String,
    destination_agent: String,
    destination_layer: String,
}

#[derive(Debug, Deserialize)]
struct DeterministicConditionPayload {
    condition_type: String,
    operator: String,
    parameters: Value,
}

#[derive(Debug, Deserialize)]
struct SemanticConditionPayload {
    condition_type: String,
    operator: String,
    parameters: Value,
}

#[derive(Debug, Clone, Serialize)]
struct ConnectionEvaluationPayload {
    matched: bool,
    source_agent: String,
    source_layer: String,
    destination_agent: String,
    destination_layer: String,
    intent_source_agent: Option<String>,
    intent_source_layer: Option<String>,
    intent_destination_agent: Option<String>,
    intent_destination_layer: Option<String>,
    policy_mode: String,
    policy_type: String,
    policy_effect: String,
    reason: String,
}

#[derive(Debug, Clone, Serialize)]
struct DeterministicConditionResultPayload {
    condition_type: String,
    operator: String,
    passed: bool,
    target_field: Option<String>,
    actual_value: Option<Value>,
    expected_value: Option<Value>,
    details: String,
}

#[derive(Debug, Clone, Serialize)]
struct SemanticConditionResultPayload {
    condition_type: String,
    operator: String,
    passed: bool,
    target_field: Option<String>,
    actual_value: Option<Value>,
    expected_value: Option<Value>,
    details: String,
}

// ============================================================================
// Data Structures
// ============================================================================

/// Enforcement engine that coordinates intent evaluation against rules
pub struct EnforcementEngine {
    /// Reference to the Bridge for querying rules
    bridge: Arc<Bridge>,

    /// Management Plane encoding endpoint
    encoding_endpoint: String,

    /// Shared HTTP client (reqwest + rustls)
    http_client: Client,

    /// Telemetry recorder (optional - can be disabled)
    telemetry: Option<Arc<TelemetryRecorder>>,
}

/// Result of enforcement evaluation
#[derive(Debug, Clone)]
pub struct EnforcementResult {
    /// Legacy 0 = BLOCK, 1 = ALLOW (for backward compat on v2 path)
    pub decision: u8,

    /// Per-slot similarity scores [action, resource, data, risk]
    pub slice_similarities: [f32; 4],

    /// Number of rules evaluated before decision
    pub rules_evaluated: usize,

    /// Evidence from each rule evaluation
    pub evidence: Vec<RuleEvidence>,

    /// Session ID used for telemetry (equals request_id if provided, else a generated UUID)
    pub session_id: String,

    /// Full AARM enforcement decision (populated by the 5-pass evaluation path).
    pub enforcement_decision: Option<EnforcementDecision>,

    /// Overall evaluation mode reflected in the returned evidence.
    pub evaluation_mode: String,

    /// Optional human-readable explanation for the returned decision.
    pub reason: String,
}

struct RuleQueryResult {
    rules: Vec<Arc<dyn RuleInstance>>,
    candidate_count: usize,
    skipped: Vec<RuleConnectionSkip>,
    invalid: Vec<RuleConnectionSkip>,
}

struct RuleConnectionSkip {
    rule_id: String,
    rule_name: String,
    reason: String,
    connection_result_json: String,
}

struct RuleConnectionCheck {
    matched: bool,
    invalid_policy_edge: bool,
    reason: String,
    connection_result_json: String,
}

struct RuleEvaluationOutcome {
    comparison: ComparisonResult,
    policy_drift_score: f32,
    guard_triggered: bool,
}

/// Evidence from a single rule evaluation
#[derive(Debug, Clone)]
pub struct RuleEvidence {
    pub rule_id: String,
    pub rule_name: String,
    pub decision: u8, // 0 = blocked, 1 = passed
    pub similarities: [f32; 4],
    pub triggering_slice: String,
    pub anchor_matched: String,
    pub thresholds: [f32; 4],
    pub scoring_mode: String,
    pub evaluation_mode: String,
    pub connection_result_json: String,
    pub deterministic_results_json: String,
    pub semantic_results_json: String,
}

#[derive(Debug, Deserialize)]
struct IntentEncodingResponse {
    vector: Vec<f32>,
}

// ============================================================================
// EnforcementEngine Implementation
// ============================================================================

impl EnforcementEngine {
    fn build_fail_closed_evidence(rule_id: &str, rule_name: &str, details: &str) -> RuleEvidence {
        RuleEvidence {
            rule_id: rule_id.to_string(),
            rule_name: rule_name.to_string(),
            decision: 0,
            similarities: [0.0; 4],
            triggering_slice: "policy".to_string(),
            anchor_matched: details.to_string(),
            thresholds: [0.0; 4],
            scoring_mode: "min".to_string(),
            evaluation_mode: "semantic".to_string(),
            connection_result_json: String::new(),
            deterministic_results_json: "[]".to_string(),
            semantic_results_json: serde_json::to_string(&vec![json!({
                "condition_type": "policy_resolution",
                "operator": "implicit_default_deny",
                "passed": false,
                "target_field": "layer",
                "expected_value": null,
                "actual_value": null,
                "details": details,
            })])
            .unwrap_or_else(|_| "[]".to_string()),
        }
    }

    fn evidence_has_guard_veto(evidence: &[RuleEvidence]) -> bool {
        evidence.iter().any(|item| {
            serde_json::from_str::<Value>(&item.semantic_results_json)
                .ok()
                .and_then(|value| value.as_array().cloned())
                .map(|results| {
                    results.iter().any(|result| {
                        result
                            .get("actual_value")
                            .and_then(|actual| actual.get("guard_triggered"))
                            .and_then(|value| value.as_bool())
                            .unwrap_or(false)
                    })
                })
                .unwrap_or(false)
        })
    }

    fn build_connection_fail_closed_evidence(
        rule_id: &str,
        rule_name: &str,
        details: &str,
        connection_result_json: String,
        semantic_details: Value,
    ) -> RuleEvidence {
        RuleEvidence {
            rule_id: rule_id.to_string(),
            rule_name: rule_name.to_string(),
            decision: 0,
            similarities: [0.0; 4],
            triggering_slice: "connection".to_string(),
            anchor_matched: details.to_string(),
            thresholds: [0.0; 4],
            scoring_mode: "exact".to_string(),
            evaluation_mode: "connection".to_string(),
            connection_result_json,
            deterministic_results_json: "[]".to_string(),
            semantic_results_json: serde_json::to_string(&vec![semantic_details])
                .unwrap_or_else(|_| "[]".to_string()),
        }
    }

    fn normalize_edge_value(value: &Option<String>) -> Option<String> {
        value
            .as_ref()
            .map(|item| item.trim())
            .filter(|item| !item.is_empty())
            .map(|item| item.to_string())
    }

    fn missing_intent_edge_fields(intent: &IntentEvent) -> Vec<&'static str> {
        let mut missing = Vec::new();
        if Self::normalize_edge_value(&intent.source_agent).is_none() {
            missing.push("source_agent");
        }
        if Self::normalize_edge_value(&intent.source_layer).is_none() {
            missing.push("source_layer");
        }
        if Self::normalize_edge_value(&intent.destination_agent).is_none() {
            missing.push("destination_agent");
        }
        if Self::normalize_edge_value(&intent.destination_layer).is_none() {
            missing.push("destination_layer");
        }
        missing
    }

    fn intent_connection_json(intent: &IntentEvent, reason: &str) -> String {
        serde_json::to_string(&json!({
            "matched": false,
            "policy_effect": "deny",
            "policy_type": "connection_required",
            "policy_mode": "Enforce",
            "reason": reason,
            "intent_source_agent": Self::normalize_edge_value(&intent.source_agent),
            "intent_source_layer": Self::normalize_edge_value(&intent.source_layer),
            "intent_destination_agent": Self::normalize_edge_value(&intent.destination_agent),
            "intent_destination_layer": Self::normalize_edge_value(&intent.destination_layer),
        }))
        .unwrap_or_default()
    }

    fn connection_missing_evidence(intent: &IntentEvent, missing: &[&'static str]) -> RuleEvidence {
        let details = format!(
            "Intent is missing required Prism edge field(s): {}. Prism requires source_agent, source_layer, destination_agent, and destination_layer for policy matching; denied fail-closed.",
            missing.join(", ")
        );
        Self::build_connection_fail_closed_evidence(
            "missing-intent-edge",
            "Missing Prism Intent Edge",
            &details,
            Self::intent_connection_json(intent, &details),
            json!({
                "condition_type": "connection_match",
                "operator": "required_intent_edge_fields_present",
                "passed": false,
                "target_field": "source_agent/source_layer/destination_agent/destination_layer",
                "expected_value": ["source_agent", "source_layer", "destination_agent", "destination_layer"],
                "actual_value": {
                    "source_agent": Self::normalize_edge_value(&intent.source_agent),
                    "source_layer": Self::normalize_edge_value(&intent.source_layer),
                    "destination_agent": Self::normalize_edge_value(&intent.destination_agent),
                    "destination_layer": Self::normalize_edge_value(&intent.destination_layer),
                },
                "missing_fields": missing,
                "details": details,
            }),
        )
    }

    fn endpoint(&self, path: &str) -> String {
        let trimmed = path.trim_start_matches('/');
        format!("{}/{}", self.encoding_endpoint, trimmed)
    }

    /// Create a new enforcement engine
    pub fn new(bridge: Arc<Bridge>, encoding_endpoint: String) -> Self {
        Self::with_telemetry(bridge, encoding_endpoint, None).unwrap()
    }

    /// Create enforcement engine with telemetry enabled
    pub fn with_telemetry(
        bridge: Arc<Bridge>,
        encoding_endpoint: String,
        telemetry: Option<Arc<TelemetryRecorder>>,
    ) -> Result<Self, String> {
        let sanitized_endpoint = encoding_endpoint.trim_end_matches('/').to_string();

        let http_client = Client::builder()
            .connect_timeout(Duration::from_millis(CONNECT_TIMEOUT_MS))
            .timeout(Duration::from_millis(REQUEST_TIMEOUT_MS))
            .build()
            .map_err(|e| format!("Failed to build HTTP client: {}", e))?;

        Ok(EnforcementEngine {
            bridge,
            encoding_endpoint: sanitized_endpoint,
            http_client,
            telemetry,
        })
    }

    /// Enforce rules against an IntentEvent
    ///
    /// This is the main entry point for enforcement. It:
    /// 1. Encodes the intent to 128d vector (via Management Plane)
    /// 2. Queries rules for the specified layer from Bridge
    /// 3. Evaluates each rule with OR semantics (first ALLOW match stops; fail-closed if none match)
    /// 4. Records complete telemetry to hitlog
    /// 5. Returns enforcement decision with evidence
    pub async fn enforce(
        &self,
        intent_json: &str,
        vector_override: Option<[f32; 128]>,
        request_id: &str,
        drift_score: f32,
    ) -> Result<EnforcementResult, String> {
        let session_start = Instant::now();

        // Parse IntentEvent JSON
        let intent: IntentEvent = serde_json::from_str(intent_json)
            .map_err(|e| format!("Failed to parse IntentEvent: {}", e))?;

        // Layer is optional — default to "" which get_rules_for_layer treats as "match all".
        let layer = intent.layer_str().unwrap_or("");
        let tenant_id = intent.tenant_id.clone();
        let actor_id = intent.actor.id.clone();

        log::info!("Enforcing intent for layer: {}", layer);

        // Start telemetry session (uses request_id as session_id if non-empty)
        let session_id = self
            .telemetry
            .as_ref()
            .and_then(|t| t.start_session(layer.to_string(), intent_json.to_string(), request_id));

        // Populate agent_id and tenant_id from IntentEvent
        if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
            telemetry.with_session(sid, |session| {
                // Set tenant_id from intent
                session.tenant_id = Some(intent.tenant_id.clone());

                // Set agent_id from rate_limit_context if available
                if let Some(ref rate_limit) = intent.rate_limit_context {
                    session.agent_id = Some(rate_limit.agent_id.clone());
                }
            });
        }

        // Record intent received event
        if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
            telemetry.with_session(sid, |session| {
                session.add_event(SessionEvent::IntentReceived {
                    timestamp_us: EnforcementSession::timestamp_us(),
                    intent_id: intent.id.clone(),
                    layer: layer.to_string(),
                });
            });
        }

        let missing_edge_fields = Self::missing_intent_edge_fields(&intent);
        if !missing_edge_fields.is_empty() {
            let reason = format!(
                "Intent is missing required Prism edge field(s): {}. Prism denied the intent fail-closed before policy evaluation.",
                missing_edge_fields.join(", ")
            );
            log::warn!("{}", reason);

            if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
                telemetry.with_session(sid, |session| {
                    session.add_event(SessionEvent::NoRulesFound {
                        timestamp_us: EnforcementSession::timestamp_us(),
                        layer: layer.to_string(),
                        reason: Some(reason.clone()),
                    });
                });

                let total_duration = session_start.elapsed().as_micros() as u64;
                telemetry.complete_session(sid, 0, total_duration).ok();
            }

            return Ok(EnforcementResult {
                decision: 0,
                slice_similarities: [0.0; 4],
                rules_evaluated: 0,
                evidence: vec![Self::connection_missing_evidence(
                    &intent,
                    &missing_edge_fields,
                )],
                session_id: session_id.clone().unwrap_or_else(|| request_id.to_string()),
                enforcement_decision: Some(EnforcementDecision {
                    decision: Decision::Deny,
                    modified_params: None,
                    drift_triggered: false,
                }),
                evaluation_mode: "connection".to_string(),
                reason,
            });
        }

        // 1. Query rules for this layer from Bridge using exact path filtering first
        let query_start = Instant::now();
        let dry_run_rule_ids = Self::extract_dry_run_rule_ids(intent.context.as_ref());
        let rule_query = self.get_rules_for_layer(
            layer,
            &actor_id,
            &tenant_id,
            &intent,
            dry_run_rule_ids.as_ref(),
        )?;
        if !rule_query.invalid.is_empty() {
            let reason = format!(
                "{} active policy candidate(s) for layer {} have missing or invalid Prism edge metadata; Prism denied the intent fail-closed.",
                rule_query.invalid.len(),
                layer
            );
            log::warn!("{}", reason);

            if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
                telemetry.with_session(sid, |session| {
                    session.add_event(SessionEvent::Error {
                        timestamp_us: EnforcementSession::timestamp_us(),
                        error: reason.clone(),
                        fail_closed: true,
                    });
                });

                let total_duration = session_start.elapsed().as_micros() as u64;
                telemetry.complete_session(sid, 0, total_duration).ok();
            }

            return Ok(EnforcementResult {
                decision: 0,
                slice_similarities: [0.0; 4],
                rules_evaluated: 0,
                evidence: vec![Self::build_connection_fail_closed_evidence(
                    "invalid-policy-edge",
                    "Invalid Prism Policy Edge",
                    &reason,
                    Self::intent_connection_json(&intent, &reason),
                    json!({
                        "condition_type": "connection_match",
                        "operator": "required_policy_edge_fields_present",
                        "passed": false,
                        "target_field": "connection_match.source_agent/source_layer/destination_agent/destination_layer",
                        "expected_value": ["source_agent", "source_layer", "destination_agent", "destination_layer"],
                        "actual_value": null,
                        "invalid_policy_count": rule_query.invalid.len(),
                        "invalid_policies": rule_query.invalid.iter().map(|item| json!({
                            "rule_id": item.rule_id.clone(),
                            "rule_name": item.rule_name.clone(),
                            "reason": item.reason.clone(),
                            "connection_result": serde_json::from_str::<Value>(&item.connection_result_json).unwrap_or_else(|_| json!({})),
                        })).collect::<Vec<Value>>(),
                        "details": reason,
                    }),
                )],
                session_id: session_id.clone().unwrap_or_else(|| request_id.to_string()),
                enforcement_decision: Some(EnforcementDecision {
                    decision: Decision::Deny,
                    modified_params: None,
                    drift_triggered: false,
                }),
                evaluation_mode: "connection".to_string(),
                reason,
            });
        }
        let rules = rule_query.rules;
        let query_duration = query_start.elapsed().as_micros() as u64;

        if rules.is_empty() {
            // No rules = fail-closed (BLOCK)
            let reason = if rule_query.candidate_count == 0 {
                format!(
                    "No active policies are configured for layer {}; Prism denied the intent by default.",
                    layer
                )
            } else {
                format!(
                    "No active policies matched Prism edge {}:{} -> {}:{} on layer {}; Prism denied the intent fail-closed.",
                    Self::normalize_edge_value(&intent.source_agent).unwrap_or_default(),
                    Self::normalize_edge_value(&intent.source_layer).unwrap_or_default(),
                    Self::normalize_edge_value(&intent.destination_agent).unwrap_or_default(),
                    Self::normalize_edge_value(&intent.destination_layer).unwrap_or_default(),
                    layer,
                )
            };
            log::info!(
                "No edge-matching rules configured for layer {}, blocking by default",
                layer
            );

            // Record no rules found
            if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
                telemetry.with_session(sid, |session| {
                    session.add_event(SessionEvent::NoRulesFound {
                        timestamp_us: EnforcementSession::timestamp_us(),
                        layer: layer.to_string(),
                        reason: Some(reason.clone()),
                    });
                });

                let total_duration = session_start.elapsed().as_micros() as u64;
                telemetry.complete_session(sid, 0, total_duration).ok();
            }

            return Ok(EnforcementResult {
                decision: 0,
                slice_similarities: [0.0; 4],
                rules_evaluated: 0,
                evidence: vec![if rule_query.candidate_count == 0 {
                    Self::build_fail_closed_evidence(
                        "implicit-default-deny",
                        "Implicit Default Deny",
                        &reason,
                    )
                } else {
                    Self::build_connection_fail_closed_evidence(
                        "no-edge-matched-policy",
                        "No Edge-Matched Prism Policy",
                        &reason,
                        Self::intent_connection_json(&intent, &reason),
                        json!({
                            "condition_type": "connection_match",
                            "operator": "exact_source_destination_edge",
                            "passed": false,
                            "target_field": "source_agent/source_layer/destination_agent/destination_layer",
                            "expected_value": "at least one active policy with matching connection_match",
                            "actual_value": {
                                "source_agent": Self::normalize_edge_value(&intent.source_agent),
                                "source_layer": Self::normalize_edge_value(&intent.source_layer),
                                "destination_agent": Self::normalize_edge_value(&intent.destination_agent),
                                "destination_layer": Self::normalize_edge_value(&intent.destination_layer),
                            },
                            "candidate_policy_count": rule_query.candidate_count,
                            "skipped_policies": rule_query.skipped.iter().map(|item| json!({
                                "rule_id": item.rule_id.clone(),
                                "rule_name": item.rule_name.clone(),
                                "reason": item.reason.clone(),
                                "connection_result": serde_json::from_str::<Value>(&item.connection_result_json).unwrap_or_else(|_| json!({})),
                            })).collect::<Vec<Value>>(),
                            "details": reason,
                        }),
                    )
                }],
                session_id: session_id.clone().unwrap_or_else(|| request_id.to_string()),
                enforcement_decision: Some(EnforcementDecision {
                    decision: Decision::Deny,
                    modified_params: None,
                    drift_triggered: false,
                }),
                evaluation_mode: if rule_query.candidate_count == 0 {
                    "semantic".to_string()
                } else {
                    "connection".to_string()
                },
                reason,
            });
        }

        let rules_count = rules.len();
        log::info!("Found {} rules for layer {}", rules_count, layer);

        // Record rules queried
        if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
            telemetry.with_session(sid, |session| {
                session.add_event(SessionEvent::RulesQueried {
                    timestamp_us: EnforcementSession::timestamp_us(),
                    layer: layer.to_string(),
                    rule_count: rules_count,
                    query_duration_us: query_duration,
                });
                session.performance.rule_query_duration_us = query_duration;
                session.performance.rules_queried = rules_count;
            });
        }

        // 2. Encode intent only if one of the applicable rules still needs semantic matching.
        // This includes condition-local semantic guards/allow anchors. Guard-only
        // policies intentionally have no PolicyMatch anchors, but still require
        // the payload embedding for semantic condition evaluation.
        let requires_semantic_encoding = rules.iter().any(|rule| self.rule_requires_semantic(rule));
        let encoding_start = Instant::now();

        if requires_semantic_encoding {
            if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
                telemetry.with_session(sid, |session| {
                    session.add_event(SessionEvent::EncodingStarted {
                        timestamp_us: EnforcementSession::timestamp_us(),
                    });
                });
            }
        }

        let (intent_vector, encoding_duration, vector_norm) = if requires_semantic_encoding {
            if let Some(vector) = vector_override {
                let norm = vector.iter().map(|v| v * v).sum::<f32>().sqrt();
                (Some(vector), 0u64, norm)
            } else {
                match self.encode_intent(intent_json).await {
                    Ok(vector) => {
                        let duration = encoding_start.elapsed().as_micros() as u64;
                        let norm = vector.iter().map(|v| v * v).sum::<f32>().sqrt();
                        (Some(vector), duration, norm)
                    }
                    Err(err) => {
                        let reason = format!(
                            "Semantic intent encoding failed; Prism denied the intent by default. Error: {}",
                            err
                        );
                        log::info!(
                            "Intent encoding failed: {}. Blocking intent (fail-closed).",
                            err
                        );

                        if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id)
                        {
                            telemetry.with_session(sid, |session| {
                                session.add_event(SessionEvent::EncodingFailed {
                                    timestamp_us: EnforcementSession::timestamp_us(),
                                    error: err.clone(),
                                });
                                session.error = Some(err.clone());
                            });

                            let total_duration = session_start.elapsed().as_micros() as u64;
                            telemetry.complete_session(sid, 0, total_duration).ok();
                        }

                        return Ok(EnforcementResult {
                            decision: 0,
                            slice_similarities: [0.0; 4],
                            rules_evaluated: 0,
                            evidence: vec![Self::build_fail_closed_evidence(
                                "semantic-evaluation-error",
                                "Semantic Evaluation Failed",
                                &reason,
                            )],
                            session_id: session_id
                                .clone()
                                .unwrap_or_else(|| request_id.to_string()),
                            enforcement_decision: Some(EnforcementDecision {
                                decision: Decision::Deny,
                                modified_params: None,
                                drift_triggered: false,
                            }),
                            evaluation_mode: "semantic".to_string(),
                            reason,
                        });
                    }
                }
            }
        } else {
            (None, 0u64, 0.0)
        };

        if requires_semantic_encoding {
            if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
                telemetry.with_session(sid, |session| {
                    session.add_event(SessionEvent::EncodingCompleted {
                        timestamp_us: EnforcementSession::timestamp_us(),
                        duration_us: encoding_duration,
                        vector_norm,
                    });
                    session.intent_vector = intent_vector.map(|vector| vector.to_vec());
                    session.performance.encoding_duration_us = encoding_duration;
                });
            }
        }

        // 3. Evaluate rules.
        //    All passes operate on the same rule set, partitioned by policy_type.
        let mut evidence = Vec::new();
        let evaluation_start = Instant::now();

        // Partition rules by policy_type (rules are already sorted by priority desc
        // from get_rules_for_layer; each partition preserves that order).
        let mut forbidden_rules: Vec<&Arc<dyn RuleInstance>> = Vec::new();
        let mut context_deny_rules: Vec<&Arc<dyn RuleInstance>> = Vec::new();
        let mut context_allow_rules: Vec<&Arc<dyn RuleInstance>> = Vec::new();
        let mut context_defer_rules: Vec<&Arc<dyn RuleInstance>> = Vec::new();

        for rule in &rules {
            match rule.policy_type() {
                PolicyType::Forbidden => forbidden_rules.push(rule),
                PolicyType::ContextDeny => context_deny_rules.push(rule),
                PolicyType::ContextAllow => context_allow_rules.push(rule),
                PolicyType::ContextDefer => context_defer_rules.push(rule),
            }
        }

        // Helper closure: evaluate a single rule vector comparison and record telemetry.
        // Returns the comparison plus condition-level veto metadata or an Err.
        let evaluate_rule = |rule: &Arc<dyn RuleInstance>,
                             evidence: &mut Vec<RuleEvidence>|
         -> Result<RuleEvaluationOutcome, String> {
            if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
                telemetry.with_session(sid, |session| {
                    session.add_event(SessionEvent::RuleEvaluationStarted {
                        timestamp_us: EnforcementSession::timestamp_us(),
                        rule_id: rule.rule_id().to_string(),
                        rule_priority: rule.priority(),
                    });
                });
            }

            let rule_vector = self
                .bridge
                .get_rule_anchors(rule.rule_id())
                .unwrap_or_default();
            let semantic_required = self.rule_vector_requires_semantic(&rule_vector);
            let payload = rule.management_plane_payload();
            let connection_check = self.rule_connection_check(rule, &intent);
            if !connection_check.matched {
                return Err(format!(
                    "Rule '{}' was selected for evaluation but failed Prism edge matching: {}",
                    rule.rule_id(),
                    connection_check.reason
                ));
            }
            let (deterministic_passed, deterministic_reason, deterministic_results) =
                self.evaluate_deterministic_conditions(rule, &intent)?;
            let semantic_conditions = Self::parse_semantic_conditions(&payload)?;
            let has_semantic_conditions = !semantic_conditions.is_empty();
            let evaluation_mode = if (semantic_required || has_semantic_conditions)
                && !deterministic_results.is_empty()
            {
                "hybrid".to_string()
            } else if semantic_required || has_semantic_conditions {
                "semantic".to_string()
            } else if !deterministic_results.is_empty() {
                "deterministic".to_string()
            } else {
                "semantic".to_string()
            };

            let weights = self.get_rule_weights(rule);
            let (ev_thresholds, ev_decision_mode) = self.get_rule_thresholds(rule)?;
            let (cmp, semantic_reason, semantic_results, guard_triggered) = if !deterministic_passed
            {
                (
                    ComparisonResult {
                        decision: 0,
                        slice_similarities: [0.0; 4],
                        triggering_slice_idx: 0,
                    },
                    String::new(),
                    Vec::new(),
                    false,
                )
            } else if has_semantic_conditions {
                let vector = intent_vector.ok_or_else(|| {
                    format!(
                        "Rule '{}' requires semantic evaluation but no intent vector is available",
                        rule.rule_id()
                    )
                })?;
                let (
                    semantic_passed,
                    semantic_reason,
                    semantic_similarities,
                    semantic_results,
                    guard_triggered,
                ) = self.evaluate_semantic_conditions(rule, &vector, &rule_vector)?;
                (
                    ComparisonResult {
                        decision: if semantic_passed { 1 } else { 0 },
                        slice_similarities: semantic_similarities,
                        triggering_slice_idx: 2,
                    },
                    semantic_reason,
                    semantic_results,
                    guard_triggered,
                )
            } else if !semantic_required {
                (
                    ComparisonResult {
                        decision: 1,
                        slice_similarities: [1.0; 4],
                        triggering_slice_idx: 0,
                    },
                    String::new(),
                    Vec::new(),
                    false,
                )
            } else {
                let vector = intent_vector.ok_or_else(|| {
                    format!(
                        "Rule '{}' requires semantic evaluation but no intent vector is available",
                        rule.rule_id()
                    )
                })?;
                (
                    self.compare_with_sandbox(
                        &vector,
                        &rule_vector,
                        ev_thresholds,
                        ev_decision_mode,
                        weights,
                    )?,
                    String::new(),
                    Vec::new(),
                    false,
                )
            };
            let cmp = cmp;
            let policy_similarity_score =
                Self::policy_similarity_score(&cmp, &rule_vector, ev_decision_mode, weights);
            let policy_drift_score = (1.0 - policy_similarity_score).clamp(0.0, 1.0);
            let rule_eval_duration = 0u64; // timing not re-measured in closure for simplicity

            let slice_names = ["action", "resource", "data", "risk"];
            let triggering_slice = if evaluation_mode == "deterministic" {
                "deterministic".to_string()
            } else if has_semantic_conditions {
                "semantic".to_string()
            } else if semantic_required {
                slice_names[cmp.triggering_slice_idx].to_string()
            } else {
                "deterministic".to_string()
            };

            let scoring_mode = match ev_decision_mode {
                DecisionMode::WeightedAvgMode => "weighted-avg".to_string(),
                DecisionMode::MinMode => "min".to_string(),
            };

            let evaluation_summary = if !deterministic_passed {
                deterministic_reason.clone()
            } else if !semantic_reason.is_empty() {
                semantic_reason.clone()
            } else {
                deterministic_reason.clone()
            };

            let mut connection_result_value =
                serde_json::from_str::<Value>(&connection_check.connection_result_json)
                    .unwrap_or_else(|_| json!({}));
            if let Some(map) = connection_result_value.as_object_mut() {
                map.insert("policy_drift_score".to_string(), json!(policy_drift_score));
                map.insert(
                    "policy_similarity_score".to_string(),
                    json!(policy_similarity_score),
                );
                map.insert("baseline_drift_score".to_string(), json!(drift_score));
                map.insert("drift_source".to_string(), json!("policy"));
            }

            evidence.push(RuleEvidence {
                rule_id: rule.rule_id().to_string(),
                rule_name: rule.description().unwrap_or("").to_string(),
                decision: cmp.decision,
                similarities: cmp.slice_similarities,
                triggering_slice,
                anchor_matched: evaluation_summary,
                thresholds: ev_thresholds,
                scoring_mode,
                evaluation_mode,
                connection_result_json: serde_json::to_string(&connection_result_value)
                    .unwrap_or_default(),
                deterministic_results_json: serde_json::to_string(&deterministic_results)
                    .unwrap_or_else(|_| "[]".to_string()),
                semantic_results_json: serde_json::to_string(&semantic_results)
                    .unwrap_or_else(|_| "[]".to_string()),
            });

            if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
                let thresholds = ev_thresholds;
                let slice_details = self.build_slice_details(&cmp, &thresholds);
                let rule_family = payload
                    .get("rule_type")
                    .and_then(|v| v.as_str())
                    .unwrap_or("design_boundary")
                    .to_string();

                telemetry.with_session(sid, |session| {
                    session.add_event(SessionEvent::RuleEvaluationCompleted {
                        timestamp_us: EnforcementSession::timestamp_us(),
                        rule_id: rule.rule_id().to_string(),
                        decision: cmp.decision,
                        similarities: cmp.slice_similarities,
                        duration_us: rule_eval_duration,
                    });
                    session.add_rule_evaluation(RuleEvaluationEvent {
                        rule_id: rule.rule_id().to_string(),
                        rule_family,
                        priority: rule.priority(),
                        description: rule.description().map(|s| s.to_string()),
                        started_at_us: EnforcementSession::timestamp_us(),
                        duration_us: rule_eval_duration,
                        decision: cmp.decision,
                        slice_similarities: cmp.slice_similarities,
                        thresholds,
                        anchor_counts: [
                            rule_vector.action_count,
                            rule_vector.resource_count,
                            rule_vector.data_count,
                            rule_vector.risk_count,
                        ],
                        short_circuited: false,
                        slice_details,
                    });
                });
            }

            Ok(RuleEvaluationOutcome {
                comparison: cmp,
                policy_drift_score,
                guard_triggered,
            })
        };

        // Helper: record final decision in telemetry and return EnforcementResult.
        let finish = |evidence: Vec<RuleEvidence>,
                      enforcement_decision: EnforcementDecision,
                      final_similarities: [f32; 4],
                      evaluation_start: Instant,
                      session_start: Instant,
                      telemetry: &Option<Arc<TelemetryRecorder>>,
                      session_id: &Option<String>,
                      request_id: &str|
         -> EnforcementResult {
            let legacy_decision: u8 = match enforcement_decision.decision {
                Decision::Allow | Decision::Modify => 1,
                _ => 0,
            };
            let evaluation_duration = evaluation_start.elapsed().as_micros() as u64;
            let total_duration = session_start.elapsed().as_micros() as u64;
            let rules_evaluated = evidence.len();
            let evaluation_mode = Self::derive_overall_evaluation_mode(&evidence);
            let reason = match enforcement_decision.decision {
                Decision::Allow => "Matched an allow policy.".to_string(),
                Decision::Modify => {
                    "Matched an allow policy with parameter modification.".to_string()
                }
                Decision::StepUp => {
                    "Matched an allow policy, but policy drift exceeded the configured threshold."
                        .to_string()
                }
                Decision::Defer => "Matched a defer policy.".to_string(),
                Decision::Deny => {
                    if evidence.is_empty() {
                        "No policy evidence was returned; Prism denied the intent.".to_string()
                    } else if Self::evidence_has_guard_veto(&evidence) {
                        "A semantic guard condition vetoed the allow policy; Prism denied the intent.".to_string()
                    } else {
                        "No configured policy produced an allow outcome; Prism denied the intent fail-closed.".to_string()
                    }
                }
            };

            if let (Some(ref t), Some(ref sid)) = (telemetry, session_id) {
                t.with_session(sid, |session| {
                    session.add_event(SessionEvent::FinalDecision {
                        timestamp_us: EnforcementSession::timestamp_us(),
                        decision: legacy_decision,
                        rules_evaluated,
                        total_duration_us: total_duration,
                    });
                    session.performance.evaluation_duration_us = evaluation_duration;
                    session.final_similarities = Some(final_similarities);
                });
                t.complete_session(sid, legacy_decision, total_duration)
                    .ok();
            }

            EnforcementResult {
                decision: legacy_decision,
                slice_similarities: final_similarities,
                rules_evaluated,
                evidence,
                session_id: session_id.clone().unwrap_or_else(|| request_id.to_string()),
                enforcement_decision: Some(enforcement_decision),
                evaluation_mode,
                reason,
            }
        };

        // -----------------------------------------------------------------------
        // Pass 1 — FORBIDDEN
        //   Any match → DENY immediately. Drift is irrelevant.
        // -----------------------------------------------------------------------
        for rule in &forbidden_rules {
            let evaluated = evaluate_rule(rule, &mut evidence)?;
            let cmp = evaluated.comparison;
            if cmp.decision == 1 {
                log::info!(
                    "DENY (FORBIDDEN): rule '{}' matched — blocking immediately",
                    rule.rule_id()
                );
                let ed = EnforcementDecision {
                    decision: Decision::Deny,
                    modified_params: None,
                    drift_triggered: false,
                };
                let sims = cmp.slice_similarities;
                return Ok(finish(
                    evidence,
                    ed,
                    sims,
                    evaluation_start,
                    session_start,
                    &self.telemetry,
                    &session_id,
                    request_id,
                ));
            }
        }

        // -----------------------------------------------------------------------
        // Pass 2 — CONTEXT_DENY
        //   Match + drift exceeded → DENY with drift_triggered = true.
        //   Match + drift disabled (threshold == 0.0) → DENY, drift_triggered = false.
        // -----------------------------------------------------------------------
        for rule in &context_deny_rules {
            let evaluated = evaluate_rule(rule, &mut evidence)?;
            let cmp = evaluated.comparison;
            let policy_drift_score = evaluated.policy_drift_score;
            if cmp.decision == 1 {
                let threshold = rule.drift_threshold();
                let (drift_triggered, deny) = if threshold > 0.0 {
                    (
                        policy_drift_score > threshold,
                        policy_drift_score > threshold,
                    )
                } else {
                    // threshold == 0.0 → always deny on match
                    (false, true)
                };
                if deny {
                    log::info!(
                        "DENY (CONTEXT_DENY): rule '{}' matched (drift_triggered={})",
                        rule.rule_id(),
                        drift_triggered
                    );
                    let ed = EnforcementDecision {
                        decision: Decision::Deny,
                        modified_params: None,
                        drift_triggered,
                    };
                    let sims = cmp.slice_similarities;
                    return Ok(finish(
                        evidence,
                        ed,
                        sims,
                        evaluation_start,
                        session_start,
                        &self.telemetry,
                        &session_id,
                        request_id,
                    ));
                }
            }
        }

        // -----------------------------------------------------------------------
        // Pass 3 — CONTEXT_ALLOW
        //   Match + drift exceeded → STEP_UP.
        //   Match + modification_spec present → MODIFY.
        //   Match otherwise → ALLOW.
        // -----------------------------------------------------------------------
        for rule in &context_allow_rules {
            let evaluated = evaluate_rule(rule, &mut evidence)?;
            let cmp = evaluated.comparison;
            let policy_drift_score = evaluated.policy_drift_score;
            if evaluated.guard_triggered {
                let payload = rule.management_plane_payload();
                let policy_mode = Self::rule_policy_mode(&payload);
                let sims = cmp.slice_similarities;
                if policy_mode.eq_ignore_ascii_case("monitor") {
                    log::info!(
                        "ALLOW (MONITOR GUARD): rule '{}' guard triggered but policy is monitor",
                        rule.rule_id()
                    );
                    let ed = EnforcementDecision {
                        decision: Decision::Allow,
                        modified_params: None,
                        drift_triggered: false,
                    };
                    return Ok(finish(
                        evidence,
                        ed,
                        sims,
                        evaluation_start,
                        session_start,
                        &self.telemetry,
                        &session_id,
                        request_id,
                    ));
                }

                log::info!(
                    "DENY (CONTEXT_ALLOW_GUARD): rule '{}' guard vetoed allow policy",
                    rule.rule_id()
                );
                let ed = EnforcementDecision {
                    decision: Decision::Deny,
                    modified_params: None,
                    drift_triggered: false,
                };
                return Ok(finish(
                    evidence,
                    ed,
                    sims,
                    evaluation_start,
                    session_start,
                    &self.telemetry,
                    &session_id,
                    request_id,
                ));
            }
            if cmp.decision == 1 {
                let threshold = rule.drift_threshold();
                let sims = cmp.slice_similarities;

                if threshold > 0.0 && policy_drift_score > threshold {
                    log::info!(
                        "STEP_UP (CONTEXT_ALLOW): rule '{}' matched but drift exceeded threshold",
                        rule.rule_id()
                    );
                    let ed = EnforcementDecision {
                        decision: Decision::StepUp,
                        modified_params: None,
                        drift_triggered: true,
                    };
                    return Ok(finish(
                        evidence,
                        ed,
                        sims,
                        evaluation_start,
                        session_start,
                        &self.telemetry,
                        &session_id,
                        request_id,
                    ));
                } else if let Some(spec) = rule.modification_spec() {
                    log::info!(
                        "MODIFY (CONTEXT_ALLOW): rule '{}' matched with modification_spec",
                        rule.rule_id()
                    );
                    let ed = EnforcementDecision {
                        decision: Decision::Modify,
                        modified_params: Some(spec.clone()),
                        drift_triggered: false,
                    };
                    return Ok(finish(
                        evidence,
                        ed,
                        sims,
                        evaluation_start,
                        session_start,
                        &self.telemetry,
                        &session_id,
                        request_id,
                    ));
                } else {
                    log::info!("ALLOW (CONTEXT_ALLOW): rule '{}' matched", rule.rule_id());
                    let ed = EnforcementDecision {
                        decision: Decision::Allow,
                        modified_params: None,
                        drift_triggered: false,
                    };
                    return Ok(finish(
                        evidence,
                        ed,
                        sims,
                        evaluation_start,
                        session_start,
                        &self.telemetry,
                        &session_id,
                        request_id,
                    ));
                }
            }
        }

        // -----------------------------------------------------------------------
        // Pass 4 — CONTEXT_DEFER
        //   Any match → DEFER.
        // -----------------------------------------------------------------------
        for rule in &context_defer_rules {
            let evaluated = evaluate_rule(rule, &mut evidence)?;
            let cmp = evaluated.comparison;
            if cmp.decision == 1 {
                log::info!("DEFER (CONTEXT_DEFER): rule '{}' matched", rule.rule_id());
                let ed = EnforcementDecision {
                    decision: Decision::Defer,
                    modified_params: None,
                    drift_triggered: false,
                };
                let sims = cmp.slice_similarities;
                return Ok(finish(
                    evidence,
                    ed,
                    sims,
                    evaluation_start,
                    session_start,
                    &self.telemetry,
                    &session_id,
                    request_id,
                ));
            }
        }

        // -----------------------------------------------------------------------
        // Pass 5 — FAIL CLOSED
        //   No rule matched in any pass → DENY.
        // -----------------------------------------------------------------------
        log::info!("DENY (FAIL-CLOSED): No rules matched for layer {}", layer);

        let avg_similarities = Self::average_similarities(&evidence);
        let ed = EnforcementDecision {
            decision: Decision::Deny,
            modified_params: None,
            drift_triggered: false,
        };
        Ok(finish(
            evidence,
            ed,
            avg_similarities,
            evaluation_start,
            session_start,
            &self.telemetry,
            &session_id,
            request_id,
        ))
    }

    /// Encode intent to 128d vector by calling Management Plane
    async fn encode_intent(&self, intent_json: &str) -> Result<[f32; 128], String> {
        let url = self.endpoint("/encode/intent");

        let response = self
            .http_client
            .post(url)
            .header(CONTENT_TYPE, "application/json")
            .body(intent_json.to_owned())
            .send()
            .await
            .map_err(|e| format!("Failed to call Management Plane /encode/intent: {e}"))?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response
                .text()
                .await
                .unwrap_or_else(|_| "<unavailable>".to_string());
            return Err(format!(
                "/encode/intent returned {} (fail-closed): {}",
                status, body
            ));
        }

        let payload: IntentEncodingResponse = response
            .json()
            .await
            .map_err(|e| format!("Failed to parse /encode/intent response: {e}"))?;

        if payload.vector.len() != 128 {
            return Err(format!(
                "Management Plane returned {}-dim vector, expected 128",
                payload.vector.len()
            ));
        }

        let mut vector = [0f32; 128];
        vector.copy_from_slice(&payload.vector);
        Ok(vector)
    }

    /// Query rules for a specific layer from Bridge
    fn get_rules_for_layer(
        &self,
        layer: &str,
        actor_id: &str,
        tenant_id: &str,
        intent: &IntentEvent,
        dry_run_rule_ids: Option<&HashSet<String>>,
    ) -> Result<RuleQueryResult, String> {
        log::info!("Querying rules for layer: {}", layer);

        let requested_layer = if layer.is_empty() { None } else { Some(layer) };

        let candidates: Vec<_> = self
            .bridge
            .all_rules()
            .into_iter()
            .filter(|rule| rule.is_enabled())
            .filter(|rule| match dry_run_rule_ids {
                Some(rule_ids) if !rule_ids.is_empty() => rule_ids.contains(rule.rule_id()),
                _ => rule.scope().applies_to(actor_id) || rule.scope().applies_to(tenant_id),
            })
            .filter(|rule| match (rule.layer(), requested_layer) {
                (None, _) => true,
                (Some(rule_layer), Some(requested)) => rule_layer == requested,
                (Some(_), None) => false,
            })
            .collect();

        let candidate_count = candidates.len();
        let mut filtered = Vec::new();
        let mut skipped = Vec::new();
        let mut invalid = Vec::new();

        for rule in candidates {
            let check = self.rule_connection_check(&rule, intent);
            if check.matched {
                filtered.push(rule);
            } else if check.invalid_policy_edge {
                invalid.push(RuleConnectionSkip {
                    rule_id: rule.rule_id().to_string(),
                    rule_name: rule.description().unwrap_or("").to_string(),
                    reason: check.reason,
                    connection_result_json: check.connection_result_json,
                });
            } else {
                skipped.push(RuleConnectionSkip {
                    rule_id: rule.rule_id().to_string(),
                    rule_name: rule.description().unwrap_or("").to_string(),
                    reason: check.reason,
                    connection_result_json: check.connection_result_json,
                });
            }
        }

        filtered.sort_by(|a, b| b.priority().cmp(&a.priority()));

        log::info!(
            "Found {} edge-matching rules for layer {} ({} layer candidates, actor: {}, tenant: {})",
            filtered.len(),
            layer,
            candidate_count,
            actor_id,
            tenant_id
        );
        Ok(RuleQueryResult {
            rules: filtered,
            candidate_count,
            skipped,
            invalid,
        })
    }

    fn rule_policy_mode(payload: &Value) -> String {
        payload
            .get("policy_mode")
            .and_then(|value| value.as_str())
            .unwrap_or("Enforce")
            .to_string()
    }

    fn rule_policy_type(payload: &Value) -> String {
        payload
            .get("policy_type")
            .and_then(|value| value.as_str())
            .unwrap_or("context_allow")
            .to_string()
    }

    fn rule_policy_effect(rule: &Arc<dyn RuleInstance>) -> String {
        match rule.policy_type() {
            PolicyType::Forbidden | PolicyType::ContextDeny => "deny".to_string(),
            PolicyType::ContextAllow => "allow".to_string(),
            PolicyType::ContextDefer => "defer".to_string(),
        }
    }

    fn build_connection_evaluation(
        intent: &IntentEvent,
        policy_connection: Option<&ConnectionMatchPayload>,
        matched: bool,
        policy_mode: String,
        policy_type: String,
        policy_effect: String,
        reason: String,
    ) -> String {
        let fallback = ConnectionMatchPayload {
            source_agent: String::new(),
            source_layer: String::new(),
            destination_agent: String::new(),
            destination_layer: String::new(),
        };
        let connection = policy_connection.unwrap_or(&fallback);
        serde_json::to_string(&ConnectionEvaluationPayload {
            matched,
            source_agent: connection.source_agent.clone(),
            source_layer: connection.source_layer.clone(),
            destination_agent: connection.destination_agent.clone(),
            destination_layer: connection.destination_layer.clone(),
            intent_source_agent: Self::normalize_edge_value(&intent.source_agent),
            intent_source_layer: Self::normalize_edge_value(&intent.source_layer),
            intent_destination_agent: Self::normalize_edge_value(&intent.destination_agent),
            intent_destination_layer: Self::normalize_edge_value(&intent.destination_layer),
            policy_mode,
            policy_type,
            policy_effect,
            reason,
        })
        .unwrap_or_default()
    }

    fn rule_connection_check(
        &self,
        rule: &Arc<dyn RuleInstance>,
        intent: &IntentEvent,
    ) -> RuleConnectionCheck {
        let payload = rule.management_plane_payload();
        let policy_mode = Self::rule_policy_mode(&payload);
        let policy_type = Self::rule_policy_type(&payload);
        let policy_effect = Self::rule_policy_effect(rule);

        let connection_match = match Self::parse_connection_match(&payload) {
            Ok(value) => value,
            Err(reason) => {
                return RuleConnectionCheck {
                    matched: false,
                    invalid_policy_edge: true,
                    connection_result_json: Self::build_connection_evaluation(
                        intent,
                        None,
                        false,
                        policy_mode,
                        policy_type,
                        policy_effect,
                        reason.clone(),
                    ),
                    reason,
                };
            }
        };

        let source_agent = Self::normalize_edge_value(&intent.source_agent).unwrap_or_default();
        let source_layer = Self::normalize_edge_value(&intent.source_layer).unwrap_or_default();
        let destination_agent =
            Self::normalize_edge_value(&intent.destination_agent).unwrap_or_default();
        let destination_layer =
            Self::normalize_edge_value(&intent.destination_layer).unwrap_or_default();

        let matched = source_agent == connection_match.source_agent
            && source_layer == connection_match.source_layer
            && destination_agent == connection_match.destination_agent
            && destination_layer == connection_match.destination_layer;

        let reason = if matched {
            format!(
                "Intent edge matched policy connection {}:{} -> {}:{}.",
                connection_match.source_agent,
                connection_match.source_layer,
                connection_match.destination_agent,
                connection_match.destination_layer
            )
        } else {
            format!(
                "Intent edge {}:{} -> {}:{} did not match policy connection {}:{} -> {}:{}.",
                source_agent,
                source_layer,
                destination_agent,
                destination_layer,
                connection_match.source_agent,
                connection_match.source_layer,
                connection_match.destination_agent,
                connection_match.destination_layer
            )
        };

        RuleConnectionCheck {
            matched,
            invalid_policy_edge: false,
            connection_result_json: Self::build_connection_evaluation(
                intent,
                Some(&connection_match),
                matched,
                policy_mode,
                policy_type,
                policy_effect,
                reason.clone(),
            ),
            reason,
        }
    }

    fn extract_dry_run_rule_ids(context: Option<&Value>) -> Option<HashSet<String>> {
        let ids = context
            .and_then(|ctx| ctx.get("dry_run_rule_ids"))
            .and_then(|value| value.as_array())
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| item.as_str().map(|s| s.to_string()))
                    .collect::<HashSet<String>>()
            })
            .unwrap_or_default();

        if ids.is_empty() {
            None
        } else {
            Some(ids)
        }
    }

    /// Compare intent vector against rule anchors using direct in-process comparison
    fn compare_with_sandbox(
        &self,
        intent_vector: &[f32; 128],
        rule_vector: &RuleVector,
        thresholds: [f32; 4],
        decision_mode: DecisionMode,
        weights: [f32; 4],
    ) -> Result<ComparisonResult, String> {
        Ok(compare_intent_vs_rule(
            intent_vector,
            rule_vector,
            thresholds,
            decision_mode,
            weights,
        ))
    }

    /// Get slice weights from a rule instance
    fn get_rule_weights(&self, rule: &Arc<dyn RuleInstance>) -> [f32; 4] {
        rule.slice_weights()
    }

    fn rule_vector_requires_semantic(&self, rule_vector: &RuleVector) -> bool {
        rule_vector.action_count > 0
            || rule_vector.resource_count > 0
            || rule_vector.data_count > 0
            || rule_vector.risk_count > 0
    }

    fn payload_has_semantic_conditions(payload: &Value) -> bool {
        match Self::parse_semantic_conditions(payload) {
            Ok(conditions) => !conditions.is_empty(),
            Err(_) => payload.get("semantic_conditions").is_some(),
        }
    }

    fn policy_similarity_score(
        cmp: &ComparisonResult,
        rule_vector: &RuleVector,
        decision_mode: DecisionMode,
        weights: [f32; 4],
    ) -> f32 {
        match decision_mode {
            DecisionMode::MinMode => {
                let counts = [
                    rule_vector.action_count,
                    rule_vector.resource_count,
                    rule_vector.data_count,
                    rule_vector.risk_count,
                ];
                let mut score: Option<f32> = None;
                for idx in 0..4 {
                    if counts[idx] > 0 {
                        score = Some(match score {
                            Some(existing) => existing.min(cmp.slice_similarities[idx]),
                            None => cmp.slice_similarities[idx],
                        });
                    }
                }
                score.unwrap_or(1.0).clamp(0.0, 1.0)
            }
            DecisionMode::WeightedAvgMode => {
                let weight_sum: f32 = weights.iter().sum();
                let weight_sum = if weight_sum < 1e-8 { 1.0 } else { weight_sum };
                let score = cmp
                    .slice_similarities
                    .iter()
                    .zip(weights.iter())
                    .map(|(similarity, weight)| similarity * weight)
                    .sum::<f32>()
                    / weight_sum;
                score.clamp(0.0, 1.0)
            }
        }
    }

    fn rule_requires_semantic(&self, rule: &Arc<dyn RuleInstance>) -> bool {
        let rule_vector_requires_semantic = self
            .bridge
            .get_rule_anchors(rule.rule_id())
            .map(|rule_vector| self.rule_vector_requires_semantic(&rule_vector))
            .unwrap_or(false);

        rule_vector_requires_semantic
            || Self::payload_has_semantic_conditions(&rule.management_plane_payload())
    }

    fn parse_connection_match(payload: &Value) -> Result<ConnectionMatchPayload, String> {
        let Some(value) = payload.get("connection_match") else {
            return Err("Policy is missing required connection_match metadata.".to_string());
        };

        let connection = if let Some(encoded) = value.as_str() {
            serde_json::from_str::<ConnectionMatchPayload>(encoded)
                .map_err(|e| format!("Policy connection_match metadata is invalid JSON: {}", e))?
        } else if value.is_object() {
            serde_json::from_value::<ConnectionMatchPayload>(value.clone())
                .map_err(|e| format!("Policy connection_match metadata is invalid: {}", e))?
        } else {
            return Err(
                "Policy connection_match metadata must be a JSON object or encoded JSON string."
                    .to_string(),
            );
        };

        let missing = [
            ("source_agent", connection.source_agent.trim()),
            ("source_layer", connection.source_layer.trim()),
            ("destination_agent", connection.destination_agent.trim()),
            ("destination_layer", connection.destination_layer.trim()),
        ]
        .iter()
        .filter_map(|(field, value)| if value.is_empty() { Some(*field) } else { None })
        .collect::<Vec<_>>();

        if !missing.is_empty() {
            return Err(format!(
                "Policy connection_match is missing required field(s): {}.",
                missing.join(", ")
            ));
        }

        Ok(ConnectionMatchPayload {
            source_agent: connection.source_agent.trim().to_string(),
            source_layer: connection.source_layer.trim().to_string(),
            destination_agent: connection.destination_agent.trim().to_string(),
            destination_layer: connection.destination_layer.trim().to_string(),
        })
    }

    fn parse_deterministic_conditions(
        payload: &Value,
    ) -> Result<Vec<DeterministicConditionPayload>, String> {
        let Some(value) = payload.get("deterministic_conditions") else {
            return Ok(Vec::new());
        };

        if let Some(encoded) = value.as_str() {
            serde_json::from_str::<Vec<DeterministicConditionPayload>>(encoded)
                .map_err(|e| format!("Invalid deterministic_conditions payload: {}", e))
        } else if value.is_array() {
            serde_json::from_value::<Vec<DeterministicConditionPayload>>(value.clone())
                .map_err(|e| format!("Invalid deterministic_conditions payload: {}", e))
        } else {
            Err(
                "Invalid deterministic_conditions payload: expected JSON string or array"
                    .to_string(),
            )
        }
    }

    fn parse_semantic_conditions(payload: &Value) -> Result<Vec<SemanticConditionPayload>, String> {
        let Some(value) = payload.get("semantic_conditions") else {
            return Ok(Vec::new());
        };

        if let Some(encoded) = value.as_str() {
            serde_json::from_str::<Vec<SemanticConditionPayload>>(encoded)
                .map_err(|e| format!("Invalid semantic_conditions payload: {}", e))
        } else if value.is_array() {
            serde_json::from_value::<Vec<SemanticConditionPayload>>(value.clone())
                .map_err(|e| format!("Invalid semantic_conditions payload: {}", e))
        } else {
            Err("Invalid semantic_conditions payload: expected JSON string or array".to_string())
        }
    }

    fn evaluate_deterministic_conditions(
        &self,
        rule: &Arc<dyn RuleInstance>,
        intent: &IntentEvent,
    ) -> Result<(bool, String, Vec<DeterministicConditionResultPayload>), String> {
        let payload = rule.management_plane_payload();
        let conditions = Self::parse_deterministic_conditions(&payload)?;
        if conditions.is_empty() {
            return Ok((true, "no deterministic conditions".to_string(), Vec::new()));
        }

        let mut results = Vec::with_capacity(conditions.len());
        for condition in conditions {
            let result = match condition.condition_type.as_str() {
                "pii_regex" => self.evaluate_pii_regex(&condition, intent)?,
                "prompt_injection_regex" => {
                    self.evaluate_prompt_injection_regex(&condition, intent)?
                }
                "regex_pattern" => self.evaluate_regex_pattern(&condition, intent)?,
                "payload_size" => self.evaluate_payload_size(&condition, intent),
                "tool_name" => self.evaluate_tool_name(&condition, intent),
                "rag_source" => self.evaluate_rag_source(&condition, intent),
                "resource_identity" => self.evaluate_resource_identity(&condition, intent),
                "request_rate" => self.evaluate_request_rate(&condition, intent),
                "tool_parameter_validation" => {
                    self.evaluate_tool_parameter_validation(&condition, intent)
                }
                "input_token_count" => self.evaluate_input_token_count(&condition, intent),
                "record_count" => self.evaluate_record_count(&condition, intent),
                "output_channel" => self.evaluate_output_channel(&condition, intent),
                "data_classification" => self.evaluate_data_classification(&condition, intent),
                "aggregation_limit" => self.evaluate_aggregation_limit(&condition, intent),
                _ => DeterministicConditionResultPayload {
                    condition_type: condition.condition_type.clone(),
                    operator: condition.operator.clone(),
                    passed: false,
                    target_field: condition
                        .parameters
                        .get("target_field")
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string()),
                    actual_value: None,
                    expected_value: None,
                    details: format!(
                        "Unsupported deterministic condition type: {}",
                        condition.condition_type
                    ),
                },
            };

            let passed = result.passed;
            results.push(result);

            if !passed {
                return Ok((
                    false,
                    format!(
                        "deterministic condition failed: {}",
                        condition.condition_type
                    ),
                    results,
                ));
            }
        }

        Ok((
            true,
            "all deterministic conditions passed".to_string(),
            results,
        ))
    }

    fn evaluate_semantic_conditions(
        &self,
        rule: &Arc<dyn RuleInstance>,
        intent_vector: &[f32; 128],
        _rule_vector: &RuleVector,
    ) -> Result<
        (
            bool,
            String,
            [f32; 4],
            Vec<SemanticConditionResultPayload>,
            bool,
        ),
        String,
    > {
        let payload = rule.management_plane_payload();
        let conditions = Self::parse_semantic_conditions(&payload)?;
        if conditions.is_empty() {
            return Ok((
                true,
                "no semantic conditions".to_string(),
                [0.0; 4],
                Vec::new(),
                false,
            ));
        }

        let mut results = Vec::with_capacity(conditions.len());
        let mut aggregate_similarities = [0.0f32; 4];
        let mut any_guard_triggered = false;
        for condition in conditions {
            let (result, condition_similarities) = match condition.condition_type.as_str() {
                "prompt_attack_semantic" | "tool_call_semantic" | "tool_response_semantic" => {
                    Self::evaluate_semantic_condition_anchors(&condition, intent_vector)?
                }
                _ => (
                    SemanticConditionResultPayload {
                        condition_type: condition.condition_type.clone(),
                        operator: condition.operator.clone(),
                        passed: false,
                        target_field: condition
                            .parameters
                            .get("target_field")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string()),
                        actual_value: None,
                        expected_value: Some(condition.parameters.clone()),
                        details: format!(
                            "Unsupported semantic condition type: {}",
                            condition.condition_type
                        ),
                    },
                    [0.0; 4],
                ),
            };

            for idx in 0..4 {
                aggregate_similarities[idx] =
                    aggregate_similarities[idx].max(condition_similarities[idx]);
            }

            let passed = result.passed;
            let role = Self::semantic_condition_role(&condition);
            let guard_triggered = role == "guard" && !passed;
            any_guard_triggered = any_guard_triggered || guard_triggered;
            results.push(result);

            if guard_triggered {
                return Ok((
                    false,
                    format!("semantic guard triggered: {}", condition.condition_type),
                    aggregate_similarities,
                    results,
                    true,
                ));
            }

            if !passed {
                return Ok((
                    false,
                    format!("semantic condition failed: {}", condition.condition_type),
                    aggregate_similarities,
                    results,
                    any_guard_triggered,
                ));
            }
        }

        Ok((
            true,
            "all semantic conditions passed".to_string(),
            aggregate_similarities,
            results,
            any_guard_triggered,
        ))
    }

    fn evaluate_pii_regex(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> Result<DeterministicConditionResultPayload, String> {
        let Some(content) = intent.data.content.as_deref() else {
            return Ok(DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("payload_text".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No payload_text was provided".to_string(),
            });
        };
        let Some(patterns) = condition
            .parameters
            .get("pii_patterns")
            .and_then(|v| v.as_object())
        else {
            return Ok(DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("payload_text".to_string()),
                actual_value: Some(json!(content)),
                expected_value: None,
                details: "No pii_patterns were configured".to_string(),
            });
        };

        let mut found_match = false;
        let mut matched_pattern_name: Option<String> = None;
        for config in patterns.values() {
            if !config
                .get("enabled")
                .and_then(|v| v.as_bool())
                .unwrap_or(true)
            {
                continue;
            }
            let Some(pattern) = config.get("pattern").and_then(|v| v.as_str()) else {
                continue;
            };
            let regex = Regex::new(pattern)
                .map_err(|e| format!("Invalid PII regex '{}': {}", pattern, e))?;
            if regex.is_match(content) {
                found_match = true;
                matched_pattern_name = Some(pattern.to_string());
                break;
            }
        }

        let passed =
            matches!(condition.operator.as_str(), "not_contains" | "absent") && !found_match;
        let details = if passed {
            "No enabled PII patterns matched".to_string()
        } else if let Some(pattern) = matched_pattern_name {
            format!("Payload matched an enabled PII pattern: {}", pattern)
        } else {
            "Payload contained disallowed PII content".to_string()
        };

        Ok(DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("payload_text".to_string()),
            actual_value: Some(json!(content)),
            expected_value: Some(condition.parameters.clone()),
            details,
        })
    }

    fn evaluate_regex_pattern(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> Result<DeterministicConditionResultPayload, String> {
        let Some(content) = intent.data.content.as_deref() else {
            return Ok(DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("payload_text".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No payload_text was provided".to_string(),
            });
        };
        let (patterns, pattern_label, invalid_details) = match condition.operator.as_str() {
            "not_matches_pattern" => {
                let Some(patterns) = condition
                    .parameters
                    .get("blocked_patterns")
                    .and_then(|v| v.as_array())
                else {
                    return Ok(DeterministicConditionResultPayload {
                        condition_type: condition.condition_type.clone(),
                        operator: condition.operator.clone(),
                        passed: false,
                        target_field: Some("payload_text".to_string()),
                        actual_value: Some(json!(content)),
                        expected_value: None,
                        details: "No blocked_patterns were configured".to_string(),
                    });
                };
                (
                    patterns,
                    "blocked",
                    "Payload matched blocked pattern: {}".to_string(),
                )
            }
            "matches_pattern" => {
                let Some(patterns) = condition
                    .parameters
                    .get("allowed_patterns")
                    .and_then(|v| v.as_array())
                else {
                    return Ok(DeterministicConditionResultPayload {
                        condition_type: condition.condition_type.clone(),
                        operator: condition.operator.clone(),
                        passed: false,
                        target_field: Some("payload_text".to_string()),
                        actual_value: Some(json!(content)),
                        expected_value: None,
                        details: "No allowed_patterns were configured".to_string(),
                    });
                };
                (
                    patterns,
                    "allowed",
                    "Payload did not match any allowed pattern; closest configured match was: {}"
                        .to_string(),
                )
            }
            _ => {
                return Ok(DeterministicConditionResultPayload {
                    condition_type: condition.condition_type.clone(),
                    operator: condition.operator.clone(),
                    passed: false,
                    target_field: Some("payload_text".to_string()),
                    actual_value: Some(json!(content)),
                    expected_value: Some(condition.parameters.clone()),
                    details: format!(
                        "Unsupported operator '{}' for regex_pattern",
                        condition.operator
                    ),
                });
            }
        };

        let mut found_match = false;
        let mut matched_pattern: Option<String> = None;
        for pattern_value in patterns {
            let Some(pattern) = pattern_value.as_str() else {
                continue;
            };
            let regex = Regex::new(pattern)
                .map_err(|e| format!("Invalid blocked regex '{}': {}", pattern, e))?;
            if regex.is_match(content) {
                found_match = true;
                matched_pattern = Some(pattern.to_string());
                break;
            }
        }

        let passed = match condition.operator.as_str() {
            "not_matches_pattern" => !found_match,
            "matches_pattern" => found_match,
            _ => false,
        };
        let details = if passed {
            match condition.operator.as_str() {
                "not_matches_pattern" => "No blocked regex patterns matched".to_string(),
                "matches_pattern" => {
                    if let Some(pattern) = matched_pattern.as_ref() {
                        format!("Payload matched allowed pattern: {}", pattern)
                    } else {
                        "Payload matched an allowed regex pattern".to_string()
                    }
                }
                _ => "Regex pattern evaluation passed".to_string(),
            }
        } else if let Some(pattern) = matched_pattern {
            if condition.operator == "not_matches_pattern" {
                format!("Payload matched blocked pattern: {}", pattern)
            } else {
                invalid_details.replacen("{}", &pattern, 1)
            }
        } else {
            format!(
                "Payload did not satisfy {} regex pattern requirements",
                pattern_label
            )
        };

        Ok(DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("payload_text".to_string()),
            actual_value: Some(json!(content)),
            expected_value: Some(condition.parameters.clone()),
            details,
        })
    }

    fn evaluate_prompt_injection_regex(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> Result<DeterministicConditionResultPayload, String> {
        let Some(content) = intent.data.content.as_deref() else {
            return Ok(DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("payload_text".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No payload_text was provided".to_string(),
            });
        };
        let Some(patterns) = condition
            .parameters
            .get("patterns")
            .and_then(|v| v.as_array())
        else {
            return Ok(DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("payload_text".to_string()),
                actual_value: Some(json!(content)),
                expected_value: None,
                details: "No prompt injection patterns were configured".to_string(),
            });
        };

        let mut found_match = false;
        let mut matched_pattern: Option<String> = None;
        for pattern_value in patterns {
            let Some(pattern) = pattern_value.as_str() else {
                continue;
            };
            let regex = RegexBuilder::new(pattern)
                .case_insensitive(true)
                .build()
                .map_err(|e| format!("Invalid prompt injection regex '{}': {}", pattern, e))?;
            if regex.is_match(content) {
                found_match = true;
                matched_pattern = Some(pattern.to_string());
                break;
            }
        }

        let passed = condition.operator == "not_matches_pattern" && !found_match;
        let details = if passed {
            "No prompt injection regex patterns matched".to_string()
        } else if let Some(pattern) = matched_pattern {
            format!("Prompt matched blocked injection pattern: {}", pattern)
        } else {
            "Prompt matched a blocked injection regex".to_string()
        };

        Ok(DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("payload_text".to_string()),
            actual_value: Some(json!(content)),
            expected_value: Some(condition.parameters.clone()),
            details,
        })
    }

    fn evaluate_payload_size(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let Some(actual_size) = intent.data.size_bytes else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("payload_bytes".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No payload_bytes was provided".to_string(),
            };
        };
        let Some(max_size) = condition
            .parameters
            .get("max_size_bytes")
            .and_then(|v| v.as_u64())
        else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("payload_bytes".to_string()),
                actual_value: Some(json!(actual_size)),
                expected_value: None,
                details: "No max_size_bytes threshold was configured".to_string(),
            };
        };

        let passed = match condition.operator.as_str() {
            "less_than" => actual_size < max_size,
            "less_than_or_equal" => actual_size <= max_size,
            _ => false,
        };

        DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("payload_bytes".to_string()),
            actual_value: Some(json!(actual_size)),
            expected_value: Some(json!(max_size)),
            details: format!(
                "payload_bytes={} compared against max_size_bytes={}",
                actual_size, max_size
            ),
        }
    }

    fn evaluate_tool_name(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let Some(tool_name) = intent.tool_name.as_deref() else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("tool_name".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No tool_name was provided".to_string(),
            };
        };
        let Some(allowed_tools) = condition
            .parameters
            .get("allowed_tools")
            .and_then(|v| v.as_array())
        else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("tool_name".to_string()),
                actual_value: Some(json!(tool_name)),
                expected_value: None,
                details: "No allowed_tools were configured".to_string(),
            };
        };

        let allowed = allowed_tools
            .iter()
            .filter_map(|value| value.as_str())
            .any(|configured| configured == tool_name);

        let passed = match condition.operator.as_str() {
            "in" => allowed,
            "not_in" => !allowed,
            _ => false,
        };

        let details = if passed {
            format!(
                "tool_name='{}' satisfied tool allowlist constraint",
                tool_name
            )
        } else {
            format!(
                "tool_name='{}' did not satisfy configured tool allowlist",
                tool_name
            )
        };

        DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("tool_name".to_string()),
            actual_value: Some(json!(tool_name)),
            expected_value: Some(condition.parameters.clone()),
            details,
        }
    }

    fn evaluate_rag_source(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let Some(rag_source_id) = intent.rag_source_id.as_deref() else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("rag_source_id".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No rag_source_id was provided".to_string(),
            };
        };
        let Some(allowed_sources) = condition
            .parameters
            .get("allowed_rag_sources")
            .and_then(|v| v.as_array())
        else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("rag_source_id".to_string()),
                actual_value: Some(json!(rag_source_id)),
                expected_value: None,
                details: "No allowed_rag_sources were configured".to_string(),
            };
        };

        let allowed = allowed_sources
            .iter()
            .filter_map(|value| value.as_str())
            .any(|configured| configured == rag_source_id);

        let passed = match condition.operator.as_str() {
            "in" => allowed,
            "not_in" => !allowed,
            _ => false,
        };

        let details = if passed {
            format!(
                "rag_source_id='{}' satisfied RAG source allowlist constraint",
                rag_source_id
            )
        } else {
            format!(
                "rag_source_id='{}' did not satisfy configured RAG source allowlist",
                rag_source_id
            )
        };

        DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("rag_source_id".to_string()),
            actual_value: Some(json!(rag_source_id)),
            expected_value: Some(condition.parameters.clone()),
            details,
        }
    }

    fn evaluate_resource_identity(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let Some(identity_key) = intent.resource_identity_key.as_deref() else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("resource_identity_key".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No resource_identity_key was provided".to_string(),
            };
        };
        let Some(allowed_identities) = condition
            .parameters
            .get("allowed_identities")
            .and_then(|v| v.as_array())
        else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("resource_identity_key".to_string()),
                actual_value: Some(json!(identity_key)),
                expected_value: None,
                details: "No allowed_identities were configured".to_string(),
            };
        };

        let allowed = allowed_identities
            .iter()
            .filter_map(|value| value.as_str())
            .any(|configured| configured == identity_key);

        let passed = match condition.operator.as_str() {
            "in" => allowed,
            "not_in" => !allowed,
            _ => false,
        };

        let details = if passed {
            format!(
                "resource_identity_key='{}' satisfied resource identity allowlist constraint",
                identity_key
            )
        } else {
            format!(
                "resource_identity_key='{}' did not satisfy configured resource identity allowlist",
                identity_key
            )
        };

        DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("resource_identity_key".to_string()),
            actual_value: Some(json!(identity_key)),
            expected_value: Some(condition.parameters.clone()),
            details,
        }
    }

    fn evaluate_request_rate(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let actual_count = intent.tool_call_count.or_else(|| {
            intent
                .rate_limit_context
                .as_ref()
                .map(|ctx| ctx.call_count as u64)
        });

        let Some(actual_count) = actual_count else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("tool_call_count".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No tool_call_count was provided".to_string(),
            };
        };
        let Some(max_requests) = condition
            .parameters
            .get("max_requests")
            .and_then(|v| v.as_u64())
        else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("tool_call_count".to_string()),
                actual_value: Some(json!(actual_count)),
                expected_value: None,
                details: "No max_requests threshold was configured".to_string(),
            };
        };

        let passed = match condition.operator.as_str() {
            "less_than" => actual_count < max_requests,
            _ => false,
        };

        DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("tool_call_count".to_string()),
            actual_value: Some(json!(actual_count)),
            expected_value: Some(json!({
                "max_requests": max_requests,
                "time_window": condition.parameters.get("time_window").cloned().unwrap_or(json!(null)),
            })),
            details: format!(
                "tool_call_count={} compared against max_requests={}",
                actual_count, max_requests
            ),
        }
    }

    fn evaluate_tool_parameter_validation(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let Some(tool_name) = intent.tool_name.as_deref() else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("tool_params".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No tool_name was provided".to_string(),
            };
        };
        let Some(dangerous_actions) = condition
            .parameters
            .get("dangerous_actions")
            .and_then(|v| v.as_object())
        else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("tool_params".to_string()),
                actual_value: None,
                expected_value: None,
                details: "No dangerous_actions configuration was provided".to_string(),
            };
        };

        let Some(tool_config) = dangerous_actions.get(tool_name).and_then(|v| v.as_object()) else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: true,
                target_field: Some("tool_params".to_string()),
                actual_value: intent.tool_params.clone(),
                expected_value: Some(condition.parameters.clone()),
                details: format!(
                    "No dangerous action rules were configured for tool '{}'",
                    tool_name
                ),
            };
        };

        let blocked_operations: Vec<String> = tool_config
            .get("blocked_operations")
            .and_then(|v| v.as_array())
            .map(|items| {
                items
                    .iter()
                    .filter_map(|value| value.as_str())
                    .map(|value| value.to_lowercase())
                    .collect()
            })
            .unwrap_or_default();

        let params_text = intent
            .tool_params
            .as_ref()
            .map(|params| params.to_string())
            .unwrap_or_default()
            .to_lowercase();
        let method_text = intent.tool_method.as_deref().unwrap_or("").to_lowercase();
        let combined = format!("{} {}", method_text, params_text);

        let matched_operation = blocked_operations
            .iter()
            .find(|operation| combined.contains(operation.as_str()))
            .cloned();

        let passed = match condition.operator.as_str() {
            "not_contains" => matched_operation.is_none(),
            _ => false,
        };

        let details = if let Some(operation) = matched_operation {
            format!("Tool invocation matched blocked operation '{}'", operation)
        } else {
            "No dangerous tool operations were detected in the tool parameters".to_string()
        };

        DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("tool_params".to_string()),
            actual_value: Some(json!({
                "tool_name": tool_name,
                "tool_method": intent.tool_method,
                "tool_params": intent.tool_params,
            })),
            expected_value: Some(condition.parameters.clone()),
            details,
        }
    }

    fn evaluate_input_token_count(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let Some(actual_count) = intent.data.input_token_count else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("input_token_count".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No input_token_count was provided".to_string(),
            };
        };
        let Some(max_tokens) = condition
            .parameters
            .get("max_tokens")
            .and_then(|v| v.as_u64())
        else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("input_token_count".to_string()),
                actual_value: Some(json!(actual_count)),
                expected_value: None,
                details: "No max_tokens threshold was configured".to_string(),
            };
        };

        let passed = match condition.operator.as_str() {
            "less_than" => actual_count < max_tokens,
            "less_than_or_equal" => actual_count <= max_tokens,
            _ => false,
        };

        DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("input_token_count".to_string()),
            actual_value: Some(json!(actual_count)),
            expected_value: Some(json!(max_tokens)),
            details: format!(
                "input_token_count={} compared against max_tokens={}",
                actual_count, max_tokens
            ),
        }
    }

    fn semantic_condition_role(condition: &SemanticConditionPayload) -> String {
        if let Some(role) = condition
            .parameters
            .get("condition_role")
            .and_then(|v| v.as_str())
            .map(|value| value.trim().to_lowercase())
            .filter(|value| !value.is_empty())
        {
            if role == "guard" || role == "allow" {
                return role;
            }
        }

        condition
            .parameters
            .get("evaluation_direction")
            .and_then(|v| v.as_str())
            .map(|value| value.trim().to_lowercase())
            .filter(|value| !value.is_empty())
            .map(|value| match value.as_str() {
                "negative" => "guard".to_string(),
                "positive" if condition.operator == "similar_to_attack" => "guard".to_string(),
                "positive" if condition.condition_type == "prompt_attack_semantic" => {
                    "guard".to_string()
                }
                "positive" => "allow".to_string(),
                _ => value,
            })
            .unwrap_or_else(|| {
                if condition.operator.starts_with("not_")
                    || condition.operator == "absent"
                    || condition.operator == "similar_to_attack"
                    || condition.condition_type == "prompt_attack_semantic"
                {
                    "guard".to_string()
                } else {
                    "allow".to_string()
                }
            })
    }

    fn semantic_condition_match_operator(condition: &SemanticConditionPayload) -> String {
        condition
            .parameters
            .get("match_operator")
            .and_then(|v| v.as_str())
            .map(|value| value.trim().to_lowercase())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "similarity_gte".to_string())
    }

    fn semantic_target_slot_index(condition: &SemanticConditionPayload) -> Result<usize, String> {
        let slot = condition
            .parameters
            .get("target_slot")
            .and_then(|v| v.as_str())
            .unwrap_or("data");

        match slot {
            "action" => Ok(0),
            "resource" => Ok(1),
            "data" => Ok(2),
            "risk" => Ok(3),
            _ => Err(format!(
                "Semantic condition '{}' has invalid target_slot '{}'",
                condition.condition_type, slot
            )),
        }
    }

    fn parse_semantic_anchor_vectors(
        condition: &SemanticConditionPayload,
    ) -> Result<Vec<[f32; 32]>, String> {
        let Some(values) = condition
            .parameters
            .get("anchor_vectors")
            .and_then(|value| value.as_array())
        else {
            return Err(format!(
                "Semantic condition '{}' is missing required anchor_vectors",
                condition.condition_type
            ));
        };

        let anchor_count = condition
            .parameters
            .get("anchor_count")
            .and_then(|value| value.as_u64())
            .map(|value| value as usize)
            .unwrap_or(values.len());

        if anchor_count == 0 || values.is_empty() {
            return Err(format!(
                "Semantic condition '{}' has no configured anchor vectors",
                condition.condition_type
            ));
        }
        if values.len() < anchor_count {
            return Err(format!(
                "Semantic condition '{}' expected {} anchor vectors but only {} were provided",
                condition.condition_type,
                anchor_count,
                values.len()
            ));
        }

        let mut vectors = Vec::with_capacity(anchor_count.min(values.len()));
        for (idx, value) in values.iter().take(anchor_count).enumerate() {
            let Some(items) = value.as_array() else {
                return Err(format!(
                    "Semantic condition '{}' anchor vector {} is not an array",
                    condition.condition_type, idx
                ));
            };
            if items.len() != 32 {
                return Err(format!(
                    "Semantic condition '{}' anchor vector {} has length {}, expected 32",
                    condition.condition_type,
                    idx,
                    items.len()
                ));
            }

            let mut vector = [0.0f32; 32];
            for (item_idx, item) in items.iter().enumerate() {
                let Some(number) = item.as_f64() else {
                    return Err(format!(
                        "Semantic condition '{}' anchor vector {} item {} is not numeric",
                        condition.condition_type, idx, item_idx
                    ));
                };
                vector[item_idx] = number as f32;
            }
            vectors.push(vector);
        }

        Ok(vectors)
    }

    fn cosine_similarity_32(left: &[f32], right: &[f32; 32]) -> f32 {
        let mut dot = 0.0f32;
        let mut left_norm = 0.0f32;
        let mut right_norm = 0.0f32;
        for idx in 0..32 {
            dot += left[idx] * right[idx];
            left_norm += left[idx] * left[idx];
            right_norm += right[idx] * right[idx];
        }
        if left_norm <= 1e-8 || right_norm <= 1e-8 {
            0.0
        } else {
            dot / (left_norm.sqrt() * right_norm.sqrt())
        }
    }

    fn max_semantic_anchor_similarity(
        intent_vector: &[f32; 128],
        slot_idx: usize,
        anchors: &[[f32; 32]],
    ) -> (f32, Option<usize>) {
        let start = slot_idx * 32;
        let slot = &intent_vector[start..start + 32];
        let mut best_score = 0.0f32;
        let mut best_idx = None;
        for (idx, anchor) in anchors.iter().enumerate() {
            let score = Self::cosine_similarity_32(slot, anchor);
            if best_idx.is_none() || score > best_score {
                best_score = score;
                best_idx = Some(idx);
            }
        }
        (best_score, best_idx)
    }

    fn evaluate_semantic_condition_anchors(
        condition: &SemanticConditionPayload,
        intent_vector: &[f32; 128],
    ) -> Result<(SemanticConditionResultPayload, [f32; 4]), String> {
        let condition_role = Self::semantic_condition_role(condition);
        let match_operator = Self::semantic_condition_match_operator(condition);
        let target_slot_idx = Self::semantic_target_slot_index(condition)?;
        let target_slot = ["action", "resource", "data", "risk"][target_slot_idx];
        let similarity_threshold = condition
            .parameters
            .get("similarity_threshold")
            .and_then(|v| v.as_f64())
            .map(|v| v as f32)
            .unwrap_or_else(|| {
                if condition_role == "guard" {
                    0.52
                } else {
                    0.72
                }
            });

        let anchors = match Self::parse_semantic_anchor_vectors(condition) {
            Ok(anchors) => anchors,
            Err(details) => {
                return Ok((
                    SemanticConditionResultPayload {
                        condition_type: condition.condition_type.clone(),
                        operator: condition.operator.clone(),
                        passed: false,
                        target_field: condition
                            .parameters
                            .get("target_field")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string()),
                        actual_value: None,
                        expected_value: Some(json!({
                            "condition_role": condition_role,
                            "match_operator": match_operator,
                            "target_slot": target_slot,
                            "similarity_threshold": similarity_threshold,
                            "requires": "anchor_vectors",
                        })),
                        details,
                    },
                    [0.0; 4],
                ));
            }
        };

        let (similarity, best_anchor_idx) =
            Self::max_semantic_anchor_similarity(intent_vector, target_slot_idx, &anchors);
        let threshold_matched = match match_operator.as_str() {
            "similarity_gte" | "gte_threshold" => similarity >= similarity_threshold,
            "similarity_lt" | "lt_threshold" => similarity < similarity_threshold,
            _ => false,
        };
        let guard_triggered = condition_role == "guard" && threshold_matched;
        let passed = match condition_role.as_str() {
            "guard" => !guard_triggered,
            "allow" => threshold_matched,
            _ => false,
        };
        let mut similarities = [0.0f32; 4];
        similarities[target_slot_idx] = similarity;

        let details = match condition_role.as_str() {
            "guard" if guard_triggered => format!(
                "Semantic guard attack similarity {:.3} reached or exceeded threshold {:.3}",
                similarity, similarity_threshold
            ),
            "guard" => format!(
                "Semantic guard attack similarity {:.3} stayed below threshold {:.3}",
                similarity, similarity_threshold
            ),
            "allow" if passed => format!(
                "Semantic allow similarity {:.3} met or exceeded threshold {:.3}",
                similarity, similarity_threshold
            ),
            "allow" => format!(
                "Semantic allow similarity {:.3} stayed below threshold {:.3}",
                similarity, similarity_threshold
            ),
            _ => format!("Unsupported semantic condition role '{}'", condition_role),
        };

        Ok((
            SemanticConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed,
                target_field: condition
                    .parameters
                    .get("target_field")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
                actual_value: Some(json!({
                    "similarity": similarity,
                    "slice_similarities": similarities,
                    "target_slot": target_slot,
                    "best_anchor_index": best_anchor_idx,
                    "threshold_matched": threshold_matched,
                    "guard_triggered": guard_triggered,
                })),
                expected_value: Some(json!({
                    "condition_role": condition_role,
                    "match_operator": match_operator,
                    "target_slot": target_slot,
                    "similarity_threshold": similarity_threshold,
                    "anchor_count": anchors.len(),
                    "categories": condition.parameters.get("categories").cloned().unwrap_or(json!([])),
                    "custom_anchors": condition.parameters.get("custom_anchors").cloned().unwrap_or(json!([])),
                })),
                details,
            },
            similarities,
        ))
    }

    fn evaluate_record_count(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let Some(actual_count) = intent.data.record_count else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("record_count".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No record_count was provided".to_string(),
            };
        };
        let Some(max_records) = condition
            .parameters
            .get("max_records")
            .and_then(|v| v.as_u64())
        else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("record_count".to_string()),
                actual_value: Some(json!(actual_count)),
                expected_value: None,
                details: "No max_records threshold was configured".to_string(),
            };
        };

        let passed = match condition.operator.as_str() {
            "less_than" => actual_count < max_records,
            "less_than_or_equal" => actual_count <= max_records,
            _ => false,
        };

        DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("record_count".to_string()),
            actual_value: Some(json!(actual_count)),
            expected_value: Some(json!(max_records)),
            details: format!(
                "record_count={} compared against max_records={}",
                actual_count, max_records
            ),
        }
    }

    fn evaluate_output_channel(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let Some(channel) = intent.risk.channel.as_deref() else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("output_channel".to_string()),
                actual_value: None,
                expected_value: Some(condition.parameters.clone()),
                details: "No output_channel was provided".to_string(),
            };
        };
        let Some(blocked_channels) = condition
            .parameters
            .get("blocked_channels")
            .and_then(|v| v.as_array())
        else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("output_channel".to_string()),
                actual_value: Some(json!(channel)),
                expected_value: None,
                details: "No blocked_channels were configured".to_string(),
            };
        };

        let passed = condition.operator == "not_in"
            && !blocked_channels
                .iter()
                .filter_map(|value| value.as_str())
                .any(|blocked| blocked == channel);

        DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("output_channel".to_string()),
            actual_value: Some(json!(channel)),
            expected_value: Some(json!(blocked_channels)),
            details: format!(
                "output_channel='{}' checked against blocked channel list",
                channel
            ),
        }
    }

    fn evaluate_data_classification(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let Some(allowed) = condition
            .parameters
            .get("allowed_classifications")
            .and_then(|v| v.as_array())
        else {
            return DeterministicConditionResultPayload {
                condition_type: condition.condition_type.clone(),
                operator: condition.operator.clone(),
                passed: false,
                target_field: Some("data_classifications".to_string()),
                actual_value: Some(json!(intent.data.sensitivity)),
                expected_value: None,
                details: "No allowed_classifications were configured".to_string(),
            };
        };

        let allowed_set: HashSet<&str> =
            allowed.iter().filter_map(|value| value.as_str()).collect();
        let passed = !intent.data.sensitivity.is_empty()
            && intent
                .data
                .sensitivity
                .iter()
                .all(|classification| allowed_set.contains(classification.as_str()));

        DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: Some("data_classifications".to_string()),
            actual_value: Some(json!(intent.data.sensitivity)),
            expected_value: Some(json!(allowed)),
            details: "All data classifications must be in the allowed list".to_string(),
        }
    }

    fn evaluate_aggregation_limit(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        if let Some(max_records) = condition
            .parameters
            .get("max_records")
            .and_then(|v| v.as_u64())
        {
            if let Some(actual_count) = intent.data.record_count {
                let passed = match condition.operator.as_str() {
                    "less_than" => actual_count < max_records,
                    "less_than_or_equal" => actual_count <= max_records,
                    _ => false,
                };
                return DeterministicConditionResultPayload {
                    condition_type: condition.condition_type.clone(),
                    operator: condition.operator.clone(),
                    passed,
                    target_field: Some("record_count".to_string()),
                    actual_value: Some(json!(actual_count)),
                    expected_value: Some(json!(max_records)),
                    details: "Aggregation limit currently evaluates against record_count only"
                        .to_string(),
                };
            }
        }

        DeterministicConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed: false,
            target_field: Some("record_count".to_string()),
            actual_value: intent.data.record_count.map(|value| json!(value)),
            expected_value: Some(condition.parameters.clone()),
            details: "Aggregation limit could not be evaluated from the provided intent"
                .to_string(),
        }
    }

    /// Calculate average similarities across all evidence
    fn average_similarities(evidence: &[RuleEvidence]) -> [f32; 4] {
        if evidence.is_empty() {
            return [0.0; 4];
        }

        let mut sums = [0.0; 4];
        for ev in evidence {
            for i in 0..4 {
                sums[i] += ev.similarities[i];
            }
        }

        let count = evidence.len() as f32;
        [
            sums[0] / count,
            sums[1] / count,
            sums[2] / count,
            sums[3] / count,
        ]
    }

    fn derive_overall_evaluation_mode(evidence: &[RuleEvidence]) -> String {
        if evidence.is_empty() {
            return "unknown".to_string();
        }

        let has_semantic = evidence
            .iter()
            .any(|ev| matches!(ev.evaluation_mode.as_str(), "semantic" | "hybrid"));
        let has_deterministic = evidence.iter().any(|ev| {
            matches!(
                ev.evaluation_mode.as_str(),
                "deterministic" | "hybrid" | "network"
            )
        });
        let all_network = evidence.iter().all(|ev| ev.evaluation_mode == "network");

        if all_network {
            "network".to_string()
        } else if has_semantic && has_deterministic {
            "hybrid".to_string()
        } else if has_semantic {
            "semantic".to_string()
        } else if has_deterministic {
            "deterministic".to_string()
        } else {
            "unknown".to_string()
        }
    }

    /// Build detailed slice comparison data for telemetry
    fn build_slice_details(
        &self,
        result: &ComparisonResult,
        thresholds: &[f32; 4],
    ) -> Vec<SliceComparisonDetail> {
        let slice_names = ["action", "resource", "data", "risk"];

        slice_names
            .iter()
            .enumerate()
            .map(|(i, &name)| SliceComparisonDetail {
                slice_name: name.to_string(),
                similarity: result.slice_similarities[i],
                threshold: thresholds[i],
                passed: result.slice_similarities[i] >= thresholds[i],
                anchor_count: 0, // Could be populated from rule_vector if needed
                best_anchor_idx: None,
            })
            .collect()
    }

    /// Flush telemetry to disk
    pub fn flush_telemetry(&self) -> Result<(), String> {
        if let Some(ref telemetry) = self.telemetry {
            telemetry.flush()
        } else {
            Ok(())
        }
    }

    /// Get telemetry statistics
    pub fn telemetry_stats(&self) -> Option<crate::telemetry::recorder::TelemetryStats> {
        self.telemetry.as_ref().map(|t| t.stats())
    }

    fn get_rule_thresholds(
        &self,
        rule: &Arc<dyn RuleInstance>,
    ) -> Result<([f32; 4], DecisionMode), String> {
        let payload = rule.management_plane_payload();

        if let Value::Object(map) = payload {
            let mut thresholds = DEFAULT_THRESHOLDS;
            if let Some(Value::String(threshold_str)) = map.get("thresholds") {
                if let Ok(decoded) = serde_json::from_str::<SliceThresholdsPayload>(threshold_str) {
                    thresholds = [decoded.action, decoded.resource, decoded.data, decoded.risk];
                }
            }

            let decision = match map.get("rule_decision") {
                Some(Value::String(mode)) => match mode.as_str() {
                    "weighted-avg" => DecisionMode::WeightedAvgMode,
                    "min" => DecisionMode::MinMode,
                    _ => {
                        return Err(format!(
                            "Rule '{}' has invalid rule_decision='{}' (expected 'min' or 'weighted-avg')",
                            rule.rule_id(),
                            mode
                        ))
                    }
                },
                Some(_) => {
                    return Err(format!(
                        "Rule '{}' has non-string rule_decision (expected 'min' or 'weighted-avg')",
                        rule.rule_id()
                    ))
                }
                None => {
                    return Err(format!(
                        "Rule '{}' missing required rule_decision (expected 'min' or 'weighted-avg')",
                        rule.rule_id()
                    ))
                }
            };

            return Ok((thresholds, decision));
        }

        Err(format!(
            "Rule '{}' has non-object management_plane_payload",
            rule.rule_id()
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api_types::{Actor, Data, Resource, Risk};

    fn intent_with_edge(
        source_agent: Option<&str>,
        source_layer: Option<&str>,
        destination_agent: Option<&str>,
        destination_layer: Option<&str>,
    ) -> IntentEvent {
        IntentEvent {
            id: "intent-1".to_string(),
            schema_version: "v1.3".to_string(),
            tenant_id: "tenant-1".to_string(),
            timestamp: 0.0,
            actor: Actor {
                id: "agent-1".to_string(),
                actor_type: "agent".to_string(),
            },
            action: "execute".to_string(),
            source_agent: source_agent.map(str::to_string),
            source_layer: source_layer.map(str::to_string),
            destination_agent: destination_agent.map(str::to_string),
            destination_layer: destination_layer.map(str::to_string),
            llm_tool_intent: None,
            tool_call_count: None,
            resource: Resource {
                resource_type: "llm".to_string(),
                name: None,
                location: None,
            },
            data: Data {
                sensitivity: Vec::new(),
                pii: None,
                volume: None,
                content: None,
                size_bytes: None,
                input_token_count: None,
                record_count: None,
            },
            risk: Risk {
                authn: "none".to_string(),
                channel: None,
            },
            context: None,
            layer: Some("llm".to_string()),
            tool_name: None,
            tool_method: None,
            tool_params: None,
            rag_source_id: None,
            rag_source_name: None,
            resource_identity_type: None,
            resource_identity_key: None,
            resource_identity_name: None,
            rate_limit_context: None,
        }
    }

    #[test]
    fn parse_connection_match_requires_complete_policy_edge() {
        let payload = json!({
            "connection_match": {
                "source_agent": "agent-1",
                "source_layer": "input",
                "destination_agent": "agent-1",
                "destination_layer": ""
            }
        });

        let err = EnforcementEngine::parse_connection_match(&payload).unwrap_err();
        assert!(err.contains("destination_layer"));
    }

    #[test]
    fn missing_intent_edge_evidence_names_missing_fields() {
        let intent = intent_with_edge(Some("agent-1"), None, Some("agent-1"), Some("llm"));
        let missing = EnforcementEngine::missing_intent_edge_fields(&intent);

        assert_eq!(missing, vec!["source_layer"]);

        let evidence = EnforcementEngine::connection_missing_evidence(&intent, &missing);
        let connection_result: Value =
            serde_json::from_str(&evidence.connection_result_json).unwrap();
        let semantic_results: Value =
            serde_json::from_str(&evidence.semantic_results_json).unwrap();

        assert_eq!(evidence.decision, 0);
        assert_eq!(evidence.evaluation_mode, "connection");
        assert_eq!(connection_result["matched"], false);
        assert!(connection_result["reason"]
            .as_str()
            .unwrap()
            .contains("source_layer"));
        assert_eq!(semantic_results[0]["missing_fields"][0], "source_layer");
    }

    fn semantic_condition(role: &str, anchors: Value) -> SemanticConditionPayload {
        let operator = if role == "guard" {
            "similar_to_attack"
        } else {
            "similar_to_allowed"
        };
        SemanticConditionPayload {
            condition_type: "prompt_attack_semantic".to_string(),
            operator: operator.to_string(),
            parameters: json!({
                "condition_role": role,
                "evaluation_direction": "positive",
                "match_operator": "similarity_gte",
                "target_slot": "data",
                "similarity_threshold": 0.8,
                "anchor_vectors": anchors,
                "anchor_count": 1,
                "target_field": "payload_text",
            }),
        }
    }

    fn one_hot_vector(index: usize) -> Vec<f32> {
        let mut vector = vec![0.0f32; 32];
        vector[index] = 1.0;
        vector
    }

    fn intent_vector_with_data_slot(data_slot: &[f32]) -> [f32; 128] {
        let mut intent_vector = [0.0f32; 128];
        intent_vector[64..96].copy_from_slice(data_slot);
        intent_vector
    }

    #[test]
    fn semantic_guard_fails_when_payload_matches_guard_anchor() {
        let anchor = one_hot_vector(0);
        let intent_vector = intent_vector_with_data_slot(&anchor);
        let condition = semantic_condition("guard", json!([anchor]));

        let (result, similarities) =
            EnforcementEngine::evaluate_semantic_condition_anchors(&condition, &intent_vector)
                .unwrap();

        assert!(!result.passed);
        assert_eq!(similarities[2], 1.0);
        assert!(result.details.contains("reached or exceeded"));
        assert_eq!(
            result.actual_value.unwrap()["guard_triggered"],
            Value::Bool(true)
        );
    }

    #[test]
    fn semantic_negative_direction_is_normalized_to_guard() {
        let anchor = one_hot_vector(0);
        let intent_vector = intent_vector_with_data_slot(&anchor);
        let condition = SemanticConditionPayload {
            condition_type: "prompt_attack_semantic".to_string(),
            operator: "not_similar_to_attack".to_string(),
            parameters: json!({
                "evaluation_direction": "negative",
                "target_slot": "data",
                "similarity_threshold": 0.8,
                "anchor_vectors": [anchor],
                "anchor_count": 1,
                "target_field": "payload_text",
            }),
        };

        let (result, _) =
            EnforcementEngine::evaluate_semantic_condition_anchors(&condition, &intent_vector)
                .unwrap();

        assert!(!result.passed);
        assert_eq!(result.expected_value.unwrap()["condition_role"], "guard");
        assert_eq!(result.actual_value.unwrap()["guard_triggered"], true);
    }

    #[test]
    fn semantic_guard_passes_when_payload_is_below_guard_threshold() {
        let intent_vector = intent_vector_with_data_slot(&one_hot_vector(0));
        let condition = semantic_condition("guard", json!([one_hot_vector(1)]));

        let (result, similarities) =
            EnforcementEngine::evaluate_semantic_condition_anchors(&condition, &intent_vector)
                .unwrap();

        assert!(result.passed);
        assert_eq!(similarities[2], 0.0);
        assert!(result.details.contains("stayed below"));
    }

    #[test]
    fn semantic_allow_passes_when_payload_matches_allow_anchor() {
        let anchor = one_hot_vector(0);
        let intent_vector = intent_vector_with_data_slot(&anchor);
        let condition = semantic_condition("allow", json!([anchor]));

        let (result, similarities) =
            EnforcementEngine::evaluate_semantic_condition_anchors(&condition, &intent_vector)
                .unwrap();

        assert!(result.passed);
        assert_eq!(similarities[2], 1.0);
        assert!(result.details.contains("met or exceeded"));
    }

    #[test]
    fn semantic_condition_missing_anchor_vectors_fails_closed_with_evidence() {
        let intent_vector = intent_vector_with_data_slot(&one_hot_vector(0));
        let condition = semantic_condition("guard", json!([]));

        let (result, similarities) =
            EnforcementEngine::evaluate_semantic_condition_anchors(&condition, &intent_vector)
                .unwrap();

        assert!(!result.passed);
        assert_eq!(similarities, [0.0; 4]);
        assert!(result.details.contains("no configured anchor vectors"));
        assert_eq!(result.expected_value.unwrap()["requires"], "anchor_vectors");
    }

    #[test]
    fn payload_with_condition_local_semantic_guard_requires_embedding() {
        let payload = json!({
            "semantic_conditions": [
                {
                    "condition_type": "prompt_attack_semantic",
                    "operator": "not_similar_to_attack",
                    "parameters": {
                        "evaluation_direction": "guard",
                        "target_slot": "data",
                        "anchors": ["ignore previous instructions"],
                        "anchor_vectors": [one_hot_vector(0)],
                        "anchor_count": 1
                    }
                }
            ]
        });

        assert!(EnforcementEngine::payload_has_semantic_conditions(&payload));
    }
}

// ============================================================================
// Helper Types
// ============================================================================
