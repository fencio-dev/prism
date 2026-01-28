//! Inspection tool to verify anchor counts are loaded correctly from warm storage

use bridge::bridge::{Bridge, StorageConfig};
use std::path::PathBuf;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("=================================================");
    println!("  Anchor Count Inspection Tool");
    println!("=================================================\n");

    // Initialize bridge with default paths
    let storage_config = StorageConfig {
        warm_storage_path: PathBuf::from("./var/data/warm_storage.bin"),
        cold_storage_path: PathBuf::from("./var/data/cold_storage.db"),
    };

    println!("Loading bridge from storage...");
    let bridge = Bridge::new(storage_config)?;
    
    println!("✓ Bridge loaded successfully");
    println!("  - Total rules: {}", bridge.rule_count());
    println!("  - Bridge version: {}\n", bridge.version());

    // Inspect each rule's anchors
    println!("=== Rule Anchor Inspection ===\n");
    
    for rule in bridge.all_rules() {
        let rule_id = rule.rule_id();
        println!("Rule ID: {}", rule_id);
        
        if let Some(anchors) = bridge.get_rule_anchors(rule_id) {
            println!("  ✓ Anchors loaded");
            println!("    - action_count: {}", anchors.action_count);
            println!("    - resource_count: {}", anchors.resource_count);
            println!("    - data_count: {}", anchors.data_count);
            println!("    - risk_count: {}", anchors.risk_count);
            
            // Check if counts are zero (bug indicator)
            let total_count = anchors.action_count + 
                            anchors.resource_count + 
                            anchors.data_count + 
                            anchors.risk_count;
            
            if total_count == 0 {
                println!("    ⚠️  WARNING: All anchor counts are ZERO!");
                println!("    This indicates deserialization issue!");
            } else {
                println!("    ✓ Total anchors: {}", total_count);
            }
        } else {
            println!("  ✗ No anchors found for this rule");
        }
        println!();
    }

    println!("=================================================");
    println!("  Inspection Complete");
    println!("=================================================");

    Ok(())
}
