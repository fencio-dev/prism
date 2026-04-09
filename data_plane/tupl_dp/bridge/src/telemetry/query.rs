//! # Hitlog Query Utility
//!
//! Query and analyze enforcement sessions from db_infra-backed telemetry.

use super::session::EnforcementSession;
use crate::storage::db_infra::{DbInfraClient, HitlogQueryParams};

/// Query filter for searching hitlogs
#[derive(Debug, Clone, Default)]
pub struct QueryFilter {
    pub session_id: Option<String>,
    pub layer: Option<String>,
    pub agent_id: Option<String>,
    pub tenant_id: Option<String>,
    pub decision: Option<u8>,
    pub start_time_ms: Option<u64>,
    pub end_time_ms: Option<u64>,
    pub min_duration_us: Option<u64>,
    pub max_duration_us: Option<u64>,
    pub rule_id: Option<String>,
    pub limit: Option<usize>,
    pub offset: Option<usize>,
}

/// Query results
#[derive(Debug)]
pub struct QueryResult {
    pub sessions: Vec<EnforcementSession>,
    pub total_matched: usize,
    pub files_searched: usize,
}

/// Hitlog query engine
pub struct HitlogQuery {
    client: DbInfraClient,
}

impl HitlogQuery {
    pub fn new(db_infra_base_url: impl AsRef<str>) -> Self {
        HitlogQuery {
            client: DbInfraClient::new(db_infra_base_url.as_ref().to_string()),
        }
    }

    pub fn query(&self, filter: &QueryFilter) -> Result<QueryResult, String> {
        let params = HitlogQueryParams {
            session_id: filter.session_id.clone(),
            tenant_id: filter.tenant_id.clone(),
            agent_id: filter.agent_id.clone(),
            layer: filter.layer.clone(),
            decision: filter.decision.map(|value| value as i32),
            start_time_ms: filter.start_time_ms.map(|value| value as i64),
            end_time_ms: filter.end_time_ms.map(|value| value as i64),
            limit: filter.limit,
            offset: filter.offset,
        };

        let (rows, total) = self.client.query_hitlogs(&params)?;
        let mut sessions = Vec::new();
        for row in rows {
            let session: EnforcementSession = match serde_json::from_str(&row.session_json) {
                Ok(value) => value,
                Err(_) => continue,
            };
            if self.matches_filter(&session, filter) {
                sessions.push(session);
            }
        }

        Ok(QueryResult {
            sessions,
            total_matched: total,
            files_searched: 0,
        })
    }

    pub fn recent(&self, limit: usize) -> Result<Vec<EnforcementSession>, String> {
        let filter = QueryFilter {
            limit: Some(limit),
            ..Default::default()
        };
        Ok(self.query(&filter)?.sessions)
    }

    pub fn blocked(&self, limit: Option<usize>) -> Result<Vec<EnforcementSession>, String> {
        let filter = QueryFilter {
            decision: Some(0),
            limit,
            ..Default::default()
        };
        Ok(self.query(&filter)?.sessions)
    }

    pub fn by_agent(
        &self,
        agent_id: String,
        limit: Option<usize>,
    ) -> Result<Vec<EnforcementSession>, String> {
        let filter = QueryFilter {
            agent_id: Some(agent_id),
            limit,
            ..Default::default()
        };
        Ok(self.query(&filter)?.sessions)
    }

    pub fn by_time_range(
        &self,
        start_ms: u64,
        end_ms: u64,
        limit: Option<usize>,
    ) -> Result<Vec<EnforcementSession>, String> {
        let filter = QueryFilter {
            start_time_ms: Some(start_ms),
            end_time_ms: Some(end_ms),
            limit,
            ..Default::default()
        };
        Ok(self.query(&filter)?.sessions)
    }

    fn matches_filter(&self, session: &EnforcementSession, filter: &QueryFilter) -> bool {
        if let Some(ref session_id) = filter.session_id {
            if &session.session_id != session_id {
                return false;
            }
        }
        if let Some(ref layer) = filter.layer {
            if &session.layer != layer {
                return false;
            }
        }
        if let Some(ref agent_id) = filter.agent_id {
            if session.agent_id.as_ref() != Some(agent_id) {
                return false;
            }
        }
        if let Some(ref tenant_id) = filter.tenant_id {
            if session.tenant_id.as_ref() != Some(tenant_id) {
                return false;
            }
        }
        if let Some(decision) = filter.decision {
            if session.final_decision != decision {
                return false;
            }
        }
        if let Some(start_time) = filter.start_time_ms {
            if session.timestamp_ms < start_time {
                return false;
            }
        }
        if let Some(end_time) = filter.end_time_ms {
            if session.timestamp_ms > end_time {
                return false;
            }
        }
        if let Some(min_duration) = filter.min_duration_us {
            if session.duration_us < min_duration {
                return false;
            }
        }
        if let Some(max_duration) = filter.max_duration_us {
            if session.duration_us > max_duration {
                return false;
            }
        }
        if let Some(ref rule_id) = filter.rule_id {
            if !session.rules_evaluated.iter().any(|r| &r.rule_id == rule_id) {
                return false;
            }
        }
        true
    }

    pub fn statistics(&self) -> Result<HitlogStatistics, String> {
        let all_sessions = self.query(&QueryFilter::default())?;
        let total_sessions = all_sessions.sessions.len();
        let blocked = all_sessions
            .sessions
            .iter()
            .filter(|s| s.final_decision == 0)
            .count();
        let allowed = all_sessions
            .sessions
            .iter()
            .filter(|s| s.final_decision == 1)
            .count();

        let total_duration_us: u64 = all_sessions.sessions.iter().map(|s| s.duration_us).sum();
        let avg_duration_us = if total_sessions > 0 {
            total_duration_us / total_sessions as u64
        } else {
            0
        };

        let total_rules_evaluated: usize = all_sessions
            .sessions
            .iter()
            .map(|s| s.rules_evaluated.len())
            .sum();
        let avg_rules_per_session = if total_sessions > 0 {
            total_rules_evaluated as f64 / total_sessions as f64
        } else {
            0.0
        };

        Ok(HitlogStatistics {
            total_sessions,
            blocked,
            allowed,
            block_rate: if total_sessions > 0 {
                blocked as f64 / total_sessions as f64
            } else {
                0.0
            },
            avg_duration_us,
            avg_rules_per_session,
        })
    }
}

#[derive(Debug, Clone)]
pub struct HitlogStatistics {
    pub total_sessions: usize,
    pub blocked: usize,
    pub allowed: usize,
    pub block_rate: f64,
    pub avg_duration_us: u64,
    pub avg_rules_per_session: f64,
}
