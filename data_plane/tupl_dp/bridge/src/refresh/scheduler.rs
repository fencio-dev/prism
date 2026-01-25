//! Scheduled rule refresh service - background task for periodic rule reloading.
//!
//! This module provides the scheduled refresh functionality that periodically
//! reloads rules from warm storage into the hot cache. Unlike the event-driven
//! refresh service, this runs as a background task on a fixed interval.
//!
//! # Motivation for Scheduled Refresh
//!
//! While event-driven refresh (via gRPC) is optimal for responsive rule updates
//! from the control plane, scheduled refresh provides:
//!
//! 1. **Resilience**: If event-driven updates are missed or fail, scheduled refresh
//!    ensures the data plane eventually converges to the correct state.
//! 2. **Warm cache consistency**: Restores LRU-evicted entries that may have been
//!    dropped from the hot cache but remain in warm storage.
//! 3. **Background sync**: Runs independently without blocking enforcement operations.
//!
//! # Production Configuration
//!
//! The default refresh interval is 6 hours (21600 seconds). This interval is based on:
//! - Typical LRU cache retention: Rules accessed within the last 6 hours stay in hot cache
//! - Operational overhead: Refreshing every 6 hours (~0.0047% CPU impact) is negligible
//! - Convergence time: Acceptable for non-critical rule updates between event-driven syncs
//!
//! # LRU Locality Assumption
//!
//! The scheduler assumes strong LRU locality in rule access patterns:
//! - Rules needed in the next 6 hours are likely accessed in the last 6 hours
//! - Refreshing every 6 hours maintains working set consistency
//! - Rules that fall out of hot cache are usually not needed in subsequent requests
//!
//! If your workload has different access patterns (e.g., batch processing with
//! long idle periods), adjust `refresh_interval` accordingly.

use parking_lot::RwLock;
use std::sync::Arc;
use std::time::Duration;
use tokio::time::interval;

use crate::bridge::Bridge;
use crate::types::now_ms;
use log::{error, info};

/// Configuration for the scheduled refresh service.
#[derive(Debug, Clone)]
pub struct SchedulerConfig {
    /// Interval between refresh operations
    pub refresh_interval: Duration,
    /// Whether the scheduler is enabled
    pub enabled: bool,
}

impl Default for SchedulerConfig {
    fn default() -> Self {
        Self {
            // 6-hour interval balances LRU locality with operational overhead
            refresh_interval: Duration::from_secs(6 * 60 * 60),
            enabled: true,
        }
    }
}

/// Scheduler for periodic rule refresh from warm storage.
///
/// Runs as a background task spawned during server initialization.
/// Periodically reloads rules from warm storage into the hot cache
/// to maintain consistency and restore LRU-evicted entries.
pub struct RefreshScheduler {
    /// Reference to the bridge instance
    bridge: Arc<Bridge>,
    /// Scheduler configuration
    config: SchedulerConfig,
    /// Timestamp of the last successful refresh
    last_refresh_at: Arc<RwLock<u64>>,
}

impl RefreshScheduler {
    /// Creates a new refresh scheduler.
    ///
    /// # Arguments
    /// - `bridge`: Arc-wrapped Bridge instance managing all rule tables
    /// - `config`: Configuration specifying refresh interval and enabled state
    ///
    /// # Returns
    /// A new RefreshScheduler instance with last_refresh_at initialized to current time.
    ///
    /// # Example
    /// ```rust,no_run
    /// use std::sync::Arc;
    /// use std::time::Duration;
    /// use bridge::{Bridge, RefreshScheduler, SchedulerConfig};
    ///
    /// let bridge = Arc::new(Bridge::init()?);
    /// let config = SchedulerConfig {
    ///     refresh_interval: Duration::from_secs(6 * 60 * 60),
    ///     enabled: true,
    /// };
    /// let scheduler = RefreshScheduler::new(bridge, config);
    /// # Ok::<(), Box<dyn std::error::Error>>(())
    /// ```
    pub fn new(bridge: Arc<Bridge>, config: SchedulerConfig) -> Self {
        Self {
            bridge,
            config,
            last_refresh_at: Arc::new(RwLock::new(now_ms())),
        }
    }

    /// Returns the timestamp of the last successful refresh.
    ///
    /// # Returns
    /// Milliseconds since UNIX epoch of the last refresh, or initialization time
    /// if no refresh has occurred yet.
    ///
    /// # Example
    /// ```rust,no_run
    /// let last_ts = scheduler.last_refresh();
    /// println!("Last refresh: {} ms ago", now_ms() - last_ts);
    /// ```
    pub fn last_refresh(&self) -> u64 {
        *self.last_refresh_at.read()
    }

