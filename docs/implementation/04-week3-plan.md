# Week 3: Scheduled Refresh Service & Hot Cache LRU Eviction

**Objective**: Implement scheduled rule refresh mechanism and hot cache LRU eviction policy to complete the tiered storage system and enable production-grade rule lifecycle management.

**Priority Order**:
1. **Scheduled refresh service** (background task, 6hr interval)
2. **Hot cache LRU eviction** (capacity enforcement with least-recently-used policy)
3. **Polish & documentation** (code comments, doc strings, standards compliance)

---

## Executive Summary

Week 3 focuses on completing the critical operational infrastructure for production deployment:

- **Scheduled Refresh**: Background task that reloads rules from warm storage every 6 hours, ensuring stale rules are refreshed without manual intervention
- **LRU Eviction**: Capacity enforcement in hot cache when rules exceed 10K limit, evicting least-recently-used entries to warm storage
- **Production Readiness**: Code formatting, documentation, and test coverage to meet deployment standards

**Expected Outcome**: Fully functional, documented, production-ready data plane with automatic rule lifecycle management.

---

## Detailed Implementation Plan

### Phase 1: Scheduled Refresh Service (Days 8)

#### Task 1.1: Background Refresh Loop

**File**: `data_plane/tupl_dp/bridge/src/refresh/scheduler.rs` (NEW)

Create a new background scheduler that periodically reloads rules from warm storage:

```rust
//! Scheduled rule refresh background task.
//!
//! # Operation
//! - Spawned on data plane startup
//! - Runs every 6 hours in background tokio task
//! - Loads anchors from warm storage
//! - Replaces hot cache atomically
//! - Logs statistics and errors

use std::sync::Arc;
use std::time::Duration;
use tokio::time::interval;
use parking_lot::RwLock;

use crate::bridge::Bridge;
use crate::refresh::service::RefreshStats;
use crate::types::now_ms;

/// Scheduled refresh configuration.
#[derive(Clone, Debug)]
pub struct SchedulerConfig {
    /// Interval between refreshes (default: 6 hours)
    pub refresh_interval: Duration,
    
    /// Enable scheduled refresh (default: true)
    pub enabled: bool,
}

impl Default for SchedulerConfig {
    fn default() -> Self {
        Self {
            refresh_interval: Duration::from_secs(6 * 3600),
            enabled: true,
        }
    }
}

/// Background scheduler for periodic rule refresh.
pub struct RefreshScheduler {
    bridge: Arc<Bridge>,
    config: SchedulerConfig,
    last_refresh_at: Arc<RwLock<u64>>,
}

impl RefreshScheduler {
    /// Create new scheduler.
    pub fn new(bridge: Arc<Bridge>, config: SchedulerConfig) -> Self {
        Self {
            bridge,
            config,
            last_refresh_at: Arc::new(RwLock::new(now_ms())),
        }
    }
    
    /// Start the scheduled refresh loop.
    ///
    /// This spawns a background task that runs indefinitely.
    /// Never returns unless an error occurs.
    pub async fn start(self: Arc<Self>) {
        if !self.config.enabled {
            log::info!("Scheduled refresh disabled");
            return;
        }
        
        log::info!(
            "Starting scheduled refresh every {} seconds",
            self.config.refresh_interval.as_secs()
        );
        
        let mut ticker = interval(self.config.refresh_interval);
        
        loop {
            ticker.tick().await;
            self.do_refresh().await;
        }
    }
    
    /// Perform one refresh cycle.
    async fn do_refresh(&self) {
        let start = now_ms();
        
        match self.refresh_from_warm_storage().await {
            Ok(stats) => {
                log::info!(
                    "Scheduled refresh completed: {} rules refreshed in {}ms",
                    stats.rules_refreshed,
                    stats.duration_ms
                );
                *self.last_refresh_at.write() = now_ms();
            }
            Err(e) => {
                log::error!("Scheduled refresh failed: {}", e);
            }
        }
    }
    
    /// Load warm storage and replace hot cache.
    async fn refresh_from_warm_storage(&self) -> Result<RefreshStats, String> {
        let start = now_ms();
        
        // Load anchors from warm storage
        let warm_anchors = self.bridge
            .warm_storage
            .load_all_anchors()
            .map_err(|e| format!("Failed to load warm storage: {}", e))?;
        
        let rule_count = warm_anchors.len();
        
        // Replace hot cache atomically
        *self.bridge.hot_cache.write() = warm_anchors;
        
        Ok(RefreshStats {
            rules_refreshed: rule_count,
            duration_ms: now_ms() - start,
            timestamp: now_ms(),
        })
    }
    
    /// Get time of last refresh.
    pub fn last_refresh(&self) -> u64 {
        *self.last_refresh_at.read()
    }
}
```

