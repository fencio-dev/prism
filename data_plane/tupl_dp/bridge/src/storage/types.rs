//! Storage data structures and traits.
//!
//! Defines core types used across hot, warm, and cold storage tiers.

use crate::rule_vector::RuleVector;
use crate::types::now_ms;

/// A rule vector cached with metadata for LRU tracking.
#[derive(Clone, Debug)]
pub struct CachedRuleVector {
    /// Pre-encoded anchor vectors for this rule
    pub anchors: RuleVector,

    /// When this rule was loaded (Unix timestamp ms)
    pub loaded_at: u64,

    /// Last time this rule was evaluated (Unix timestamp ms)
    pub last_evaluated_at: u64,
}

impl CachedRuleVector {
    /// Create a new cached rule vector.
    pub fn new(anchors: RuleVector) -> Self {
        let now = now_ms();
        Self {
            anchors,
            loaded_at: now,
            last_evaluated_at: now,
        }
    }

    /// Update last evaluated timestamp.
    #[inline]
    pub fn mark_evaluated(&mut self) {
        self.last_evaluated_at = now_ms();
    }
}

/// Statistics about storage tier usage.
#[derive(Clone, Debug, Default)]
pub struct StorageStats {
    /// Number of rules in hot cache
    pub hot_rules: usize,
    /// Number of rules in warm storage
    pub warm_rules: usize,
    /// Number of rules in cold storage
    pub cold_rules: usize,
    /// Cache hits from hot tier
    pub hot_hits: u64,
    /// Cache hits from warm tier
    pub warm_hits: u64,
    /// Cache hits from cold tier
    pub cold_hits: u64,
    /// Number of evictions from hot cache
    pub evictions: u64,
}

/// Storage tier levels.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StorageTier {
    /// In-memory HashMap (<1μs latency)
    Hot,
    /// Memory-mapped file (~10μs latency)
    Warm,
    /// SQLite database (~100μs latency)
    Cold,
}
