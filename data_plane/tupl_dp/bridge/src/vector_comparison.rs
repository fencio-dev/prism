//! Direct vector comparison using cosine similarity.
//!
//! Compares intent vectors against rule anchor vectors to determine enforcement decisions.
//! No FFI wrapper - direct Rust function calls.
//!
//! This module was adapted from the semantic-sandbox FFI layer, with all FFI-specific
//! code removed to enable direct in-process vector comparisons.

use crate::rule_vector::RuleVector;

/// FFI-compatible structure for returning comparison results
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct ComparisonResult {
    pub decision: u8,                 // 0 = block, 1 = allow
    pub slice_similarities: [f32; 4], // action, resource, data, risk
}

/// Decision mode for rule enforcement
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecisionMode {
    /// Min-mode: All slices must meet their thresholds
    MinMode = 0,
    /// Weighted-average mode: Weighted average of slices compared to global threshold
    WeightedAvgMode = 1,
}

impl From<u8> for DecisionMode {
    fn from(value: u8) -> Self {
        match value {
            0 => DecisionMode::MinMode,
            _ => DecisionMode::WeightedAvgMode,
        }
    }
}

/// Compute dot product of two slices (used for cosine similarity on normalized vectors)
#[inline]
#[allow(dead_code)]
fn dot_product(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b.iter()).map(|(x, y)| x * y).sum()
}

/// Compute cosine similarity between two vectors
#[inline]
fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
    let norm_a = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let norm_b = b.iter().map(|x| x * x).sum::<f32>().sqrt();

    if norm_a < 1e-8 || norm_b < 1e-8 {
        0.0
    } else {
        let sim = dot / (norm_a * norm_b);
        sim.min(1.0).max(-1.0) // Clamp to [-1, 1]
    }
}

/// Compute maximum cosine similarity between intent slice and anchor set
#[inline]
fn max_anchor_similarity(intent_slice: &[f32], anchors: &[[f32; 32]], count: usize) -> f32 {
    if count == 0 {
        // No anchors = fail-closed (no match)
        return 0.0;
    }

    anchors[..count]
        .iter()
        .map(|anchor| cosine_similarity(intent_slice, anchor))
        .fold(0.0f32, f32::max)
}

