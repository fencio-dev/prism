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

#[derive(Debug, Deserialize)]
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

        println!("Enforcing intent for layer: {}", layer);

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

        // 1. Query rules for this layer from Bridge using exact path filtering first
        let query_start = Instant::now();
        let dry_run_rule_ids = Self::extract_dry_run_rule_ids(intent.context.as_ref());
        let rules = self.get_rules_for_layer(
            layer,
            &actor_id,
            &tenant_id,
            &intent,
            dry_run_rule_ids.as_ref(),
        )?;
        let query_duration = query_start.elapsed().as_micros() as u64;

        if rules.is_empty() {
            // No rules = fail-closed (BLOCK)
            println!(
                "No rules configured for layer {}, blocking by default",
                layer
            );

            // Record no rules found
            if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
                telemetry.with_session(sid, |session| {
                    session.add_event(SessionEvent::NoRulesFound {
                        timestamp_us: EnforcementSession::timestamp_us(),
                        layer: layer.to_string(),
                    });
                });

                let total_duration = session_start.elapsed().as_micros() as u64;
                telemetry.complete_session(sid, 0, total_duration).ok();
            }

            return Ok(EnforcementResult {
                decision: 0,
                slice_similarities: [0.0; 4],
                rules_evaluated: 0,
                evidence: vec![],
                session_id: session_id.clone().unwrap_or_else(|| request_id.to_string()),
                enforcement_decision: Some(EnforcementDecision {
                    decision: Decision::Deny,
                    modified_params: None,
                    drift_triggered: false,
                }),
                evaluation_mode: "unknown".to_string(),
            });
        }

        let rules_count = rules.len();
        println!("Found {} rules for layer {}", rules_count, layer);

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
                        println!(
                            "Intent encoding failed: {}. Blocking intent (fail-closed).",
                            err
                        );

                        if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
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
                            evidence: vec![],
                            session_id: session_id.clone().unwrap_or_else(|| request_id.to_string()),
                            enforcement_decision: Some(EnforcementDecision {
                                decision: Decision::Deny,
                                modified_params: None,
                                drift_triggered: false,
                            }),
                            evaluation_mode: "unknown".to_string(),
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
        // Returns (ComparisonResult, rule_vector) or an Err.
        let evaluate_rule = |rule: &Arc<dyn RuleInstance>,
                             evidence: &mut Vec<RuleEvidence>|
         -> Result<(ComparisonResult, RuleVector), String> {
            if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
                telemetry.with_session(sid, |session| {
                    session.add_event(SessionEvent::RuleEvaluationStarted {
                        timestamp_us: EnforcementSession::timestamp_us(),
                        rule_id: rule.rule_id().to_string(),
                        rule_priority: rule.priority(),
                    });
                });
            }

            let rule_vector = self.bridge.get_rule_anchors(rule.rule_id()).unwrap_or_default();
            let semantic_required = self.rule_vector_requires_semantic(&rule_vector);
            let payload = rule.management_plane_payload();
            let connection_result = Self::parse_connection_match(&payload).map(|connection_match| {
                ConnectionEvaluationPayload {
                    matched: true,
                    source_agent: connection_match.source_agent,
                    source_layer: connection_match.source_layer,
                    destination_agent: connection_match.destination_agent,
                    destination_layer: connection_match.destination_layer,
                }
            });
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
            let (cmp, semantic_reason, semantic_results) = if !deterministic_passed {
                (
                    ComparisonResult {
                        decision: 0,
                        slice_similarities: [0.0; 4],
                        triggering_slice_idx: 0,
                    },
                    String::new(),
                    Vec::new(),
                )
            } else if has_semantic_conditions {
                let vector = intent_vector.ok_or_else(|| {
                    format!(
                        "Rule '{}' requires semantic evaluation but no intent vector is available",
                        rule.rule_id()
                    )
                })?;
                let (semantic_passed, semantic_reason, semantic_similarities, semantic_results) =
                    self.evaluate_semantic_conditions(rule, &vector, &rule_vector)?;
                (
                    ComparisonResult {
                        decision: if semantic_passed { 1 } else { 0 },
                        slice_similarities: semantic_similarities,
                        triggering_slice_idx: 2,
                    },
                    semantic_reason,
                    semantic_results,
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
                )
            };
            let cmp = cmp;
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
                connection_result_json: connection_result
                    .as_ref()
                    .map(|value| serde_json::to_string(value).unwrap_or_default())
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

            Ok((cmp, rule_vector))
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
                t.complete_session(sid, legacy_decision, total_duration).ok();
            }

            EnforcementResult {
                decision: legacy_decision,
                slice_similarities: final_similarities,
                rules_evaluated,
                evidence,
                session_id: session_id.clone().unwrap_or_else(|| request_id.to_string()),
                enforcement_decision: Some(enforcement_decision),
                evaluation_mode,
            }
        };

        // -----------------------------------------------------------------------
        // Pass 1 — FORBIDDEN
        //   Any match → DENY immediately. Drift is irrelevant.
        // -----------------------------------------------------------------------
        for rule in &forbidden_rules {
            let (cmp, _) = evaluate_rule(rule, &mut evidence)?;
            if cmp.decision == 1 {
                println!(
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
            let (cmp, _) = evaluate_rule(rule, &mut evidence)?;
            if cmp.decision == 1 {
                let threshold = rule.drift_threshold();
                let (drift_triggered, deny) = if threshold > 0.0 {
                    (drift_score > threshold, drift_score > threshold)
                } else {
                    // threshold == 0.0 → always deny on match
                    (false, true)
                };
                if deny {
                    println!(
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
            let (cmp, _) = evaluate_rule(rule, &mut evidence)?;
            if cmp.decision == 1 {
                let threshold = rule.drift_threshold();
                let sims = cmp.slice_similarities;

                if threshold > 0.0 && drift_score > threshold {
                    println!(
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
                    println!(
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
                    println!(
                        "ALLOW (CONTEXT_ALLOW): rule '{}' matched",
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
            }
        }

        // -----------------------------------------------------------------------
        // Pass 4 — CONTEXT_DEFER
        //   Any match → DEFER.
        // -----------------------------------------------------------------------
        for rule in &context_defer_rules {
            let (cmp, _) = evaluate_rule(rule, &mut evidence)?;
            if cmp.decision == 1 {
                println!(
                    "DEFER (CONTEXT_DEFER): rule '{}' matched",
                    rule.rule_id()
                );
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
        println!(
            "DENY (FAIL-CLOSED): No rules matched for layer {}",
            layer
        );

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
    ) -> Result<Vec<Arc<dyn RuleInstance>>, String> {
        println!("Querying rules for layer: {}", layer);

        let requested_layer = if layer.is_empty() { None } else { Some(layer) };

        let mut filtered: Vec<_> = self
            .bridge
            .all_rules()
            .into_iter()
            .filter(|rule| rule.is_enabled())
            .filter(|rule| match dry_run_rule_ids {
                Some(rule_ids) if !rule_ids.is_empty() => {
                    rule_ids.contains(rule.rule_id())
                }
                _ => rule.scope().applies_to(actor_id) || rule.scope().applies_to(tenant_id),
            })
            .filter(|rule| match (rule.layer(), requested_layer) {
                (None, _) => true,
                (Some(rule_layer), Some(requested)) => rule_layer == requested,
                (Some(_), None) => false,
            })
            .filter(|rule| self.rule_matches_connection(rule, intent))
            .collect();

        filtered.sort_by(|a, b| b.priority().cmp(&a.priority()));

        println!("Found {} rules for layer {} (actor: {}, tenant: {})", filtered.len(), layer, actor_id, tenant_id);
        Ok(filtered)
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

    fn rule_requires_semantic(&self, rule: &Arc<dyn RuleInstance>) -> bool {
        self.bridge
            .get_rule_anchors(rule.rule_id())
            .map(|rule_vector| self.rule_vector_requires_semantic(&rule_vector))
            .unwrap_or(false)
    }

    fn parse_connection_match(payload: &Value) -> Option<ConnectionMatchPayload> {
        payload
            .get("connection_match")
            .and_then(|value| value.as_str())
            .and_then(|encoded| serde_json::from_str::<ConnectionMatchPayload>(encoded).ok())
    }

    fn parse_deterministic_conditions(
        payload: &Value,
    ) -> Result<Vec<DeterministicConditionPayload>, String> {
        let Some(encoded) = payload
            .get("deterministic_conditions")
            .and_then(|value| value.as_str())
        else {
            return Ok(Vec::new());
        };

        serde_json::from_str::<Vec<DeterministicConditionPayload>>(encoded)
            .map_err(|e| format!("Invalid deterministic_conditions payload: {}", e))
    }

    fn parse_semantic_conditions(
        payload: &Value,
    ) -> Result<Vec<SemanticConditionPayload>, String> {
        let Some(encoded) = payload
            .get("semantic_conditions")
            .and_then(|value| value.as_str())
        else {
            return Ok(Vec::new());
        };

        serde_json::from_str::<Vec<SemanticConditionPayload>>(encoded)
            .map_err(|e| format!("Invalid semantic_conditions payload: {}", e))
    }

    fn rule_matches_connection(&self, rule: &Arc<dyn RuleInstance>, intent: &IntentEvent) -> bool {
        let payload = rule.management_plane_payload();
        let Some(connection_match) = Self::parse_connection_match(&payload) else {
            return true;
        };

        match (
            intent.source_agent.as_deref(),
            intent.source_layer.as_deref(),
            intent.destination_agent.as_deref(),
            intent.destination_layer.as_deref(),
        ) {
            (Some(source_agent), Some(source_layer), Some(destination_agent), Some(destination_layer)) => {
                source_agent == connection_match.source_agent
                    && source_layer == connection_match.source_layer
                    && destination_agent == connection_match.destination_agent
                    && destination_layer == connection_match.destination_layer
            }
            _ => false,
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
                "prompt_injection_regex" => self.evaluate_prompt_injection_regex(&condition, intent)?,
                "regex_pattern" => self.evaluate_regex_pattern(&condition, intent)?,
                "payload_size" => self.evaluate_payload_size(&condition, intent),
                "tool_name" => self.evaluate_tool_name(&condition, intent),
                "request_rate" => self.evaluate_request_rate(&condition, intent),
                "tool_parameter_validation" => self.evaluate_tool_parameter_validation(&condition, intent),
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
                    format!("deterministic condition failed: {}", condition.condition_type),
                    results,
                ));
            }
        }

        Ok((true, "all deterministic conditions passed".to_string(), results))
    }

    fn evaluate_semantic_conditions(
        &self,
        rule: &Arc<dyn RuleInstance>,
        intent_vector: &[f32; 128],
        rule_vector: &RuleVector,
    ) -> Result<(bool, String, [f32; 4], Vec<SemanticConditionResultPayload>), String> {
        let payload = rule.management_plane_payload();
        let conditions = Self::parse_semantic_conditions(&payload)?;
        if conditions.is_empty() {
            return Ok((true, "no semantic conditions".to_string(), [0.0; 4], Vec::new()));
        }

        let semantic_cmp = compare_intent_vs_rule(
            intent_vector,
            rule_vector,
            [0.0, 0.0, 0.0, 0.0],
            DecisionMode::MinMode,
            [1.0, 1.0, 1.0, 1.0],
        );

        let mut results = Vec::with_capacity(conditions.len());
        for condition in conditions {
            let result = match condition.condition_type.as_str() {
                "prompt_attack_semantic" => {
                    self.evaluate_prompt_attack_semantic(&condition, semantic_cmp.slice_similarities)?
                }
                "tool_call_semantic" => {
                    self.evaluate_positive_semantic_condition(&condition, semantic_cmp.slice_similarities)
                }
                "tool_response_semantic" => {
                    self.evaluate_positive_semantic_condition(&condition, semantic_cmp.slice_similarities)
                }
                _ => SemanticConditionResultPayload {
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
            };

            let passed = result.passed;
            results.push(result);

            if !passed {
                return Ok((
                    false,
                    format!("semantic condition failed: {}", condition.condition_type),
                    semantic_cmp.slice_similarities,
                    results,
                ));
            }
        }

        Ok((
            true,
            "all semantic conditions passed".to_string(),
            semantic_cmp.slice_similarities,
            results,
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
        let Some(patterns) = condition.parameters.get("pii_patterns").and_then(|v| v.as_object()) else {
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
            if !config.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true) {
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

        let passed = matches!(condition.operator.as_str(), "not_contains" | "absent") && !found_match;
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
        let Some(patterns) = condition.parameters.get("blocked_patterns").and_then(|v| v.as_array()) else {
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

        let passed = condition.operator == "not_matches_pattern" && !found_match;
        let details = if passed {
            "No blocked regex patterns matched".to_string()
        } else if let Some(pattern) = matched_pattern {
            format!("Payload matched blocked pattern: {}", pattern)
        } else {
            "Payload matched a blocked regex pattern".to_string()
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
        let Some(patterns) = condition.parameters.get("patterns").and_then(|v| v.as_array()) else {
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
        let Some(max_size) = condition.parameters.get("max_size_bytes").and_then(|v| v.as_u64()) else {
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
            details: format!("payload_bytes={} compared against max_size_bytes={}", actual_size, max_size),
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
        let Some(allowed_tools) = condition.parameters.get("allowed_tools").and_then(|v| v.as_array()) else {
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
            format!("tool_name='{}' satisfied tool allowlist constraint", tool_name)
        } else {
            format!("tool_name='{}' did not satisfy configured tool allowlist", tool_name)
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

    fn evaluate_request_rate(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let actual_count = intent
            .tool_call_count
            .or_else(|| intent.rate_limit_context.as_ref().map(|ctx| ctx.call_count as u64));

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
        let Some(max_requests) = condition.parameters.get("max_requests").and_then(|v| v.as_u64()) else {
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
        let Some(dangerous_actions) = condition.parameters.get("dangerous_actions").and_then(|v| v.as_object()) else {
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
                details: format!("No dangerous action rules were configured for tool '{}'", tool_name),
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
        let Some(max_tokens) = condition.parameters.get("max_tokens").and_then(|v| v.as_u64()) else {
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

    fn evaluate_prompt_attack_semantic(
        &self,
        condition: &SemanticConditionPayload,
        slice_similarities: [f32; 4],
    ) -> Result<SemanticConditionResultPayload, String> {
        let similarity_threshold = condition
            .parameters
            .get("similarity_threshold")
            .and_then(|v| v.as_f64())
            .map(|v| v as f32)
            .unwrap_or(0.78);
        let data_similarity = slice_similarities[2];

        let passed = match condition.operator.as_str() {
            "not_similar_to_attack" => data_similarity < similarity_threshold,
            _ => false,
        };

        let details = if passed {
            format!(
                "Prompt semantic similarity {:.3} stayed below attack threshold {:.3}",
                data_similarity, similarity_threshold
            )
        } else {
            format!(
                "Prompt semantic similarity {:.3} reached or exceeded attack threshold {:.3}",
                data_similarity, similarity_threshold
            )
        };

        Ok(SemanticConditionResultPayload {
            condition_type: condition.condition_type.clone(),
            operator: condition.operator.clone(),
            passed,
            target_field: condition
                .parameters
                .get("target_field")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            actual_value: Some(json!({
                "data_similarity": data_similarity,
                "slice_similarities": slice_similarities,
            })),
            expected_value: Some(json!({
                "similarity_threshold": similarity_threshold,
                "direction": "below_threshold_is_safe",
                "categories": condition.parameters.get("categories").cloned().unwrap_or(json!([])),
                "custom_anchors": condition.parameters.get("custom_anchors").cloned().unwrap_or(json!([])),
            })),
            details,
        })
    }

    fn evaluate_positive_semantic_condition(
        &self,
        condition: &SemanticConditionPayload,
        slice_similarities: [f32; 4],
    ) -> SemanticConditionResultPayload {
        let similarity_threshold = condition
            .parameters
            .get("similarity_threshold")
            .and_then(|v| v.as_f64())
            .map(|v| v as f32)
            .unwrap_or(0.72);
        let data_similarity = slice_similarities[2];

        let passed = match condition.operator.as_str() {
            "similar_to_allowed" => data_similarity >= similarity_threshold,
            _ => false,
        };

        let details = if passed {
            format!(
                "Semantic similarity {:.3} met or exceeded allow threshold {:.3}",
                data_similarity, similarity_threshold
            )
        } else {
            format!(
                "Semantic similarity {:.3} stayed below allow threshold {:.3}",
                data_similarity, similarity_threshold
            )
        };

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
                "data_similarity": data_similarity,
                "slice_similarities": slice_similarities,
            })),
            expected_value: Some(json!({
                "similarity_threshold": similarity_threshold,
                "direction": "above_threshold_is_allowed",
                "categories": condition.parameters.get("categories").cloned().unwrap_or(json!([])),
                "custom_anchors": condition.parameters.get("custom_anchors").cloned().unwrap_or(json!([])),
            })),
            details,
        }
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
        let Some(max_records) = condition.parameters.get("max_records").and_then(|v| v.as_u64()) else {
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
            details: format!("record_count={} compared against max_records={}", actual_count, max_records),
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
        let Some(blocked_channels) = condition.parameters.get("blocked_channels").and_then(|v| v.as_array()) else {
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
            details: format!("output_channel='{}' checked against blocked channel list", channel),
        }
    }

    fn evaluate_data_classification(
        &self,
        condition: &DeterministicConditionPayload,
        intent: &IntentEvent,
    ) -> DeterministicConditionResultPayload {
        let Some(allowed) = condition.parameters.get("allowed_classifications").and_then(|v| v.as_array()) else {
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

        let allowed_set: HashSet<&str> = allowed.iter().filter_map(|value| value.as_str()).collect();
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
        if let Some(max_records) = condition.parameters.get("max_records").and_then(|v| v.as_u64()) {
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
                    details: "Aggregation limit currently evaluates against record_count only".to_string(),
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
            details: "Aggregation limit could not be evaluated from the provided intent".to_string(),
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
        let has_deterministic = evidence
            .iter()
            .any(|ev| matches!(ev.evaluation_mode.as_str(), "deterministic" | "hybrid" | "network"));
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
                    thresholds = [
                        decoded.action,
                        decoded.resource,
                        decoded.data,
                        decoded.risk,
                    ];
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

// ============================================================================
// Helper Types
// ============================================================================
