//! Tiered storage system for rule vectors.
//!
//! Three-tier architecture:
//! - **Hot**: In-memory HashMap (10K rules, <1μs)
//! - **Warm**: Memory-mapped file (100K rules, ~10μs)
//! - **Cold**: SQLite database (unlimited, ~100μs)
//!
//! Rules are promoted from cold→warm→hot on access.
//! Rules are demoted from hot→warm when cache is full.

pub mod cold_storage;
pub mod types;
pub mod warm_storage;

pub use cold_storage::ColdStorage;
pub use types::{CachedRuleVector, StorageStats, StorageTier};
pub use warm_storage::WarmStorage;

// Will implement in subsequent tasks:
// pub mod hot_cache;
