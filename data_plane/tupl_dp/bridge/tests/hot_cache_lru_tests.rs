//! Comprehensive unit tests for HotCache LRU eviction policy.
//!
//! Tests verify:
//! - Capacity enforcement and eviction triggers
//! - LRU algorithm correctness (oldest entries evicted first)
//! - Statistics tracking (eviction counts)
//! - Mark/access timestamp updates
//! - Concurrent access safety

use bridge::rule_vector::RuleVector;
use bridge::storage::HotCache;
use std::sync::Arc;
use std::thread;
use std::time::Duration;

/// Helper function to create a test RuleVector with distinguishable data.
fn create_test_vector(id: usize) -> RuleVector {
    let mut vector = RuleVector::default();
    // Set a unique value to distinguish vectors
    vector.action_count = id;
    vector
}

// ============================================================================
// BASIC FUNCTIONALITY TESTS
// ============================================================================

#[test]
fn test_insert_and_get() {
    let cache = HotCache::with_capacity(100);
    let rule_id = "test-rule-1".to_string();
    let vector = create_test_vector(1);

    assert!(cache.insert(rule_id.clone(), vector.clone()).is_ok());
    assert!(cache.contains(&rule_id));

    let retrieved = cache.get(&rule_id);
    assert!(retrieved.is_some());
    assert_eq!(retrieved.unwrap().action_count, 1);
}

#[test]
fn test_update_existing_rule() {
    let cache = HotCache::with_capacity(100);
    let rule_id = "rule-1".to_string();
    let vector1 = create_test_vector(1);
    let vector2 = create_test_vector(2);

    // Insert first time
    assert!(cache.insert(rule_id.clone(), vector1).is_ok());
    let stats_after_insert = cache.stats();
    assert_eq!(stats_after_insert.entries, 1);

    // Update same rule - should not increase count
    assert!(cache.insert(rule_id.clone(), vector2).is_ok());
    let stats_after_update = cache.stats();
    assert_eq!(stats_after_update.entries, 1);

    // No eviction should have occurred
    assert_eq!(stats_after_update.total_evictions, 0);

    // Verify updated value is stored
    let retrieved = cache.get(&rule_id);
    assert_eq!(retrieved.unwrap().action_count, 2);
}

#[test]
fn test_remove_and_clear() {
    let cache = HotCache::with_capacity(100);

    // Insert some rules
    for i in 0..5 {
        let rule_id = format!("rule-{}", i);
        let vector = create_test_vector(i);
        assert!(cache.insert(rule_id, vector).is_ok());
    }

    assert_eq!(cache.stats().entries, 5);

    // Remove one
    cache.remove("rule-2");
    assert_eq!(cache.stats().entries, 4);
    assert!(!cache.contains("rule-2"));

    // Clear all
    cache.clear();
    assert_eq!(cache.stats().entries, 0);
    assert!(!cache.contains("rule-0"));
}

// ============================================================================
// CAPACITY AND EVICTION TESTS
// ============================================================================

#[test]
fn test_capacity_enforcement() {
    let cache = HotCache::with_capacity(10);

    // Insert 10 rules (at capacity)
    for i in 0..10 {
        let rule_id = format!("rule-{}", i);
        let vector = create_test_vector(i);
        assert!(cache.insert(rule_id, vector).is_ok());
    }

    let stats = cache.stats();
    assert_eq!(stats.entries, 10, "Cache should be exactly at capacity");
    assert_eq!(
        stats.total_evictions, 0,
        "No eviction should have occurred yet"
    );

    // Insert 11th rule - should trigger eviction
    let vector = create_test_vector(10);
    assert!(cache.insert("rule-10".to_string(), vector).is_ok());

    let stats = cache.stats();
    assert_eq!(
        stats.entries, 10,
        "Cache should remain at capacity after eviction"
    );
    assert!(
        stats.total_evictions > 0,
        "Eviction should have been triggered"
    );
    assert!(
        stats.total_evicted > 0,
        "Should report evicted entries count"
    );
}

