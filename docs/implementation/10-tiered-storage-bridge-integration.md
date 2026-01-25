# Task 2.5: Tiered Storage Integration into Bridge

**Status**: ✅ COMPLETED  
**Date**: January 25, 2026  
**Duration**: ~2 hours  

## Summary

Successfully integrated 3-tier storage architecture into the Bridge struct. The Bridge now manages rule anchors across:
- **Hot**: In-memory HashMap (< 1μs)
- **Warm**: Memory-mapped file (~10μs)
- **Cold**: SQLite database (~100μs)

All existing tests pass, plus 6 new tests added for tiered storage functionality.

## Changes Made

### 1. **bridge.rs** - Core Bridge Implementation
- **Lines changed**: 297 insertions, 16 deletions
- **Files modified**: `src/bridge.rs`

#### StorageConfig Structure (NEW)
```rust
pub struct StorageConfig {
    pub warm_storage_path: PathBuf,
    pub cold_storage_path: PathBuf,
}

impl Default for StorageConfig { }  // Uses ./var/data/ paths
```

#### Bridge Structure (UPDATED)
Replaced:
- `rule_anchors: Arc<RwLock<HashMap<String, RuleVector>>>`

With 3-tier storage:
- `hot_cache: Arc<RwLock<HashMap<String, RuleVector>>>`
- `warm_storage: Arc<WarmStorage>`
- `cold_storage: Arc<ColdStorage>`
- `storage_config: StorageConfig`

#### Bridge::init() -> Result (UPDATED)
Changed from `init() -> Self` to `init() -> Result<Self, String>` to handle storage initialization errors.

#### New Methods

**Bridge::new(StorageConfig) -> Result<Self, String>**
- Initializes all 14 rule family tables
- Creates warm/cold storage instances
- Loads warm storage into hot cache on startup

**Bridge::with_defaults() -> Result<Self, String>**
- Convenience method using default storage paths

**Bridge::add_rule_with_anchors()** (UPDATED)
- Now persists anchors to warm storage after adding to hot cache
- Releases locks during I/O to prevent contention

**Bridge::get_rule_anchors()** (UPDATED)
- Implements tiered lookup: hot → warm → cold
- Promotes hits from warm/cold to hot cache
- Returns None if not found in any tier

**Bridge::storage_stats() -> StorageStats** (NEW)
- Returns storage statistics across tiers
- Currently returns hot cache size (extensible for warm/cold)

### 2. **storage/warm_storage.rs** - Memory-Mapped Storage
- **Change**: Added custom Debug implementation for WarmStorage
- **Reason**: Mmap doesn't implement Debug; custom impl shows path and entry count

```rust
impl std::fmt::Debug for WarmStorage {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WarmStorage")
            .field("path", &self.path)
            .field("index_entries", &self.index.read().len())
            .finish()
    }
}
```

### 3. **storage/cold_storage.rs** - SQLite Storage
- **Change**: Added `#[derive(Debug)]` macro
- **Reason**: Required for Bridge struct to implement Debug

### 4. **main.rs** - Application Entry Point
- **Change**: Updated Bridge::init() call to handle Result
- **Before**:
  ```rust
  let bridge_inst = Arc::new(Bridge::init());
  ```
- **After**:
  ```rust
  let bridge_inst = match Bridge::init() {
      Ok(bridge) => Arc::new(bridge),
      Err(e) => {
          eprintln!("✗ Failed to initialize Bridge: {}", e);
          return Err(e.into());
      }
  };
  ```

### 5. **lib.rs** - Module Exports
- No changes needed (storage module already exported)

## Test Results

### Existing Tests
- ✅ 17 existing tests still pass
- ✅ API types roundtrip tests
- ✅ Vector comparison tests
- ✅ Storage module tests (warm & cold)

### New Tests (6 total)
All in `bridge::tests` module:

1. **test_bridge_init_with_storage**
   - Verifies 14 tables are created
   - Confirms hot cache is empty on init

