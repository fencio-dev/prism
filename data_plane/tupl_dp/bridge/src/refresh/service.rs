//! Event-driven rule refresh service.
//!
//! Triggers rule reload from warm storage on demand via gRPC endpoint.
//! Replaces hot cache contents with latest from persistent storage.

use crate::bridge::Bridge;
use crate::types::now_ms;
use std::sync::Arc;

/// Statistics from a refresh operation.
#[derive(Debug, Clone)]
pub struct RefreshStats {
    /// Number of rules reloaded
    pub rules_refreshed: usize,
    /// Duration of refresh operation in milliseconds
    pub duration_ms: u64,
    /// Timestamp of refresh
    pub timestamp: u64,
}

/// Rule refresh service.
pub struct RefreshService {
    bridge: Arc<Bridge>,
}

impl RefreshService {
    /// Create a new refresh service.
    pub fn new(bridge: Arc<Bridge>) -> Self {
        Self { bridge }
    }

    /// Trigger immediate refresh from warm storage.
    ///
    /// Called via gRPC RefreshRules() endpoint.
    /// Reloads rules from warm storage into hot cache.
    ///
    /// **Algorithm**:
    /// - Clear the hot cache to remove any stale/evicted entries
    /// - Reload all rule anchors from warm storage
    /// - Re-insert each anchor into the hot cache
    ///
    /// # Returns
    /// Stats about the refresh operation or error message.
    pub async fn refresh_from_storage(&self) -> Result<RefreshStats, String> {
        let start = now_ms();

        // Load all rule anchors from warm storage
        let warm_anchors = self.bridge.warm_storage.load_anchors()?;
        let num_rules = warm_anchors.len();

        // Clear the hot cache to remove any stale/evicted entries
        self.bridge.hot_cache.clear();

        // Re-insert all anchors from warm storage into hot cache
        for (rule_id, anchors) in warm_anchors {
            self.bridge.hot_cache.insert(rule_id, anchors)?;
        }

        let duration_ms = now_ms() - start;

        Ok(RefreshStats {
            rules_refreshed: num_rules,
            duration_ms,
            timestamp: now_ms(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Tests will be added when Bridge is available in test environment
    #[test]
    fn test_refresh_stats_creation() {
        let stats = RefreshStats {
            rules_refreshed: 100,
            duration_ms: 50,
            timestamp: 1234567890,
        };

        assert_eq!(stats.rules_refreshed, 100);
        assert_eq!(stats.duration_ms, 50);
    }
}