**Integration**: In `grpc_server.rs`:

```rust
// Add to DataPlaneService
let scheduler = Arc::new(RefreshScheduler::new(
    bridge.clone(),
    SchedulerConfig::default(),
));

// Spawn background task
tokio::spawn({
    let sched = scheduler.clone();
    async move {
        sched.start().await
    }
});
```

---

#### Task 1.2: Update `refresh/mod.rs` Exports

**File**: `data_plane/tupl_dp/bridge/src/refresh/mod.rs` (MODIFY)

Add scheduler module:

```rust
//! Rule refresh service - scheduled and event-driven refresh.
//!
//! Handles two refresh modes:
//! 1. Scheduled: Background task every 6 hours
//! 2. Event-driven: Triggered via gRPC RefreshRules RPC

mod scheduler;
mod service;

pub use scheduler::{RefreshScheduler, SchedulerConfig};
pub use service::{RefreshService, RefreshStats};
```

---

### Phase 2: Hot Cache LRU Eviction (Days 8-9)

#### Task 2.1: Implement LRU Eviction Policy

**File**: `data_plane/tupl_dp/bridge/src/hot_cache.rs` (MODIFY - if separate file) or update in `storage/types.rs`

The hot cache needs to enforce capacity with LRU eviction. Update the insert logic:

```rust
/// Insert rule into hot cache with capacity enforcement.
///
/// If capacity is exceeded, evicts least-recently-used rules.
///
/// # Arguments
/// - `rule_id`: Unique rule identifier
/// - `anchors`: Rule vector to cache
///
/// # Eviction Strategy
/// - Triggers when: `cache.len() >= capacity && new_rule not cached`
/// - Evicts: 10% of capacity (rounded up to at least 1)
/// - Selection: Least-recently-used by `last_evaluated_at` timestamp
///
/// # Returns
/// - `Ok(())` if inserted successfully
/// - `Err(msg)` if eviction fails
pub fn insert(&self, rule_id: String, anchors: RuleVector) -> Result<(), String> {
    let mut cache = self.cache.write();
    
    // If rule already exists, just update it
    if cache.contains_key(&rule_id) {
        cache.insert(rule_id, (now_ms(), anchors));
        return Ok(());
    }
    
    // Check if we need to evict
    if cache.len() >= self.capacity {
        self.evict_lru(&mut cache)?;
    }
    
    cache.insert(rule_id, (now_ms(), anchors));
    Ok(())
}

/// Evict least-recently-used entries when cache is full.
///
/// Algorithm:
/// 1. Scan all cached rules
/// 2. Sort by `last_evaluated_at` timestamp (ascending)
/// 3. Remove oldest 10% of capacity
/// 4. Update statistics
///
/// # Rationale
/// - LRU eviction assumes locality: rules evaluated recently will be evaluated again
/// - 10% batch eviction reduces frequency of expensive sort operations
/// - Preserves working set for typical 6hr refresh cycles
fn evict_lru(&self, cache: &mut HashMap<String, (u64, RuleVector)>) -> Result<(), String> {
    // Collect all entries with timestamps
    let entries: Vec<(String, u64)> = cache
        .iter()
        .map(|(id, (ts, _))| (id.clone(), *ts))
        .collect();
    
    // Calculate how many to evict
    let to_evict = (self.capacity / 10).max(1);
    
    // Sort by timestamp (oldest first)
    let mut sorted = entries.clone();
    sorted.sort_by_key(|(_, ts)| *ts);
    
    // Remove oldest entries
    for (id, _) in sorted.iter().take(to_evict) {
        cache.remove(id);
    }
    
    // Update statistics
    self.stats.write().evictions += to_evict as u64;
    
    log::debug!(
        "Hot cache eviction: {} entries removed ({}ms elapsed)",
        to_evict,
        now_ms() - self.last_eviction_at
    );
    
    Ok(())
}

/// Get cache statistics.
pub fn stats(&self) -> StorageStats {
    let mut stats = self.stats.read().clone();
    stats.hot_rules = self.cache.read().len();
    stats
}
```

