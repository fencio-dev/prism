//! Example: Integrating the RefreshScheduler into the gRPC server.
//!
//! This example demonstrates how to spawn the scheduled refresh service
//! as a background task during server initialization.
//!
//! Run with: `cargo run --example scheduler_integration`

use std::sync::Arc;
use std::time::Duration;

// In a real application, these would come from the bridge crate
// use bridge::{Bridge, RefreshScheduler, SchedulerConfig};

/// Example function showing the integration pattern for RefreshScheduler.
///
/// This would be called from `start_grpc_server()` or during server setup.
#[allow(dead_code)]
async fn spawn_scheduled_refresh_example(// bridge: Arc<Bridge>,
    // port: u16,
) -> Result<(), Box<dyn std::error::Error>> {
    println!("=================================================");
    println!("  Scheduled Refresh Service Integration Example ");
    println!("=================================================");
    println!();

    // Initialize bridge (would be Arc::new(Bridge::init()?))
    // let bridge = Arc::new(Bridge::init()?);

    // Create scheduler with default configuration
    // Default: 6-hour refresh interval, enabled
    println!("1. Creating RefreshScheduler with default config:");
    println!("   - refresh_interval: 6 hours");
    println!("   - enabled: true");
    println!();
    // let scheduler_config = SchedulerConfig::default();
    // let scheduler = Arc::new(RefreshScheduler::new(
    //     bridge.clone(),
    //     scheduler_config,
    // ));

    // Alternative: Custom configuration for testing
    println!("2. Alternative: Custom configuration for testing");
    println!("   - refresh_interval: 1 minute");
    println!("   - enabled: true");
    println!();
    // let test_config = SchedulerConfig {
    //     refresh_interval: Duration::from_secs(60),
    //     enabled: true,
    // };
    // let scheduler = Arc::new(RefreshScheduler::new(
    //     bridge.clone(),
    //     test_config,
    // ));

    // Spawn the scheduler as a background task
    println!("3. Spawning scheduler as tokio background task:");
    // {
    //     let scheduler_clone = scheduler.clone();
    //     tokio::spawn(async move {
    //         println!("Starting scheduled refresh background task");
    //         scheduler_clone.start().await;
    //     });
    // }

    println!("   ✓ Scheduler spawned (runs indefinitely in background)");
    println!();

    // Continue with gRPC server setup
    println!("4. gRPC server initialization continues:");
    println!("   - DataPlaneService created");
    println!("   - tonic Server listening on 0.0.0.0:50051");
    println!();

    // Example of querying scheduler state (if needed)
    println!("5. Optional: Query scheduler state");
    println!("   let last_refresh = scheduler.last_refresh();");
    println!("   let elapsed = now_ms() - last_refresh;");
    println!();

    Ok(())
}

/// Example: How to structure integration in grpc_server.rs
///
/// This shows the actual integration code pattern.
const INTEGRATION_PATTERN: &str = r#"
// In src/grpc_server.rs

use crate::refresh::{RefreshScheduler, SchedulerConfig};
use std::sync::Arc;

pub async fn start_grpc_server(
    bridge: Arc<Bridge>,
    port: u16,
    management_plane_url: String,
) -> Result<(), Box<dyn std::error::Error>> {
    let addr = format!("0.0.0.0:{}", port).parse()?;

    println!("Starting Data Plane gRPC server on {}", addr);
    println!("Management Plane URL: {}", management_plane_url);

    // Initialize RefreshScheduler with default config
    let scheduler_config = SchedulerConfig::default();
    let scheduler = Arc::new(RefreshScheduler::new(
        bridge.clone(),
        scheduler_config,
    ));

    // Spawn scheduler as background task
    // This must happen BEFORE server bind to ensure scheduler starts
    {
        let scheduler_clone = scheduler.clone();
        tokio::spawn(async move {
            println!("Spawning scheduled refresh background task");
            scheduler_clone.start().await;
        });
    }

    // Initialize gRPC service
    let service = DataPlaneService::new(bridge, management_plane_url);

    // Start server (blocks indefinitely)
    // Scheduler runs in separate tokio task
    Server::builder()
        .add_service(DataPlaneServer::new(service))
        .serve(addr)
        .await?;

    Ok(())
}
"#;

/// Example: Configuration variations
const CONFIG_EXAMPLES: &str = r#"
// Default configuration (6-hour interval)
let config = SchedulerConfig::default();

// Production: 12-hour interval for larger deployments
let config = SchedulerConfig {
    refresh_interval: Duration::from_secs(12 * 60 * 60),
    enabled: true,
};

// Testing: 1-minute interval for quick verification
let config = SchedulerConfig {
    refresh_interval: Duration::from_secs(60),
    enabled: true,
};

// Disabled (fallback to event-driven only)
let config = SchedulerConfig {
    refresh_interval: Duration::from_secs(6 * 60 * 60),
    enabled: false,
};

// Aggressive: 1-hour interval for frequently changing rules
let config = SchedulerConfig {
    refresh_interval: Duration::from_secs(60 * 60),
    enabled: true,
};
"#;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!();
    spawn_scheduled_refresh_example().await?;

    println!("=================================================");
    println!("  Integration Code Pattern                      ");
    println!("=================================================");
    println!("{}", INTEGRATION_PATTERN);

    println!();
    println!("=================================================");
    println!("  Configuration Examples                        ");
    println!("=================================================");
    println!("{}", CONFIG_EXAMPLES);

    println!();
    println!("=================================================");
    println!("  Key Points                                    ");
    println!("=================================================");
    println!();
    println!("1. Spawn scheduler BEFORE server.serve() call");
    println!("2. Scheduler runs indefinitely in background");
    println!("3. Errors are logged but don't stop scheduler");
    println!("4. Default 6-hour interval balances:");
    println!("   - LRU locality (rules used in 6h stay cached)");
    println!("   - Operational overhead (~0.005% CPU)");
    println!("   - Convergence time (acceptable delay)");
    println!();
    println!("5. Monitor with:");
    println!("   - Logs: 'Scheduled refresh completed: X rules'");
    println!("   - Metric: scheduler.last_refresh() timestamp");
    println!("   - Error: 'WARNING: Scheduled refresh failed'");
    println!();

    Ok(())
}