#[test]
fn test_evicts_least_recently_used() {
    let cache = HotCache::with_capacity(10);

    // Insert 10 rules
    for i in 0..10 {
        let rule_id = format!("rule-{}", i);
        let vector = create_test_vector(i);
        assert!(cache.insert(rule_id, vector).is_ok());
        // Small delay to ensure different timestamps
        thread::sleep(Duration::from_millis(5));
    }

    // Access rule-5 to make it recently used
    let _ = cache.get_and_mark("rule-5");
    thread::sleep(Duration::from_millis(5));

    // Insert new rule - should evict oldest (rule-0, not rule-5)
    let vector = create_test_vector(10);
    assert!(cache.insert("rule-10".to_string(), vector).is_ok());

    // rule-5 should still be there (it was accessed after insertion)
    assert!(
        cache.contains("rule-5"),
        "Recently accessed rule should not be evicted"
    );

    // rule-0 should be gone (it was oldest)
    assert!(
        !cache.contains("rule-0"),
        "Oldest rule should be evicted first"
    );
}

#[test]
fn test_multiple_evictions() {
    let cache = HotCache::with_capacity(5);

    // Insert 5 rules
    for i in 0..5 {
        let rule_id = format!("rule-{}", i);
        let vector = create_test_vector(i);
        assert!(cache.insert(rule_id, vector).is_ok());
        thread::sleep(Duration::from_millis(2));
    }

    let stats_before = cache.stats();
    assert_eq!(stats_before.total_evictions, 0);

    // Trigger multiple evictions by inserting several rules
    for i in 5..15 {
        let rule_id = format!("rule-{}", i);
        let vector = create_test_vector(i);
        assert!(cache.insert(rule_id, vector).is_ok());
        thread::sleep(Duration::from_millis(2));
    }

    let stats_after = cache.stats();
    assert!(
        stats_after.total_evictions > 1,
        "Multiple evictions should occur"
    );
    assert!(
        stats_after.total_evicted >= 5,
        "Should evict at least 5 entries total"
    );
    assert_eq!(stats_after.entries, 5, "Cache should remain at capacity");
}

// ============================================================================
// STATISTICS AND TRACKING TESTS
// ============================================================================

#[test]
fn test_eviction_statistics() {
    let cache = HotCache::with_capacity(5);

    // Insert 5 rules
    for i in 0..5 {
        let rule_id = format!("rule-{}", i);
        let vector = create_test_vector(i);
        assert!(cache.insert(rule_id, vector).is_ok());
    }

    let stats_before = cache.stats();
    assert_eq!(stats_before.total_evictions, 0);
    assert_eq!(stats_before.total_evicted, 0);

    // Trigger eviction
    let vector = create_test_vector(5);
    assert!(cache.insert("rule-5".to_string(), vector).is_ok());

    let stats_after = cache.stats();
    assert_eq!(
        stats_after.total_evictions, 1,
        "Should record eviction event"
    );
    assert!(
        stats_after.total_evicted > 0,
        "Should record number of evicted entries"
    );

    // Eviction batch is 10% of 5 capacity = at least 1 entry
    assert!(
        stats_after.total_evicted >= 1,
        "Should evict at least 1 entry (10% of capacity)"
    );
}

#[test]
fn test_cache_stats_accuracy() {
    let cache = HotCache::with_capacity(20);

    // Insert and verify entry count
    for i in 0..15 {
        let rule_id = format!("rule-{}", i);
        let vector = create_test_vector(i);
        assert!(cache.insert(rule_id, vector).is_ok());
    }

    let stats = cache.stats();
    assert_eq!(
        stats.entries, 15,
        "Entry count should match number of insertions"
    );
    assert_eq!(stats.capacity, 20);
    assert_eq!(stats.total_evictions, 0, "No evictions yet");

    // Remove some entries
    for i in 0..5 {
        cache.remove(&format!("rule-{}", i));
    }

    let stats = cache.stats();
    assert_eq!(
        stats.entries, 10,
        "Entry count should decrease after removal"
    );
}

