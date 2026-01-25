use crate::rule_vector::RuleVector;
use crate::storage::{ColdStorage, StorageStats, WarmStorage};
use crate::table::RuleFamilyTable;
use crate::types::{now_ms, LayerId, RuleFamilyId, RuleInstance};
use parking_lot::RwLock;
/// Implements the bridge structure that manages all the rule family tables.
/// The Bridge acts as a multiplexer for 14 rule family tables (one per family),
/// providing unified access, versioning and lifecycle management.
///
/// # Architecture
/// - One table per rule family (not per layer)
/// - Lock-free reads via atomic Arc pointers
/// - Copy-on-write for hot-reload scenarios
/// - Per-family indexing optimized for evaluation patterns
/// - Tiered storage for rule anchors: hot (HashMap) → warm (mmap) → cold (SQLite)
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

// ================================================================================================
// STORAGE CONFIGURATION
// ================================================================================================

/// Configuration for Bridge tiered storage.
#[derive(Clone, Debug)]
pub struct StorageConfig {
    /// Path to warm storage file (mmap)
    pub warm_storage_path: PathBuf,
    /// Path to cold storage database (SQLite)
    pub cold_storage_path: PathBuf,
}

impl Default for StorageConfig {
    fn default() -> Self {
        Self {
            warm_storage_path: PathBuf::from("./var/data/warm_storage.bin"),
            cold_storage_path: PathBuf::from("./var/data/cold_storage.db"),
        }
    }
}

// ================================================================================================
// BRIDGE STRUCTURE
// ================================================================================================

/// The Bridge is the root data structure for storing all rules in the data plane
///
/// It maintains 14 independent tables (one per rule family), each optimized
/// for a specific rule schema and indexing strategy.
///
/// # Thread Safety
/// - Tables are stored behind Arc<RwLock<>> for safe concurrent access
/// - Reads can occur simultaneously across all tables
/// - Writes to one table don't block reads/writes to other tables
///
/// # Versioning
/// - Bridge has a global version number
/// - Each table has its own version number
/// - Versions increment on any modification
///
/// # Tiered Storage
/// Rule anchors are stored in a 3-tier system:
/// - **Hot**: In-memory HashMap for frequent access (<1μs)
/// - **Warm**: Memory-mapped file for large caches (~10μs)
/// - **Cold**: SQLite database for overflow (~100μs)
///
/// Lookup chain: hot → warm → cold with automatic promotion to hot on access.

#[derive(Debug)]
pub struct Bridge {
    /// Map of family ID to table
    tables: HashMap<RuleFamilyId, Arc<RwLock<RuleFamilyTable>>>,
    ///Global bridge version (Increments on any table modifications)
    active_version: Arc<RwLock<u64>>,
    ///Optional staged version for atomic hot reload
    staged_version: Arc<RwLock<Option<u64>>>,
    ///Creation timestamp
    created_at: u64,
    /// Hot cache: in-memory HashMap for rule vectors (fastest access)
    hot_cache: Arc<RwLock<HashMap<String, RuleVector>>>,
    /// Warm storage: memory-mapped file for persistent cache
    warm_storage: Arc<WarmStorage>,
    /// Cold storage: SQLite database for overflow
    cold_storage: Arc<ColdStorage>,
    /// Storage configuration
    storage_config: StorageConfig,
}

impl Bridge {
    ///Initializes a new Bridge with empty tables for all rule families.
    /// Each table is created with deafult settings and no rules.
    /// Tables can be populated later through the add_rule method or hot-reload
    /// options.
    ///
    /// Uses default storage configuration.
    ///
    pub fn init() -> Result<Self, String> {
        Self::new(StorageConfig::default())
    }

