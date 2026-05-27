//! # Bridge Data Plane Entry Point
//!
//! Main executable that initializes the bridge and starts the gRPC server
//! for receiving rule installation requests from the control plane.

use bridge::{grpc_server::start_grpc_server, Bridge};
use fencio_logger::{registration_payload, LoggerConfig};
use log::{error, info, warn};
use reqwest::Client;
use std::sync::Arc;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let logger_config = fencio_logger::init("prism_data_plane")?;
    register_logger_with_db_infra(&logger_config).await;

    info!("TUPL Data Plane - Bridge & Rule Installation");
    info!("Initializing Bridge with db_infra-backed storage...");
    let bridge_inst = match Bridge::init() {
        Ok(bridge) => Arc::new(bridge),
        Err(e) => {
            error!("Failed to initialize Bridge: {}", e);
            return Err(e.into());
        }
    };
    info!("Bridge initialized");
    info!("Rules loaded: {}", bridge_inst.rule_count());
    info!("Bridge version: {}", bridge_inst.version());

    // Start gRPC server for rule installation and enforcement
    let grpc_port = 50051;
    let management_plane_url = std::env::var("MANAGEMENT_PLANE_URL")
        .unwrap_or_else(|_| "http://localhost:8001/api/v2".to_string());

    info!("Starting gRPC server for rule installation and enforcement");
    info!("Listening on 0.0.0.0:{}", grpc_port);
    info!("Management Plane: {}", management_plane_url);

    // Start the gRPC server
    start_grpc_server(bridge_inst, grpc_port, management_plane_url).await?;

    info!("Data Plane shut down");

    Ok(())
}

async fn register_logger_with_db_infra(config: &LoggerConfig) {
    let Ok(base_url) = std::env::var("DB_INFRA_BASE_URL") else {
        info!("Logger registration skipped because DB_INFRA_BASE_URL is not configured");
        return;
    };
    let base_url = base_url.trim_end_matches('/');
    if base_url.is_empty() {
        info!("Logger registration skipped because DB_INFRA_BASE_URL is empty");
        return;
    }

    let payload = registration_payload(config);
    let response = Client::new()
        .post(format!("{base_url}/api/v1/logger/services/register"))
        .header("Accept", "application/json")
        .header("Content-Type", "application/json")
        .header("X-DB-Infra-Service", "logger")
        .json(&payload)
        .send()
        .await;

    match response {
        Ok(response) if response.status().is_success() => {
            info!("Logger registration persisted in db_infra");
        }
        Ok(response) => {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            warn!(
                "Logger registration failed in db_infra status={} body={}",
                status, body
            );
        }
        Err(error) => {
            warn!("Logger registration request failed: {}", error);
        }
    }
}