// ============================================================================
// TIMESTAMP AND ACCESS TESTS
// ============================================================================

#[test]
fn test_get_vs_get_and_mark() {
    let cache = HotCache::with_capacity(10);
    let rule_id = "rule-1".to_string();
    let vector = create_test_vector(1);

    assert!(cache.insert(rule_id.clone(), vector).is_ok());

    // Both get and get_and_mark should return the vector
    let result1 = cache.get(&rule_id);
    assert!(result1.is_some());

    let result2 = cache.get_and_mark(&rule_id);
    assert!(result2.is_some());

    // Both should return equivalent vectors
    assert_eq!(result1.unwrap().action_count, result2.unwrap().action_count);
}

#[test]
fn test_mark_evaluated_prevents_eviction() {
    let cache = HotCache::with_capacity(3);

    // Insert 3 rules
    for i in 0..3 {
        let rule_id = format!("rule-{}", i);
        let vector = create_test_vector(i);
        assert!(cache.insert(rule_id, vector).is_ok());
        thread::sleep(Duration::from_millis(5));
    }

    // Mark rule-0 as recently evaluated
    let _ = cache.get_and_mark("rule-0");
    thread::sleep(Duration::from_millis(5));

    // Insert new rule - should evict rule-1 or rule-2 (older than rule-0)
    let vector = create_test_vector(3);
    assert!(cache.insert("rule-3".to_string(), vector).is_ok());

    // rule-0 should survive (it was marked)
    assert!(
        cache.contains("rule-0"),
        "Recently marked rule should not be evicted"
    );
}

// ============================================================================
// CONCURRENT ACCESS TESTS
// ============================================================================

#[test]
fn test_concurrent_inserts() {
    let cache = Arc::new(HotCache::with_capacity(100));
    let mut handles = vec![];

    // Spawn 4 threads
    for thread_id in 0..4 {
        let cache_clone = Arc::clone(&cache);
        let handle = thread::spawn(move || {
            for i in 0..10 {
                let rule_id = format!("rule-{}-{}", thread_id, i);
                let vector = create_test_vector(i);
                let _ = cache_clone.insert(rule_id, vector);
            }
        });
        handles.push(handle);
    }

    // Wait for all threads
    for handle in handles {
        handle.join().unwrap();
    }

    // Cache should have 40 entries (4 threads × 10 entries)
    let stats = cache.stats();
    assert_eq!(
        stats.entries, 40,
        "Cache should contain all inserted entries"
    );
}

#[test]
fn test_concurrent_read_write() {
    let cache = Arc::new(HotCache::with_capacity(50));

    // Insert initial entries
    for i in 0..10 {
        let rule_id = format!("rule-{}", i);
        let vector = create_test_vector(i);
        let _ = cache.insert(rule_id, vector);
    }

    let mut handles = vec![];

    // Reader threads
    for _ in 0..2 {
        let cache_clone = Arc::clone(&cache);
        let handle = thread::spawn(move || {
            for i in 0..10 {
                let rule_id = format!("rule-{}", i);
                let _ = cache_clone.get(&rule_id);
            }
        });
        handles.push(handle);
    }

    // Writer threads
    for thread_id in 0..2 {
        let cache_clone = Arc::clone(&cache);
        let handle = thread::spawn(move || {
            for i in 10..20 {
                let rule_id = format!("rule-{}-{}", thread_id, i);
                let vector = create_test_vector(i);
                let _ = cache_clone.insert(rule_id, vector);
            }
        });
        handles.push(handle);
    }

    // Wait for all threads
    for handle in handles {
        handle.join().unwrap();
    }

    // Cache should have entries and no panics occurred
    let stats = cache.stats();
    assert!(stats.entries > 0, "Cache should contain entries");
}

