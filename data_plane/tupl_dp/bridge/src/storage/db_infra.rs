use reqwest::blocking::Client;
use serde::{de::DeserializeOwned, Deserialize, Serialize};

#[derive(Debug, Clone)]
pub struct DbInfraClient {
    base_url: String,
    client: Client,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrismRuleRecord {
    pub rule_id: String,
    pub tenant_id: String,
    pub layer: Option<String>,
    pub priority: i64,
    pub rule_json: String,
    pub anchors_json: String,
    pub status: String,
    pub updated_at: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrismHitlogRecord {
    pub session_id: String,
    pub tenant_id: Option<String>,
    pub agent_id: Option<String>,
    pub layer: Option<String>,
    pub timestamp_ms: i64,
    pub final_decision: i32,
    pub duration_us: i64,
    pub session_json: String,
}

#[derive(Debug, Default, Clone)]
pub struct HitlogQueryParams {
    pub session_id: Option<String>,
    pub tenant_id: Option<String>,
    pub agent_id: Option<String>,
    pub layer: Option<String>,
    pub decision: Option<i32>,
    pub start_time_ms: Option<i64>,
    pub end_time_ms: Option<i64>,
    pub limit: Option<usize>,
    pub offset: Option<usize>,
}

#[derive(Debug, Deserialize)]
struct RuleListResponse {
    rules: Vec<PrismRuleRecord>,
}

#[derive(Debug, Deserialize)]
struct HitlogListResponse {
    sessions: Vec<PrismHitlogRecord>,
    total: usize,
}

impl DbInfraClient {
    pub fn new(base_url: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into().trim_end_matches('/').to_string(),
            client: Client::builder()
                .timeout(std::time::Duration::from_secs(30))
                .build()
                .expect("failed to build reqwest blocking client"),
        }
    }

    fn service_headers(
        &self,
        builder: reqwest::blocking::RequestBuilder,
    ) -> reqwest::blocking::RequestBuilder {
        builder
            .header("X-DB-Infra-Service", "prism_data_plane")
            .header("Accept", "application/json")
    }

    fn parse_json<T: DeserializeOwned>(&self, response: reqwest::blocking::Response) -> Result<T, String> {
        let status = response.status();
        if status.is_success() {
            response
                .json::<T>()
                .map_err(|e| format!("Failed to parse db_infra response JSON: {}", e))
        } else {
            let body = response.text().unwrap_or_default();
            Err(format!("db_infra request failed: {} {}", status, body))
        }
    }

    pub fn list_active_rules(&self) -> Result<Vec<PrismRuleRecord>, String> {
        let response = self
            .service_headers(
                self.client
                    .get(format!("{}/api/v1/prism-data-plane/rules", self.base_url))
                    .query(&[("status", "active")]),
            )
            .send()
            .map_err(|e| format!("Failed to query prism_data_plane rules: {}", e))?;
        let payload: RuleListResponse = self.parse_json(response)?;
        Ok(payload.rules)
    }

    pub fn upsert_rule(&self, record: &PrismRuleRecord) -> Result<(), String> {
        let response = self
            .service_headers(
                self.client
                    .post(format!("{}/api/v1/prism-data-plane/rules", self.base_url))
                    .json(record),
            )
            .send()
            .map_err(|e| format!("Failed to upsert prism_data_plane rule: {}", e))?;
        let _: PrismRuleRecord = self.parse_json(response)?;
        Ok(())
    }

    pub fn delete_rule(&self, rule_id: &str) -> Result<(), String> {
        let response = self
            .service_headers(
                self.client.delete(format!(
                    "{}/api/v1/prism-data-plane/rules/{}",
                    self.base_url, rule_id
                )),
            )
            .send()
            .map_err(|e| format!("Failed to delete prism_data_plane rule: {}", e))?;
        let _: serde_json::Value = self.parse_json(response)?;
        Ok(())
    }

    pub fn clear_rules(&self) -> Result<(), String> {
        let response = self
            .service_headers(
                self.client
                    .delete(format!("{}/api/v1/prism-data-plane/rules", self.base_url)),
            )
            .send()
            .map_err(|e| format!("Failed to clear prism_data_plane rules: {}", e))?;
        let _: serde_json::Value = self.parse_json(response)?;
        Ok(())
    }

    pub fn upsert_hitlog(&self, record: &PrismHitlogRecord) -> Result<(), String> {
        let response = self
            .service_headers(
                self.client
                    .post(format!("{}/api/v1/prism-data-plane/hitlogs", self.base_url))
                    .json(record),
            )
            .send()
            .map_err(|e| format!("Failed to upsert prism_data_plane hitlog: {}", e))?;
        let _: PrismHitlogRecord = self.parse_json(response)?;
        Ok(())
    }

    pub fn get_hitlog(&self, session_id: &str) -> Result<Option<PrismHitlogRecord>, String> {
        let response = self
            .service_headers(
                self.client.get(format!(
                    "{}/api/v1/prism-data-plane/hitlogs/{}",
                    self.base_url, session_id
                )),
            )
            .send()
            .map_err(|e| format!("Failed to fetch prism_data_plane hitlog: {}", e))?;

        if response.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }
        let payload: PrismHitlogRecord = self.parse_json(response)?;
        Ok(Some(payload))
    }

    pub fn query_hitlogs(
        &self,
        params: &HitlogQueryParams,
    ) -> Result<(Vec<PrismHitlogRecord>, usize), String> {
        let mut query: Vec<(&str, String)> = Vec::new();
        if let Some(ref value) = params.session_id {
            query.push(("session_id", value.clone()));
        }
        if let Some(ref value) = params.tenant_id {
            query.push(("tenant_id", value.clone()));
        }
        if let Some(ref value) = params.agent_id {
            query.push(("agent_id", value.clone()));
        }
        if let Some(ref value) = params.layer {
            query.push(("layer", value.clone()));
        }
        if let Some(value) = params.decision {
            query.push(("decision", value.to_string()));
        }
        if let Some(value) = params.start_time_ms {
            query.push(("start_time_ms", value.to_string()));
        }
        if let Some(value) = params.end_time_ms {
            query.push(("end_time_ms", value.to_string()));
        }
        if let Some(value) = params.limit {
            query.push(("limit", value.to_string()));
        }
        if let Some(value) = params.offset {
            query.push(("offset", value.to_string()));
        }

        let response = self
            .service_headers(
                self.client
                    .get(format!("{}/api/v1/prism-data-plane/hitlogs", self.base_url))
                    .query(&query),
            )
            .send()
            .map_err(|e| format!("Failed to query prism_data_plane hitlogs: {}", e))?;
        let payload: HitlogListResponse = self.parse_json(response)?;
        Ok((payload.sessions, payload.total))
    }
}