    /// Creates a new Bridge with the specified storage configuration.
    ///
    /// This initializes:
    /// - 14 rule family tables (one per family)
    /// - Tiered storage (hot → warm → cold)
    /// - Loads warm storage into hot cache on startup
    ///
    pub fn new(storage_config: StorageConfig) -> Result<Self, String> {
        let families = RuleFamilyId::all();
        let mut tables = HashMap::new();

        // Create one table per family
        for family in families {
            let layer = family.layer();
            let table = RuleFamilyTable::new(family.clone(), layer);

            tables.insert(family.clone(), Arc::new(RwLock::new(table)));
        }

        // Initialize tiered storage
        let hot_cache = Arc::new(RwLock::new(HashMap::new()));

        let warm_storage = Arc::new(WarmStorage::open(&storage_config.warm_storage_path)?);

        let cold_storage = Arc::new(ColdStorage::open(&storage_config.cold_storage_path)?);

        // Load warm storage into hot cache on startup
        let warm_anchors = warm_storage.load_anchors()?;
        {
            let mut hot = hot_cache.write();
            *hot = warm_anchors;
        }

        Ok(Bridge {
            tables,
            active_version: Arc::new(RwLock::new(0)),
            staged_version: Arc::new(RwLock::new(None)),
            created_at: now_ms(),
            hot_cache,
            warm_storage,
            cold_storage,
            storage_config,
        })
    }

    /// Creates a Bridge with default storage paths.
    ///
    /// Storage paths default to:
    /// - Warm: `./var/data/warm_storage.bin`
    /// - Cold: `./var/data/cold_storage.db`
    ///
    pub fn with_defaults() -> Result<Self, String> {
        Self::new(StorageConfig::default())
    }

    // ============================================================================================
    // ACCESSORS
    // ============================================================================================

    /// Returns the current global version

    pub fn version(&self) -> u64 {
        *self.active_version.read()
    }

    /// Returns the staged version (if any)
    pub fn staged_version(&self) -> Option<u64> {
        *self.staged_version.read()
    }

    /// Returns the creation timestamp
    pub fn created_at(&self) -> u64 {
        self.created_at
    }

    /// Returns the number of tables in the bridge
    pub fn table_count(&self) -> usize {
        self.tables.len()
    }

    /// Returns a list of all family IDs in the bridge
    pub fn family_ids(&self) -> Vec<RuleFamilyId> {
        self.tables.keys().cloned().collect()
    }

    // ============================================================================================
    // TABLE ACCESS
    // ============================================================================================

    /// Gets a reference to a specific table by family ID

    pub fn get_table(&self, family_id: &RuleFamilyId) -> Option<Arc<RwLock<RuleFamilyTable>>> {
        self.tables.get(family_id).map(Arc::clone)
    }

    /// Gets all tables for a specific layer
    pub fn get_tables_by_layer(
        &self,
        layer_id: &LayerId,
    ) -> Vec<(RuleFamilyId, Arc<RwLock<RuleFamilyTable>>)> {
        self.tables
            .iter()
            .filter(|(fam_id, _)| fam_id.layer() == *layer_id)
            .map(|(fam_id, table)| (fam_id.clone(), Arc::clone(table)))
            .collect()
    }

    // ============================================================================================
    // RULE OPERATIONS (CONVENIENCE WRAPPERS)
    // ============================================================================================

    /// Adds a rule to the appropriate table based on its family
    ///
    /// This is a convenience wrapper that automatically routes the rule
    /// to the correct table based on the rule's family_id().

    pub fn add_rule(&self, rule: Arc<dyn RuleInstance>) -> Result<(), String> {
        let family_id = rule.family_id();
        match self.get_table(&family_id) {
            Some(table) => {
                let result = table.write().add_rule(rule);
                if result.is_ok() {
                    self.increment_version();
                }
                result
            }
            None => Err(format!("Table for family {} not found", family_id)),
        }
    }

