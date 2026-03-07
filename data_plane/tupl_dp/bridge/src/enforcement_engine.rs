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

use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::api_types::IntentEvent;
use reqwest::{header::CONTENT_TYPE, Client};
use serde::Deserialize;
use serde_json::Value;

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

        // 1. Encode intent to 128d vector (or reuse override)
        let encoding_start = Instant::now();

        if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
            telemetry.with_session(sid, |session| {
                session.add_event(SessionEvent::EncodingStarted {
                    timestamp_us: EnforcementSession::timestamp_us(),
                });
            });
        }

        let (intent_vector, encoding_duration, vector_norm) = if let Some(vector) = vector_override
        {
            let norm = vector.iter().map(|v| v * v).sum::<f32>().sqrt();
            (vector, 0u64, norm)
        } else {
            match self.encode_intent(intent_json).await {
                Ok(vector) => {
                    let duration = encoding_start.elapsed().as_micros() as u64;
                    let norm = vector.iter().map(|v| v * v).sum::<f32>().sqrt();
                    (vector, duration, norm)
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
                    });
                }
            }
        };

        if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
            telemetry.with_session(sid, |session| {
                session.add_event(SessionEvent::EncodingCompleted {
                    timestamp_us: EnforcementSession::timestamp_us(),
                    duration_us: encoding_duration,
                    vector_norm,
                });
                session.intent_vector = Some(intent_vector.to_vec());
                session.performance.encoding_duration_us = encoding_duration;
            });
        }

        // 2. Query rules for this layer from Bridge
        let query_start = Instant::now();
        let rules = self.get_rules_for_layer(layer, &actor_id, &tenant_id)?;
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

        // 3. Five-pass AARM evaluation.
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

            let rule_vector =
                self.bridge
                    .get_rule_anchors(rule.rule_id())
                    .ok_or_else(|| {
                        format!(
                            "Rule '{}' missing pre-encoded anchors (install-time encoding incomplete)",
                            rule.rule_id()
                        )
                    })?;

            let weights = self.get_rule_weights(rule);
            let (ev_thresholds, ev_decision_mode) = self.get_rule_thresholds(rule)?;
            let cmp = self.compare_with_sandbox(
                &intent_vector,
                &rule_vector,
                ev_thresholds,
                ev_decision_mode,
                weights,
            )?;
            let rule_eval_duration = 0u64; // timing not re-measured in closure for simplicity

            let slice_names = ["action", "resource", "data", "risk"];
            let triggering_slice = slice_names[cmp.triggering_slice_idx].to_string();

            let scoring_mode = match ev_decision_mode {
                DecisionMode::WeightedAvgMode => "weighted-avg".to_string(),
                DecisionMode::MinMode => "min".to_string(),
            };

            evidence.push(RuleEvidence {
                rule_id: rule.rule_id().to_string(),
                rule_name: rule.description().unwrap_or("").to_string(),
                decision: cmp.decision,
                similarities: cmp.slice_similarities,
                triggering_slice,
                anchor_matched: String::new(),
                thresholds: ev_thresholds,
                scoring_mode,
            });

            if let (Some(ref telemetry), Some(ref sid)) = (&self.telemetry, &session_id) {
                let thresholds = ev_thresholds;
                let slice_details = self.build_slice_details(&cmp, &thresholds);
                let payload = rule.management_plane_payload();
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
    fn get_rules_for_layer(&self, layer: &str, actor_id: &str, tenant_id: &str) -> Result<Vec<Arc<dyn RuleInstance>>, String> {
        println!("Querying rules for layer: {}", layer);

        let requested_layer = if layer.is_empty() { None } else { Some(layer) };

        let mut filtered: Vec<_> = self
            .bridge
            .all_rules()
            .into_iter()
            .filter(|rule| rule.is_enabled())
            .filter(|rule| rule.scope().applies_to(actor_id) || rule.scope().applies_to(tenant_id))
            .filter(|rule| match (rule.layer(), requested_layer) {
                (None, _) => true,
                (Some(rule_layer), Some(requested)) => rule_layer == requested,
                (Some(_), None) => false,
            })
            .collect();

        filtered.sort_by(|a, b| b.priority().cmp(&a.priority()));

        println!("Found {} rules for layer {} (actor: {}, tenant: {})", filtered.len(), layer, actor_id, tenant_id);
        Ok(filtered)
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