#### Task 2.2: Track Last-Evaluated Timestamps

**File**: `data_plane/tupl_dp/bridge/src/storage/types.rs` (MODIFY)

Ensure `CachedRuleVector` tracks evaluation time:

```rust
/// Cached rule with LRU metadata.
#[derive(Clone, Debug)]
pub struct CachedRuleVector {
    /// Pre-computed anchor vectors
    pub anchors: RuleVector,
    
    /// When rule was loaded into cache (Unix ms)
    pub loaded_at: u64,
    
    /// Last time rule was accessed for enforcement (Unix ms)
    pub last_evaluated_at: u64,
}

impl CachedRuleVector {
    /// Create new cached rule with current timestamp.
    pub fn new(anchors: RuleVector) -> Self {
        let now = now_ms();
        Self {
            anchors,
            loaded_at: now,
            last_evaluated_at: now,
        }
    }
    
    /// Mark this rule as evaluated (update timestamp).
    pub fn mark_evaluated(&mut self) {
        self.last_evaluated_at = now_ms();
    }
}
```

#### Task 2.3: Update Enforcement Path to Mark Evaluated

**File**: `data_plane/tupl_dp/bridge/src/enforcement_engine.rs` (MODIFY)

When a rule is looked up for enforcement, mark it as evaluated:

```rust
/// Look up rule by ID and mark as evaluated.
fn get_rule_for_enforcement(&self, rule_id: &str) -> Result<RuleVector, String> {
    // Get from storage (may promote across tiers)
    let rule = self.bridge.get_rule_anchors(rule_id)?;
    
    // Mark as evaluated for LRU tracking
    if let Ok(mut cache) = self.bridge.hot_cache.write() {
        if let Some((_, cached)) = cache.get_mut(rule_id) {
            cached.mark_evaluated();
        }
    }
    
    Ok(rule.anchors)
}
```

#### Task 2.4: Unit Tests for LRU Eviction

**File**: `data_plane/tupl_dp/bridge/tests/hot_cache_tests.rs` (NEW)

```rust
#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_lru_eviction_on_capacity_exceeded() {
        let cache = HotCache::new();
        
        // Fill to capacity
        for i in 0..cache.capacity {
            let rule_id = format!("rule-{}", i);
            let anchors = RuleVector::default();
            cache.insert(rule_id, anchors).unwrap();
        }
        
        assert_eq!(cache.cache.read().len(), cache.capacity);
        
        // Insert one more - should trigger eviction
        cache.insert(
            "rule-overflow".to_string(),
            RuleVector::default(),
        ).unwrap();
        
        // Should have evicted 10% and added new one
        let expected_size = cache.capacity - (cache.capacity / 10) + 1;
        assert_eq!(cache.cache.read().len(), expected_size);
    }
    
    #[test]
    fn test_evicts_least_recently_used() {
        let cache = HotCache::with_capacity(100);
        
        // Insert 3 rules
        for i in 0..3 {
            cache.insert(
                format!("rule-{}", i),
                RuleVector::default(),
            ).unwrap();
        }
        
        // Access rule-0 and rule-2 (not rule-1)
        cache.get("rule-0");
        cache.get("rule-2");
        
        // Fill to capacity
        for i in 3..100 {
            cache.insert(
                format!("rule-{}", i),
                RuleVector::default(),
            ).unwrap();
        }
        
        // Trigger eviction by inserting when full
        cache.insert(
            "rule-overflow".to_string(),
            RuleVector::default(),
        ).unwrap();
        
        // Rule-1 should be evicted (least recently used)
        assert!(cache.get("rule-1").is_none());
        assert!(cache.get("rule-0").is_some());
        assert!(cache.get("rule-2").is_some());
    }
    
    #[test]
    fn test_eviction_statistics() {
        let cache = HotCache::with_capacity(100);
        
        // Fill and trigger eviction
        for i in 0..110 {
            cache.insert(
                format!("rule-{}", i),
                RuleVector::default(),
            ).unwrap();
        }
        
        let stats = cache.stats();
        assert_eq!(stats.evictions, 10); // 10% of 100
    }
}
```