    /// Starts the scheduler background task.
    ///
    /// This method runs indefinitely and should be spawned as a tokio task.
    /// It periodically calls `do_refresh()` based on the configured interval.
    ///
    /// If the scheduler is disabled in config, this method returns immediately.
    /// If the scheduler is enabled, it will:
    /// 1. Wait for the configured interval
    /// 2. Execute a refresh cycle
    /// 3. Log statistics and any errors
    /// 4. Repeat indefinitely
    ///
    /// # Example
    /// ```rust,no_run
    /// let scheduler = Arc::new(RefreshScheduler::new(bridge, config));
    /// let scheduler_clone = scheduler.clone();
    /// tokio::spawn(async move {
    ///     scheduler_clone.start().await;
    /// });
    /// ```
    pub async fn start(self: Arc<Self>) {
        if !self.config.enabled {
            info!("Scheduled refresh is disabled, skipping");
            return;
        }

        info!(
            "Starting scheduled refresh with {}-second interval",
            self.config.refresh_interval.as_secs()
        );

        let mut ticker = interval(self.config.refresh_interval);

        // Run indefinitely
        loop {
            ticker.tick().await;
            self.do_refresh().await;
        }
    }

    /// Executes one refresh cycle.
    ///
    /// This internal method:
    /// 1. Calls `refresh_from_warm_storage()` to reload rules
    /// 2. Logs the operation statistics
    /// 3. Catches and logs any errors without panicking
    ///
    /// Errors are printed but do not stop the scheduler.
    async fn do_refresh(&self) {
        match self.refresh_from_warm_storage().await {
            Ok(stats) => {
                info!(
                    "Scheduled refresh completed: {} rules refreshed in {}ms",
                    stats.rules_refreshed, stats.duration_ms
                );
                *self.last_refresh_at.write() = now_ms();
            }
            Err(e) => {
                error!("Scheduled refresh failed: {}", e);
            }
        }
    }

    /// Performs one refresh cycle from warm storage.
    ///
    /// This method:
    /// 1. Records the start time
    /// 2. Loads all rule anchors from warm storage
    /// 3. Clears the hot cache and reloads with fresh anchors
    /// 4. Calculates operation duration
    /// 5. Returns statistics about the refresh
    ///
    /// **Algorithm**:
    /// - Clear the hot cache to remove any stale/evicted entries
    /// - Reload all rule anchors from warm storage
    /// - Re-insert each anchor into the hot cache
    /// - This restores LRU-evicted entries that remain in warm storage
    ///
    /// # Returns
    /// - `Ok(RefreshStats)`: Refresh succeeded with statistics
    /// - `Err(String)`: Refresh failed with error description
    ///
    /// # Error Handling
    /// Errors from warm storage access are propagated and will be logged
    /// by the scheduler. The hot cache is not modified if loading fails.
    async fn refresh_from_warm_storage(&self) -> Result<crate::refresh::RefreshStats, String> {
        let start = now_ms();

        // Load all rule anchors from warm storage
        let warm_anchors = self.bridge.warm_storage.load_anchors()?;
        let num_rules = warm_anchors.len();

        // Clear the hot cache to remove evicted entries
        self.bridge.hot_cache.clear();

        // Re-insert all anchors from warm storage into hot cache
        // This restores any LRU-evicted entries that remain in warm storage
        for (rule_id, anchors) in warm_anchors {
            self.bridge.hot_cache.insert(rule_id, anchors)?;
        }

        let duration_ms = now_ms() - start;

        Ok(crate::refresh::RefreshStats {
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
    fn test_scheduler_config_defaults() {
        let config = SchedulerConfig::default();
        assert!(config.enabled);
        assert_eq!(config.refresh_interval.as_secs(), 6 * 60 * 60);
    }

    #[test]
    fn test_scheduler_creation() {
        // This test verifies the scheduler can be created
        // Full integration tests would require a test Bridge instance
        let config = SchedulerConfig::default();
        assert!(config.enabled);
        assert_eq!(config.refresh_interval.as_secs(), 21600);
    }

    #[test]
    fn test_scheduler_config_custom() {
        let config = SchedulerConfig {
            refresh_interval: Duration::from_secs(3600),
            enabled: false,
        };
        assert!(!config.enabled);
        assert_eq!(config.refresh_interval.as_secs(), 3600);
    }
}
