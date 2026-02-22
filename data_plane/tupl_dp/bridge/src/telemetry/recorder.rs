//! # Telemetry Recorder
//!
//! Thread-safe telemetry collection and session management.

use super::session::{EnforcementSession, SessionId};
use super::writer::{HitlogConfig, HitlogWriter};
use parking_lot::RwLock;
use std::collections::HashMap;
use std::sync::Arc;
use uuid::Uuid;

/// Configuration for telemetry recording
#[derive(Debug, Clone)]
pub struct TelemetryConfig {
    /// Enable telemetry recording
    pub enabled: bool,

    /// Directory for hitlog files
    pub hitlog_dir: String,

    /// Sample rate (0.0 - 1.0). 1.0 = record all
    pub sample_rate: f64,

    /// Maximum sessions to buffer before forcing write
    pub buffer_size: usize,

    /// Force write every N seconds
    pub flush_interval_secs: u64,

    /// Enable per-session performance tracking
    pub track_performance: bool,

    /// Enable detailed slice-level comparisons
    pub track_slice_details: bool,
}

impl Default for TelemetryConfig {
    fn default() -> Self {
        TelemetryConfig {
            enabled: true,
            hitlog_dir: "/var/hitlogs".to_string(),
            sample_rate: 1.0,
            buffer_size: 100,
            flush_interval_secs: 5,
            track_performance: true,
            track_slice_details: true,
        }
    }
}

/// Thread-safe telemetry recorder
pub struct TelemetryRecorder {
    /// Configuration
    config: TelemetryConfig,

    /// Active sessions (session_id -> session)
    active_sessions: Arc<RwLock<HashMap<SessionId, EnforcementSession>>>,

    /// Hitlog writer
    writer: Arc<HitlogWriter>,

    /// Session counter for stats
    total_sessions: Arc<RwLock<u64>>,

    /// Blocked sessions counter
    blocked_sessions: Arc<RwLock<u64>>,

    /// Allowed sessions counter
    allowed_sessions: Arc<RwLock<u64>>,
}

impl TelemetryRecorder {
    /// Create a new telemetry recorder
    pub fn new(config: TelemetryConfig) -> Result<Self, String> {
        // Create hitlog writer
        let hitlog_config = HitlogConfig::from_telemetry_config(&config);
        let writer = HitlogWriter::new(hitlog_config)?;

        Ok(TelemetryRecorder {
            config,
            active_sessions: Arc::new(RwLock::new(HashMap::new())),
            writer: Arc::new(writer),
            total_sessions: Arc::new(RwLock::new(0)),
            blocked_sessions: Arc::new(RwLock::new(0)),
            allowed_sessions: Arc::new(RwLock::new(0)),
        })
    }

    /// Start a new enforcement session
    pub fn start_session(&self, layer: String, intent_json: String, request_id: &str) -> Option<SessionId> {
        if !self.config.enabled {
            return None;
        }

        // Apply sampling
        if self.config.sample_rate < 1.0 {
            use rand::Rng;
            let mut rng = rand::thread_rng();
            if rng.gen::<f64>() > self.config.sample_rate {
                return None; // Skip this session
            }
        }

        // Use caller-supplied request_id if non-empty, otherwise generate UUID
        let session_id = if request_id.is_empty() {
            Uuid::new_v4().to_string()
        } else {
            request_id.to_string()
        };

        // Create session
        let session = EnforcementSession::new(session_id.clone(), layer, intent_json);

        // Store in active sessions
        self.active_sessions
            .write()
            .insert(session_id.clone(), session);

        // Increment counter
        *self.total_sessions.write() += 1;

        Some(session_id)
    }

    /// Get a mutable reference to an active session
    pub fn with_session<F, R>(&self, session_id: &str, f: F) -> Option<R>
    where
        F: FnOnce(&mut EnforcementSession) -> R,
    {
        let mut sessions = self.active_sessions.write();
        sessions.get_mut(session_id).map(f)
    }

    /// Complete a session and write to hitlog
    pub fn complete_session(
        &self,
        session_id: &str,
        final_decision: u8,
        total_duration_us: u64,
    ) -> Result<(), String> {
        // Remove from active sessions
        let mut session = {
            let mut sessions = self.active_sessions.write();
            sessions.remove(session_id).ok_or("Session not found")?
        };

        // Finalize session
        session.finalize(final_decision, total_duration_us);

        // Update stats
        if final_decision == 0 {
            *self.blocked_sessions.write() += 1;
        } else {
            *self.allowed_sessions.write() += 1;
        }

        // Write to hitlog
        self.writer.write_session(&session)?;

        Ok(())
    }

    /// Abandon a session (e.g., on error)
    pub fn abandon_session(&self, session_id: &str) {
        self.active_sessions.write().remove(session_id);
    }

    /// Flush all pending writes
    pub fn flush(&self) -> Result<(), String> {
        self.writer.flush()
    }

    /// Get telemetry statistics
    pub fn stats(&self) -> TelemetryStats {
        TelemetryStats {
            total_sessions: *self.total_sessions.read(),
            blocked_sessions: *self.blocked_sessions.read(),
            allowed_sessions: *self.allowed_sessions.read(),
            active_sessions: self.active_sessions.read().len(),
            sample_rate: self.config.sample_rate,
        }
    }

    /// Check if telemetry is enabled
    pub fn is_enabled(&self) -> bool {
        self.config.enabled
    }
}

/// Telemetry statistics
#[derive(Debug, Clone)]
pub struct TelemetryStats {
    pub total_sessions: u64,
    pub blocked_sessions: u64,
    pub allowed_sessions: u64,
    pub active_sessions: usize,
    pub sample_rate: f64,
}

impl TelemetryStats {
    pub fn block_rate(&self) -> f64 {
        if self.total_sessions == 0 {
            0.0
        } else {
            self.blocked_sessions as f64 / self.total_sessions as f64
        }
    }

    pub fn allow_rate(&self) -> f64 {
        if self.total_sessions == 0 {
            0.0
        } else {
            self.allowed_sessions as f64 / self.total_sessions as f64
        }
    }
}