2. **test_hot_cache_lookup_empty**
   - Verifies get_rule_anchors returns None for non-existent rules

3. **test_warm_storage_reload_on_startup**
   - Tests that warm storage is reloaded when Bridge starts
   - Simulates restart persistence

4. **test_storage_config_defaults**
   - Verifies default paths are set correctly
   - Confirms expected storage locations

5. **test_storage_stats_reflects_hot_cache**
   - Verifies storage_stats() returns correct hot cache size
   - Tests initial state is 0

6. **test_bridge_with_defaults**
   - Ensures with_defaults() convenience method works
   - Verifies no panic with actual default paths

**Final Test Count**: 23 passed (0 failed)

## Compilation Status

```
✅ Library: CLEAN (0 errors, 18 warnings - pre-existing)
✅ Tests: ALL PASS (23/23)
✅ Binary: Pre-existing errors unrelated to this task
```

## Architecture Validation

### Tiered Lookup Flow
```
get_rule_anchors("rule-123")
    ↓
[1] Check hot_cache (Arc<RwLock<HashMap>>)
    ├─ FOUND: Return immediately
    └─ MISS: Continue to [2]
    ↓
[2] Check warm_storage.get() (mmap file)
    ├─ FOUND: Insert into hot_cache, return
    └─ MISS: Continue to [3]
    ↓
[3] Check cold_storage.get() (SQLite)
    ├─ FOUND: Insert into hot_cache, return
    └─ MISS: Return None
```

### Write Flow (add_rule_with_anchors)
```
add_rule_with_anchors(rule, anchors)
    ↓
[1] Add to RuleFamilyTable (unchanged)
    ↓
[2] Insert into hot_cache (fast)
    ↓
[3] Release locks, persist to warm_storage (I/O)
    ↓
[4] Increment version
    ↓
Done
```

## Key Design Decisions

1. **Immediate Warm Persistence**: Anchors written to warm storage on add, not lazily
   - Trade-off: Slight latency increase for guaranteed persistence

2. **Automatic Promotion**: Hits from warm/cold automatically added to hot cache
   - Benefit: Working set gravitates to hot tier naturally

3. **Hot Cache Size Unlimited**: No LRU or eviction yet
   - Future: Can implement per-tier size limits

4. **Backward Compatibility**: RuleInstance tables unchanged
   - Only RuleVector anchors moved to tiered storage

## Files Modified Summary

| File | Type | Changes |
|------|------|---------|
| `bridge.rs` | Core | +297 / -16 (net +281) |
| `warm_storage.rs` | Storage | +14 (Debug impl) |
| `cold_storage.rs` | Storage | +1 (#[derive]) |
| `main.rs` | Main | +5 (Error handling) |

**Total Lines Added**: ~317  
**Total Files Modified**: 4  
**Total Tests Added**: 6

## Known Limitations & Future Work

1. **Hot Cache Unbounded**: Should implement LRU with configurable size
2. **Passive Warm/Cold Stats**: Could query warm/cold storage for full statistics
3. **Binary Build**: Unrelated errors in grpc/protobuf compilation
4. **Storage Directories**: Default paths created at runtime, could be pre-created

## Verification Checklist

- [x] Bridge struct updated with 3-tier storage fields
- [x] add_rule_with_anchors() writes to warm storage
- [x] get_rule_anchors() implements tiered lookup with promotion
- [x] Warm storage loaded into hot cache on startup
- [x] `cargo build --lib` succeeds with 0 errors
- [x] All existing Bridge tests pass
- [x] New integration tests pass (6/6)
- [x] Debug impl added for WarmStorage
- [x] Error handling updated in main.rs

## References

- Design Doc: `docs/plan/02-tiered-storage.md`
- Storage Types: `src/storage/types.rs`
- Warm Storage: `src/storage/warm_storage.rs`
- Cold Storage: `src/storage/cold_storage.rs`
- Bridge Tests: `src/bridge.rs::tests`