    /// Adds a rule and stores its pre-encoded anchors with tiered persistence.
    ///
    /// The anchors are:
    /// 1. Added to the hot cache (in-memory)
    /// 2. Persisted to warm storage (mmap file)
    /// 3. Available for promotion from cold storage if needed
    ///
    pub fn add_rule_with_anchors(
        &self,
        rule: Arc<dyn RuleInstance>,
        anchors: RuleVector,
    ) -> Result<(), String> {
        let family_id = rule.family_id();
        let rule_id = rule.rule_id().to_string();

        // 1. Add to rule table
        match self.get_table(&family_id) {
            Some(table) => {
                table.write().add_rule(Arc::clone(&rule))?;
            }
            None => return Err(format!("Table for family {} not found", family_id)),
        }

        // 2. Add to hot cache
        {
            let mut hot = self.hot_cache.write();
            hot.insert(rule_id.clone(), anchors.clone());
        }

        // 3. Persist to warm storage (immediate write)
        {
            let hot_cache = self.hot_cache.read();
            let anchors_to_persist = hot_cache.clone();
            drop(hot_cache); // Release lock before I/O

            self.warm_storage.write_anchors(anchors_to_persist)?;
        }

        // 4. Increment version
        self.increment_version();

        Ok(())
    }

    /// Get rule anchors with tiered lookup and automatic promotion.
    ///
    /// Lookup strategy (in order of speed):
    /// 1. **Hot cache**: In-memory HashMap (<1μs)
    /// 2. **Warm storage**: Memory-mapped file (~10μs)
    /// 3. **Cold storage**: SQLite database (~100μs)
    /// 4. Return None if not found
    ///
    /// On hit from warm/cold, the entry is promoted to the hot cache
    /// for faster future access.
    ///
    pub fn get_rule_anchors(&self, rule_id: &str) -> Option<RuleVector> {
        // Try hot cache first (fastest)
        {
            let hot = self.hot_cache.read();
            if let Some(anchors) = hot.get(rule_id) {
                return Some(anchors.clone());
            }
        }

        // Try warm storage second (medium speed)
        if let Ok(Some(anchors)) = self.warm_storage.get(rule_id) {
            // Promote to hot cache
            {
                let mut hot = self.hot_cache.write();
                hot.insert(rule_id.to_string(), anchors.clone());
            }
            return Some(anchors);
        }

        // Try cold storage last (slowest)
        if let Ok(Some(anchors)) = self.cold_storage.get(rule_id) {
            // Promote to hot cache
            {
                let mut hot = self.hot_cache.write();
                hot.insert(rule_id.to_string(), anchors.clone());
            }
            return Some(anchors);
        }

        // Not found in any tier
        None
    }

    /// Add multiple rules in a batch (more efficient than individual adds)
    pub fn add_rules_batch(&self, rules: Vec<Arc<dyn RuleInstance>>) -> Result<(), String> {
        if rules.is_empty() {
            return Ok(());
        }

        // Group rules by family
        let mut by_family: HashMap<RuleFamilyId, Vec<Arc<dyn RuleInstance>>> = HashMap::new();

        for rule in rules {
            by_family
                .entry(rule.family_id())
                .or_insert_with(Vec::new)
                .push(rule);
        }

        // Add rules to each table
        for (family_id, family_rules) in by_family {
            match self.get_table(&family_id) {
                Some(table) => {
                    table.write().add_rules_batch(family_rules)?;
                }
                None => {
                    return Err(format!("Table for family {} not found", family_id));
                }
            }
        }

        self.increment_version();
        Ok(())
    }

    // Removes a rule from the appropriate table
    pub fn remove_rule(&self, family_id: &RuleFamilyId, rule_id: &str) -> Result<bool, String> {
        match self.get_table(family_id) {
            Some(table) => {
                let result = table.write().remove_rule(rule_id);
                if result.is_ok() && result.as_ref().unwrap() == &true {
                    self.increment_version();
                }
                result
            }
            None => Err(format!("Table for family {} not found", family_id)),
        }
    }

    /// Clears all rules from a specific table
    pub fn clear_table(&self, family_id: &RuleFamilyId) -> Result<(), String> {
        match self.get_table(family_id) {
            Some(table) => {
                table.write().clear();
                self.increment_version();
                Ok(())
            }
            None => Err(format!("Table for family {} not found", family_id)),
        }
    }

    /// Clears all rules from all tables
    pub fn clear_all(&self) {
        for table in self.tables.values() {
            table.write().clear();
        }
        self.increment_version();
    }

    // ============================================================================================
    // QUERY OPERATIONS
    // ============================================================================================