/// Compare intent vector against rule anchors
///
/// Computes per-slice cosine similarity (via dot product on normalized vectors)
/// and applies threshold-based decision logic.
pub fn compare_intent_vs_rule(
    intent: &[f32; 128],
    rule_vector: &RuleVector,
    thresholds: [f32; 4],
    decision_mode: DecisionMode,
) -> ComparisonResult {
    let mut slice_similarities = [0.0f32; 4];

    // Extract intent slices
    let intent_action = &intent[0..32];
    let intent_resource = &intent[32..64];
    let intent_data = &intent[64..96];
    let intent_risk = &intent[96..128];

    // Compute max-of-anchors similarity per slot
    slice_similarities[0] = max_anchor_similarity(
        intent_action,
        &rule_vector.action_anchors,
        rule_vector.action_count,
    );
    slice_similarities[1] = max_anchor_similarity(
        intent_resource,
        &rule_vector.resource_anchors,
        rule_vector.resource_count,
    );
    slice_similarities[2] = max_anchor_similarity(
        intent_data,
        &rule_vector.data_anchors,
        rule_vector.data_count,
    );
    slice_similarities[3] = max_anchor_similarity(
        intent_risk,
        &rule_vector.risk_anchors,
        rule_vector.risk_count,
    );

    // Decision logic based on mode
    let decision = match decision_mode {
        DecisionMode::MinMode => {
            // Min-mode: All slices must meet their thresholds
            let all_pass = slice_similarities
                .iter()
                .zip(thresholds.iter())
                .all(|(sim, thresh)| sim >= thresh);

            if all_pass {
                1
            } else {
                0
            }
        }
        DecisionMode::WeightedAvgMode => {
            // Weighted-avg mode: Not used in current enforcement
            // Kept for compatibility, defaults to min-mode behavior
            let all_pass = slice_similarities
                .iter()
                .zip(thresholds.iter())
                .all(|(sim, thresh)| sim >= thresh);

            if all_pass {
                1
            } else {
                0
            }
        }
    };

    ComparisonResult {
        decision,
        slice_similarities,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dot_product() {
        let a = [1.0, 2.0, 3.0];
        let b = [4.0, 5.0, 6.0];
        let result = dot_product(&a, &b);
        assert_eq!(result, 32.0); // 1*4 + 2*5 + 3*6 = 32
    }

    #[test]
    fn test_cosine_similarity_identical() {
        let a = [0.5f32; 32];
        let b = [0.5f32; 32];
        let result = cosine_similarity(&a, &b);
        assert!((result - 1.0).abs() < 0.01, "Expected ~1.0, got {}", result);
    }

    #[test]
    fn test_cosine_similarity_orthogonal() {
        let mut a = [0.0f32; 32];
        let mut b = [0.0f32; 32];
        a[0..16].fill(1.0);
        b[16..32].fill(1.0);
        let result = cosine_similarity(&a, &b);
        assert!(result.abs() < 0.05, "Expected ~0, got {}", result);
    }

    #[test]
    fn test_zero_norm_guard() {
        let a = [0.0f32; 32];
        let b = [1.0f32; 32];
        let result = cosine_similarity(&a, &b);
        assert_eq!(result, 0.0);
        assert!(!result.is_nan());
    }

    #[test]
    fn test_max_anchor_similarity_empty() {
        let intent = [1.0f32; 32];
        let anchors = [[0.0f32; 32]; 16];
        let result = max_anchor_similarity(&intent, &anchors, 0);
        assert_eq!(result, 0.0, "Empty anchor set should fail-closed");
    }

    #[test]
    fn test_max_anchor_similarity_single() {
        let intent = [1.0f32; 32];
        let mut anchors = [[0.0f32; 32]; 16];
        anchors[0].fill(1.0);
        let result = max_anchor_similarity(&intent, &anchors, 1);
        assert!((result - 1.0).abs() < 0.01, "Expected ~1.0, got {}", result);
    }

    #[test]
    fn test_decision_mode_min_all_pass() {
        let intent = [0.9f32; 128];
        let rule_vector = RuleVector {
            action_anchors: [[1.0; 32]; 16],
            action_count: 1,
            resource_anchors: [[1.0; 32]; 16],
            resource_count: 1,
            data_anchors: [[1.0; 32]; 16],
            data_count: 1,
            risk_anchors: [[1.0; 32]; 16],
            risk_count: 1,
        };

        let thresholds = [0.85, 0.85, 0.85, 0.85];
        let result =
            compare_intent_vs_rule(&intent, &rule_vector, thresholds, DecisionMode::MinMode);
        assert_eq!(result.decision, 1, "All slices should pass");
    }

    #[test]
    fn test_decision_mode_min_one_fail() {
        let mut intent = [1.0f32; 128];
        intent[0..32].fill(-1.0); // Action slice opposite direction

        let mut action_anchors = [[0.0f32; 32]; 16];
        action_anchors[0].fill(1.0); // Opposite direction from intent

        let rule_vector = RuleVector {
            action_anchors,
            action_count: 1,
            resource_anchors: [[1.0; 32]; 16],
            resource_count: 1,
            data_anchors: [[1.0; 32]; 16],
            data_count: 1,
            risk_anchors: [[1.0; 32]; 16],
            risk_count: 1,
        };

        let thresholds = [0.85, 0.85, 0.85, 0.85];
        let result =
            compare_intent_vs_rule(&intent, &rule_vector, thresholds, DecisionMode::MinMode);
        assert_eq!(result.decision, 0, "Should block when one slice fails");
    }
}