#[test]
fn test_concurrent_eviction_stress() {
    let cache = Arc::new(HotCache::with_capacity(20));
    let mut handles = vec![];

    // Multiple threads inserting more than capacity
    for thread_id in 0..3 {
        let cache_clone = Arc::clone(&cache);
        let handle = thread::spawn(move || {
            for i in 0..20 {
                let rule_id = format!("rule-{}-{}", thread_id, i);
                let vector = create_test_vector(i);
                let _ = cache_clone.insert(rule_id, vector);
            }
        });
        handles.push(handle);
    }

    // Wait for all threads
    for handle in handles {
        handle.join().unwrap();
    }

    // Cache should be at capacity with no panics
    let stats = cache.stats();
    assert_eq!(stats.entries, 20, "Cache should be at capacity");
    assert!(stats.total_evictions > 0, "Evictions should have occurred");
}

// ============================================================================
// CAPACITY BOUNDARY TESTS
// ============================================================================

#[test]
fn test_small_capacity() {
    let cache = HotCache::with_capacity(1);

    // Insert and verify capacity is enforced
    assert!(cache
        .insert("rule-1".to_string(), create_test_vector(1))
        .is_ok());
    assert_eq!(cache.stats().entries, 1);

    // Insert second rule - should evict first
    assert!(cache
        .insert("rule-2".to_string(), create_test_vector(2))
        .is_ok());
    assert_eq!(cache.stats().entries, 1);
    assert!(!cache.contains("rule-1"));
    assert!(cache.contains("rule-2"));
}

#[test]
fn test_large_capacity() {
    let cache = HotCache::with_capacity(1000);

    // Insert 500 rules
    for i in 0..500 {
        let rule_id = format!("rule-{}", i);
        let vector = create_test_vector(i);
        assert!(cache.insert(rule_id, vector).is_ok());
    }

    let stats = cache.stats();
    assert_eq!(stats.entries, 500);
    assert_eq!(stats.total_evictions, 0, "No evictions under capacity");

    // Insert to trigger eviction
    for i in 500..1100 {
        let rule_id = format!("rule-{}", i);
        let vector = create_test_vector(i);
        assert!(cache.insert(rule_id, vector).is_ok());
    }

    let stats = cache.stats();
    assert_eq!(stats.entries, 1000, "Cache should be at capacity");
    assert!(stats.total_evictions > 0, "Evictions should have occurred");
}

// ============================================================================
// EDGE CASES
// ============================================================================

#[test]
fn test_get_nonexistent_rule() {
    let cache = HotCache::with_capacity(10);

    // Get should return None for non-existent rule
    assert!(cache.get("nonexistent").is_none());
    assert!(cache.get_and_mark("nonexistent").is_none());
}

#[test]
fn test_remove_nonexistent_rule() {
    let cache = HotCache::with_capacity(10);

    // Remove should handle gracefully
    cache.remove("nonexistent");
    assert_eq!(cache.stats().entries, 0);
}

#[test]
fn test_clear_empty_cache() {
    let cache = HotCache::with_capacity(10);

    // Clear should handle empty cache gracefully
    cache.clear();
    assert_eq!(cache.stats().entries, 0);
}

#[test]
fn test_repeated_updates_same_rule() {
    let cache = HotCache::with_capacity(5);
    let rule_id = "rule-1".to_string();

    // Insert and update the same rule multiple times
    for iteration in 1..=5 {
        let vector = create_test_vector(iteration);
        assert!(cache.insert(rule_id.clone(), vector).is_ok());
    }

    // Should still have only 1 entry
    assert_eq!(cache.stats().entries, 1);
    // Final value should be from iteration 5
    assert_eq!(cache.get(&rule_id).unwrap().action_count, 5);
}

#[test]
fn test_default_capacity() {
    let cache = HotCache::new();
    let stats = cache.stats();
    assert_eq!(stats.capacity, 10_000, "Default capacity should be 10K");
}