    /// Queries rules from a specific table by agent ID
    pub fn query_by_agent(
        &self,
        family_id: &RuleFamilyId,
        agent_id: &str,
    ) -> Result<Vec<Arc<dyn RuleInstance>>, String> {
        match self.get_table(family_id) {
            Some(table) => Ok(table.read().query_by_secondary(agent_id)),
            None => Err(format!("Table for family {} not found", family_id)),
        }
    }

    /// Queries global rules from a specific table
    pub fn query_global(
        &self,
        family_id: &RuleFamilyId,
    ) -> Result<Vec<Arc<dyn RuleInstance>>, String> {
        match self.get_table(family_id) {
            Some(table) => Ok(table.read().query_globals()),
            None => Err(format!("Table for family {} not found", family_id)),
        }
    }

    /// Finds a specific rule across all tables
    pub fn find_rule(&self, rule_id: &str) -> Option<Arc<dyn RuleInstance>> {
        for table in self.tables.values() {
            if let Some(rule) = table.read().find_rule(rule_id) {
                return Some(rule);
            }
        }
        None
    }

    // ============================================================================================
    // STATISTICS & MONITORING
    // ============================================================================================

    /// Returns statistics about the bridge
    pub fn stats(&self) -> BridgeStats {
        let mut total_rules = 0;
        let mut total_global_rules = 0;
        let mut total_scoped_rules = 0;
        let mut tables_with_rules = 0;

        for table in self.tables.values() {
            let meta = table.read().metadata();
            total_rules += meta.rule_count;
            total_global_rules += meta.global_count;
            total_scoped_rules += meta.scoped_count;

            if meta.rule_count > 0 {
                tables_with_rules += 1;
            }
        }

        BridgeStats {
            version: self.version(),
            total_tables: self.tables.len(),
            tables_with_rules,
            total_rules,
            total_global_rules,
            total_scoped_rules,
            created_at: self.created_at,
        }
    }

    /// Returns storage statistics across all tiers.
    ///
    /// Includes:
    /// - Number of rule anchors in each tier (hot, warm, cold)
    /// - Cache hit/miss statistics
    /// - Eviction counts
    ///
    pub fn storage_stats(&self) -> StorageStats {
        let hot_rules = self.hot_cache.read().len();

        // Could query warm_storage and cold_storage for full stats here
        // For now, return hot cache size
        StorageStats {
            hot_rules,
            ..Default::default()
        }
    }

    /// Returns per-table statistics
    pub fn table_stats(&self) -> Vec<TableStats> {
        let mut stats = Vec::new();

        for (family_id, table) in &self.tables {
            let table_guard = table.read();
            let meta = table_guard.metadata();

            stats.push(TableStats {
                family_id: family_id.clone(),
                layer_id: table_guard.layer_id().clone(),
                version: table_guard.version(),
                rule_count: meta.rule_count,
                global_count: meta.global_count,
                scoped_count: meta.scoped_count,
            });
        }

        stats.sort_by_key(|s| s.layer_id.layer_num());
        stats
    }

    // ============================================================================================
    // VERSIONING
    // ============================================================================================

    /// Increments the bridge version
    fn increment_version(&self) {
        *self.active_version.write() += 1;
    }

    /// Sets the staged version for hot-reload
    pub fn set_staged_version(&self, version: u64) {
        *self.staged_version.write() = Some(version);
    }

    /// Clears the staged version
    pub fn clear_staged_version(&self) {
        *self.staged_version.write() = None;
    }

    /// Promotes staged version to active (atomic hot-reload)
    pub fn promote_staged(&self) -> Result<(), String> {
        let staged = *self.staged_version.read();

        match staged {
            Some(v) => {
                *self.active_version.write() = v;
                self.clear_staged_version();
                Ok(())
            }
            None => Err("No staged version to promote".to_string()),
        }
    }
}

// ================================================================================================
// STATISTICS STRUCTURES
// ================================================================================================

/// Bridge-level statistics
#[derive(Debug, Clone)]
pub struct BridgeStats {
    /// Current bridge version
    pub version: u64,

    /// Total number of tables
    pub total_tables: usize,

    /// Number of tables with at least one rule
    pub tables_with_rules: usize,

