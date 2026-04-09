//! # Hitlog Writer
//!
//! db_infra-backed writer for enforcement session logs.

use super::recorder::TelemetryConfig;
use super::session::EnforcementSession;
use crate::storage::db_infra::{DbInfraClient, PrismHitlogRecord};

/// Hitlog writer configuration
#[derive(Debug, Clone)]
pub struct HitlogConfig {
    pub db_infra_base_url: String,
}

impl Default for HitlogConfig {
    fn default() -> Self {
        HitlogConfig {
            db_infra_base_url: std::env::var("DB_INFRA_BASE_URL")
                .unwrap_or_else(|_| "http://localhost:8020".to_string()),
        }
    }
}

impl HitlogConfig {
    pub fn from_telemetry_config(config: &TelemetryConfig) -> Self {
        HitlogConfig {
            db_infra_base_url: config.db_infra_base_url.clone(),
        }
    }
}

/// Thread-safe hitlog writer
pub struct HitlogWriter {
    client: DbInfraClient,
}

impl HitlogWriter {
    pub fn new(config: HitlogConfig) -> Result<Self, String> {
        Ok(Self {
            client: DbInfraClient::new(config.db_infra_base_url),
        })
    }

    pub fn write_session(&self, session: &EnforcementSession) -> Result<(), String> {
        let session_json = serde_json::to_string(session)
            .map_err(|e| format!("Failed to serialize session: {}", e))?;

        self.client.upsert_hitlog(&PrismHitlogRecord {
            session_id: session.session_id.clone(),
            tenant_id: session.tenant_id.clone(),
            agent_id: session.agent_id.clone(),
            layer: Some(session.layer.clone()),
            timestamp_ms: session.timestamp_ms as i64,
            final_decision: session.final_decision as i32,
            duration_us: session.duration_us as i64,
            session_json,
        })
    }

    pub fn flush(&self) -> Result<(), String> {
        Ok(())
    }
}
