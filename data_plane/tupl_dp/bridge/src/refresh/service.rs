//! Event-driven rule refresh service.
//!
//! Triggers rule reload from db_infra on demand via gRPC endpoint.
//! Replaces in-memory HashMap with latest from persistent storage.

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

    /// Trigger immediate refresh from db_infra.
    ///
    /// Called via gRPC RefreshRules() endpoint.
    /// Rebuilds the in-memory HashMap from db_infra.
    ///
    /// # Returns
    /// Stats about the refresh operation or error message.
    pub async fn refresh_from_storage(&self) -> Result<RefreshStats, String> {
        let start = now_ms();

        self.bridge.rebuild_from_db_public()?;
        let num_rules = self.bridge.rule_count();

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