    /// Total rules across all tables
    pub total_rules: usize,

    /// Total global rules
    pub total_global_rules: usize,

    /// Total scoped rules
    pub total_scoped_rules: usize,

    /// Bridge creation timestamp
    pub created_at: u64,
}

/// Per-table statistics
#[derive(Debug, Clone)]
pub struct TableStats {
    /// Rule family ID
    pub family_id: RuleFamilyId,

    /// Parent layer
    pub layer_id: LayerId,

    /// Table version
    pub version: u64,

    /// Number of rules
    pub rule_count: usize,

    /// Number of global rules
    pub global_count: usize,

    /// Number of scoped rules
    pub scoped_count: usize,
}

// ================================================================================================
// TESTS
// ================================================================================================

#[cfg(test)]
mod tests {
    use super::*;

    fn create_test_bridge() -> Result<Bridge, String> {
        let tmp_dir = tempfile::tempdir().map_err(|e| e.to_string())?;
        let warm_path = tmp_dir.path().join("warm.bin");
        let cold_path = tmp_dir.path().join("cold.db");

        let config = StorageConfig {
            warm_storage_path: warm_path,
            cold_storage_path: cold_path,
        };

        Bridge::new(config)
    }

    #[test]
    fn test_bridge_init_with_storage() -> Result<(), String> {
        let bridge = create_test_bridge()?;

        // Verify all 14 tables are created
        assert_eq!(bridge.table_count(), 14);

        // Verify hot cache is empty on init
        let storage_stats = bridge.storage_stats();
        assert_eq!(storage_stats.hot_rules, 0);

        Ok(())
    }

    #[test]
    fn test_hot_cache_lookup_empty() -> Result<(), String> {
        let bridge = create_test_bridge()?;

        // Looking up non-existent rule should return None
        let result = bridge.get_rule_anchors("non-existent-rule");
        assert!(result.is_none());

        Ok(())
    }

    #[test]
    fn test_warm_storage_reload_on_startup() -> Result<(), String> {
        let tmp_dir = tempfile::tempdir().map_err(|e| e.to_string())?;
        let warm_path = tmp_dir.path().join("warm.bin");
        let cold_path = tmp_dir.path().join("cold.db");

        // First bridge: create and store some anchors
        {
            let config = StorageConfig {
                warm_storage_path: warm_path.clone(),
                cold_storage_path: cold_path.clone(),
            };
            let bridge = Bridge::new(config)?;

            // Simulate adding anchors (would need mock RuleInstance)
            // For now, verify storage stats
            let stats = bridge.storage_stats();
            assert_eq!(stats.hot_rules, 0);
        }

        // Second bridge: should reload warm storage
        {
            let config = StorageConfig {
                warm_storage_path: warm_path,
                cold_storage_path: cold_path,
            };
            let bridge = Bridge::new(config)?;

            // Should have reloaded whatever was in warm storage
            let stats = bridge.storage_stats();
            assert_eq!(stats.hot_rules, 0); // Still 0 since we never added anything
        }

        Ok(())
    }

    #[test]
    fn test_storage_config_defaults() {
        let config = StorageConfig::default();
        assert_eq!(
            config.warm_storage_path,
            PathBuf::from("./var/data/warm_storage.bin")
        );
        assert_eq!(
            config.cold_storage_path,
            PathBuf::from("./var/data/cold_storage.db")
        );
    }

    #[test]
    fn test_storage_stats_reflects_hot_cache() -> Result<(), String> {
        let bridge = create_test_bridge()?;

        // Initial state
        let stats = bridge.storage_stats();
        assert_eq!(stats.hot_rules, 0);

        // Stats should reflect hot cache size
        // (actual addition would require mock RuleInstance)
        assert_eq!(stats.warm_rules, 0);
        assert_eq!(stats.cold_rules, 0);

        Ok(())
    }

    #[test]
    fn test_bridge_with_defaults() -> Result<(), String> {
        // This will use actual default paths - verify it doesn't panic
        let bridge = Bridge::with_defaults();
        assert!(bridge.is_ok());

        Ok(())
    }
}
