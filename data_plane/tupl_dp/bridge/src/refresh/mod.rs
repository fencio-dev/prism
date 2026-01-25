//! Rule refresh service - reload rules from persistent storage.
//!
//! Provides two refresh mechanisms:
//! 1. **Event-driven refresh** (RefreshService): Triggered on-demand via gRPC
//! 2. **Scheduled refresh** (RefreshScheduler): Periodic background task (6-hour default)

pub mod scheduler;
pub mod service;

pub use scheduler::{RefreshScheduler, SchedulerConfig};
pub use service::{RefreshService, RefreshStats};
