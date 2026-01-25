//! # Telemetry Module - conntrack for Tupl Data Plane
//!
//! Records every intent evaluation with complete visibility into:
//! - Intent details
//! - Rules evaluated
//! - Per-rule decisions
//! - Final enforcement outcome
//! - Performance metrics
//!
//! Analogous to Linux conntrack, providing complete audit trail of all
//! enforcement decisions.

pub mod query;
pub mod recorder;
pub mod session;
pub mod writer;

pub use query::{HitlogQuery, QueryFilter};
pub use recorder::{TelemetryConfig, TelemetryRecorder};
pub use session::{EnforcementSession, RuleEvaluationEvent, SessionEvent};
pub use writer::{HitlogConfig, HitlogWriter, RotationPolicy};