---

### Phase 3: Polish & Documentation (Days 9-10)

#### Task 3.1: Code Formatting & Linting

**Checklist**:

```bash
# Rust formatting
cd data_plane/tupl_dp/bridge
cargo fmt --all
cargo clippy --all -- -D warnings
cargo test --all

# Verify no unsafe code except where documented
grep -r "unsafe {" src/ | wc -l  # Should be minimal

# Python formatting (if MP code)
cd management_plane
black app/ --line-length 100
isort app/
pylint app/ --min-score=9.0
mypy app/
pytest tests/
```

**Standards Applied**:

- **Rust**:
  - Max function length: 100 lines
  - Max file length: 1000 lines
  - All public items documented with `///` comments
  - No `unwrap()` in production code
  - Zero clippy warnings (`-D warnings`)

- **Python**:
  - Black formatting (100 char lines)
  - Type hints on all function signatures
  - Google-style docstrings
  - Pylint score > 9.0

---

#### Task 3.2: Module Documentation

**Rust Files to Document**:

| File | Doc Focus |
|------|-----------|
| `refresh/scheduler.rs` | Background task lifecycle, interval timing |
| `refresh/service.rs` | Event-driven refresh vs scheduled |
| `storage/hot_cache.rs` (if exists) | LRU algorithm, capacity enforcement |
| `storage/types.rs` | Storage tier architecture |
| `enforcement_engine.rs` | Mark-evaluated integration |

**Documentation Template**:

```rust
//! Module description.
//!
//! # Overview
//! What this module does.
//!
//! # Design
//! Key architectural decisions.
//!
//! # Example
//! Usage example.
//!
//! # Performance
//! Time/space complexity where relevant.

/// Function description.
///
/// # Arguments
/// - `arg1`: What it does
/// - `arg2`: What it does
///
/// # Returns
/// What it returns and when error occurs.
///
/// # Example
/// ```
/// let result = my_function(arg1, arg2)?;
/// assert_eq!(result, expected);
/// ```
pub fn my_function(arg1: T, arg2: U) -> Result<V, String> {
    // Implementation
}
```

#### Task 3.3: Integration Guide Comments

Add comments to critical paths explaining integration:

**In `grpc_server.rs`**:

```rust
// Initialize refresh scheduler
// This spawns a background tokio task that reloads rules every 6 hours
// from warm storage into hot cache. Handles production restarts gracefully.
let scheduler = Arc::new(RefreshScheduler::new(
    bridge.clone(),
    SchedulerConfig::default(),
));

tokio::spawn({
    let sched = scheduler.clone();
    async move {
        sched.start().await
    }
});

// Log initialization
log::info!(
    "Data plane initialized: {} rules in hot cache, scheduled refresh enabled",
    bridge.hot_cache.read().len()
);
```

---

#### Task 3.4: Performance Benchmarks

**File**: `data_plane/tupl_dp/bridge/benches/storage.rs` (NEW)

```rust
use criterion::{black_box, criterion_group, criterion_main, Criterion};
use guard_bridge::storage::HotCache;
use guard_bridge::types::RuleVector;

fn bench_hot_cache_lookup(c: &mut Criterion) {
    let cache = HotCache::new();
    
    // Pre-populate cache
    for i in 0..1000 {
        cache.insert(format!("rule-{}", i), RuleVector::default()).unwrap();
    }
    
    c.bench_function("hot_cache_lookup_hit", |b| {
        b.iter(|| {
            cache.get(black_box("rule-500"))
        });
    });
    
    c.bench_function("hot_cache_insert", |b| {
        b.iter(|| {
            cache.insert(
                black_box("rule-new".to_string()),
                black_box(RuleVector::default()),
            )
        });
    });
}

fn bench_lru_eviction(c: &mut Criterion) {
    let cache = HotCache::with_capacity(100);
    
    // Fill to capacity
    for i in 0..100 {
        cache.insert(format!("rule-{}", i), RuleVector::default()).unwrap();
    }
    
    c.bench_function("lru_eviction_trigger", |b| {
        b.iter(|| {
            cache.insert(
                black_box("rule-overflow".to_string()),
                black_box(RuleVector::default()),
            )
        });
    });
}

criterion_group!(benches, bench_hot_cache_lookup, bench_lru_eviction);
criterion_main!(benches);
```

**Running benchmarks**:

```bash
cd data_plane/tupl_dp/bridge
cargo bench --bench storage
```

Expected baseline:
- Hot cache lookup (hit): <1μs
- Hot cache insert: <10μs
- LRU eviction: <100μs (depends on cache size)

---

## Code Quality Checklist

- [ ] All Rust code formatted with `cargo fmt`
- [ ] All Rust code passes `cargo clippy -- -D warnings`
- [ ] All Rust tests pass with `cargo test`
- [ ] All public Rust functions have `///` doc comments
- [ ] All modules have `//!` doc comments
- [ ] All Python code formatted with Black
- [ ] All Python code has type hints
- [ ] All Python functions have Google-style docstrings
- [ ] No `unwrap()` calls in production code
- [ ] No `todo!()` macros left in production code
- [ ] Benchmarks written and baseline established
- [ ] Integration tests verify LRU eviction behavior
- [ ] Scheduler logs refresh operations
- [ ] Error handling covers all failure modes

---

## Testing Strategy

### Unit Tests

**Hot Cache Tests** (`tests/hot_cache_tests.rs`):
- Capacity enforcement
- LRU eviction on overflow
- Timestamp tracking
- Statistics accuracy

**Scheduler Tests** (`tests/refresh_scheduler_tests.rs`):
- Background task spawning
- Interval timing
- Warm storage loading
- Error handling

### Integration Tests

**Rule Lifecycle** (`tests/integration/rule_lifecycle.rs`):
1. Install rules → hot cache
2. Let cache evict (fill beyond 10K)
3. Verify rules moved to warm
4. Trigger scheduled refresh
5. Verify rules in hot cache

**Persistence** (`tests/integration/persistence.rs`):
1. Install 5K rules
2. Restart service
3. Verify warm storage loaded
4. Enforce rules → same results

---

## Success Criteria

✅ **Scheduled Refresh**:
- Background task starts on data plane init
- Reloads warm storage every 6 hours
- Logs refresh statistics (count, duration)
- Handles errors gracefully (logs, continues)

✅ **LRU Eviction**:
- Inserts respect 10K capacity
- Evicts 10% of capacity when full
- Selects oldest by `last_evaluated_at`
- Maintains statistics (eviction count)

✅ **Production Ready**:
- All code formatted and linted
- All modules documented
- All tests passing
- Benchmarks establish baseline
- No warnings or unsafe code

---

## Timeline

| Phase | Tasks | Days | Status |
|-------|-------|------|--------|
| 1 | Scheduled refresh service | 1-1.5 | Pending |
| 2 | LRU eviction implementation | 1-2 | Pending |
| 3a | Code formatting & linting | 1 | Pending |
| 3b | Documentation & comments | 1.5 | Pending |
| 3c | Benchmarks & tests | 1 | Pending |

**Total**: ~5-6 days of focused implementation + 1-2 days for testing and integration

---

## Dependencies & Risk Mitigation

**Dependencies**:
- ✅ Week 1-2 must be complete (storage, refresh service already built)
- Warm storage must be functional and tested

**Risks**:
1. **LRU eviction performance**: Sort on every eviction could be expensive
   - *Mitigation*: 10% batch eviction + benchmark to verify <100μs
   
2. **Scheduler drift**: 6-hour interval may drift over weeks
   - *Mitigation*: Use tokio `interval()` which maintains wall-clock accuracy
   
3. **Promotion under load**: Rules evicted→warm→promoted could cause cache thrashing
   - *Mitigation*: Monitor eviction stats in production, adjust capacity as needed

---

## Deliverables

| Item | File | Status |
|------|------|--------|
| Scheduler implementation | `refresh/scheduler.rs` | To create |
| LRU eviction logic | `storage/types.rs` + enforcement update | To modify |
| Unit tests | `tests/hot_cache_tests.rs`, `refresh_scheduler_tests.rs` | To create |
| Integration tests | `tests/integration/rule_lifecycle.rs` | To verify |
| Documentation | All module `//!` comments | To add |
| Benchmarks | `benches/storage.rs` | To create |
| Code formatting | Format all modified code | To apply |
| Completion summary | `04-week3-completion-summary.md` | To create |

