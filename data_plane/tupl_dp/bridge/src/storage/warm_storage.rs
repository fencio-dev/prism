//! Warm storage - memory-mapped file for persistent rule cache.
//!
//! Stores HashMap<String, RuleVector> in binary format for fast reload on restart.
//! Uses memmap2 for memory-mapped file access.
//!
//! # File Format
//! ```
//! Metadata (stored separately in a JSON header):
//!   magic: "GUAR"
//!   version: 1
//!   index_offset: u64
//!
//! Entries (variable length, binary):
//!   [entry_1: (String, RuleVector)]
//!   [entry_2: (String, RuleVector)]
//!   ...
//!
//! Index at index_offset (HashMap):
//!   rule_id → offset
//! ```

use memmap2::Mmap;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use crate::rule_vector::RuleVector;

const VERSION: u32 = 1;

/// Metadata header stored as JSON at the start of the file.
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Metadata {
    magic: String,
    version: u32,
    index_offset: u64,
}

/// Memory-mapped warm storage.
pub struct WarmStorage {
    /// Path to storage file
    path: PathBuf,

    /// Memory-mapped file (read-only after load)
    mmap: Arc<RwLock<Option<Mmap>>>,

    /// Index: rule_id → offset in file
    index: Arc<RwLock<HashMap<String, u64>>>,

    /// Metadata (where the index starts)
    metadata: Arc<RwLock<Option<Metadata>>>,
}

impl std::fmt::Debug for WarmStorage {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WarmStorage")
            .field("path", &self.path)
            .field("index_entries", &self.index.read().len())
            .finish()
    }
}

impl WarmStorage {
    /// Open or create warm storage at the given path.
    pub fn open<P: AsRef<Path>>(path: P) -> Result<Self, String> {
        let path = path.as_ref().to_path_buf();

        // Create parent directory if needed
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("Failed to create directory: {}", e))?;
        }

        let storage = Self {
            path: path.clone(),
            mmap: Arc::new(RwLock::new(None)),
            index: Arc::new(RwLock::new(HashMap::new())),
            metadata: Arc::new(RwLock::new(None)),
        };

        // Load existing or create new
        if path.exists() {
            storage.load()?;
        } else {
            storage.create()?;
        }

        Ok(storage)
    }

    /// Get rule anchors by ID.
    pub fn get(&self, rule_id: &str) -> Result<Option<RuleVector>, String> {
        let index = self.index.read();
        let offset = match index.get(rule_id) {
            Some(off) => *off,
            None => return Ok(None),
        };

        let mmap_guard = self.mmap.read();
        let mmap = mmap_guard.as_ref().ok_or("Warm storage not loaded")?;

        // Deserialize (rule_id, RuleVector) at offset
        let mut cursor = std::io::Cursor::new(&mmap[offset as usize..]);
        let _stored_rule_id: String = bincode::deserialize_from(&mut cursor)
            .map_err(|e| format!("Deserialize rule_id failed: {}", e))?;
        let anchors: RuleVector = bincode::deserialize_from(&mut cursor)
            .map_err(|e| format!("Deserialize anchors failed: {}", e))?;

        Ok(Some(anchors))
    }

    /// Load all rule anchors as HashMap.
    pub fn load_anchors(&self) -> Result<HashMap<String, RuleVector>, String> {
        let index = self.index.read().clone();
        let mut anchors = HashMap::with_capacity(index.len());

        for rule_id in index.keys() {
            if let Some(vector) = self.get(rule_id)? {
                anchors.insert(rule_id.clone(), vector);
            }
        }

        Ok(anchors)
    }

    /// Write all anchors to storage.
    pub fn write_anchors(&self, anchors: HashMap<String, RuleVector>) -> Result<(), String> {
        // Create temp file
        let tmp_path = self.path.with_extension("tmp");
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&tmp_path)
            .map_err(|e| format!("Create temp failed: {}", e))?;

        // Reserve space for metadata (we'll overwrite it later)
        // Use a large placeholder to ensure we have enough space
        let metadata_placeholder = format!(
            "{{\"magic\":\"GUAR\",\"version\":1,\"index_offset\":{}}}",
            u64::MAX
        );
        let metadata_line = format!("{:<200}\n", metadata_placeholder);
        let metadata_size = metadata_line.len();
        file.write_all(metadata_line.as_bytes())
            .map_err(|e| format!("Write metadata failed: {}", e))?;

        // Write entries and build index
        let mut index = HashMap::new();
        for (rule_id, rule_vector) in anchors {
            let offset = file
                .stream_position()
                .map_err(|e| format!("Get position failed: {}", e))?;

            // Write rule_id length + rule_id + RuleVector
            file.write_all(
                &bincode::serialize(&rule_id)
                    .map_err(|e| format!("Serialize rule_id failed: {}", e))?,
            )
            .map_err(|e| format!("Write rule_id failed: {}", e))?;

            file.write_all(
                &bincode::serialize(&rule_vector)
                    .map_err(|e| format!("Serialize anchors failed: {}", e))?,
            )
            .map_err(|e| format!("Write anchors failed: {}", e))?;

            index.insert(rule_id, offset);
        }

        // Write index
        let index_offset = file
            .stream_position()
            .map_err(|e| format!("Get index position failed: {}", e))?;

        file.write_all(
            &bincode::serialize(&index).map_err(|e| format!("Serialize index failed: {}", e))?,
        )
        .map_err(|e| format!("Write index failed: {}", e))?;

        // Update metadata with correct index_offset
        file.seek(SeekFrom::Start(0))
            .map_err(|e| format!("Seek failed: {}", e))?;

        let metadata_str = format!(
            "{{\"magic\":\"GUAR\",\"version\":{},\"index_offset\":{}}}",
            VERSION, index_offset
        );

        // Pad to original size
        let padded = format!("{:<width$}\n", metadata_str, width = metadata_size - 1);
        file.write_all(padded.as_bytes())
            .map_err(|e| format!("Write updated metadata failed: {}", e))?;

        file.sync_all().map_err(|e| format!("Sync failed: {}", e))?;
        drop(file);

        // Atomic rename
        std::fs::rename(&tmp_path, &self.path).map_err(|e| format!("Rename failed: {}", e))?;

        // Reload mmap
        self.load()?;
        Ok(())
    }

    /// Load mmap and index from file.
    fn load(&self) -> Result<(), String> {
        let file = File::open(&self.path).map_err(|e| format!("Open failed: {}", e))?;

        let mmap = unsafe { Mmap::map(&file).map_err(|e| format!("Mmap failed: {}", e))? };

        if mmap.is_empty() {
            return Err("File is empty".to_string());
        }

        // Read metadata from first line
        let newline_pos = mmap
            .iter()
            .position(|&b| b == b'\n')
            .ok_or("No metadata line found")?;

        let metadata_str = std::str::from_utf8(&mmap[..newline_pos])
            .map_err(|e| format!("Invalid UTF-8 in metadata: {}", e))?
            .trim();

        let metadata: Metadata = serde_json::from_str(metadata_str)
            .map_err(|e| format!("Parse metadata failed: {}", e))?;

        if metadata.magic != "GUAR" {
            return Err("Invalid magic number".to_string());
        }

        if metadata.version != VERSION {
            return Err(format!("Unsupported version: {}", metadata.version));
        }

        // Read index
        let index: HashMap<String, u64> =
            bincode::deserialize(&mmap[metadata.index_offset as usize..])
                .map_err(|e| format!("Deserialize index failed: {}", e))?;

        *self.mmap.write() = Some(mmap);
        *self.index.write() = index;
        *self.metadata.write() = Some(metadata);

        Ok(())
    }

    /// Create empty storage file.
    fn create(&self) -> Result<(), String> {
        self.write_anchors(HashMap::new())?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_warm_storage_create_and_load() {
        let tmp_dir = tempfile::tempdir().unwrap();
        let path = tmp_dir.path().join("test.bin");

        let _ = WarmStorage::open(&path).unwrap();
        assert!(path.exists());
    }

    #[test]
    fn test_warm_storage_write_and_read() {
        let tmp_dir = tempfile::tempdir().unwrap();
        let path = tmp_dir.path().join("test.bin");

        let storage = WarmStorage::open(&path).unwrap();

        // Create test data
        let mut anchors = HashMap::new();
        anchors.insert("rule-1".to_string(), RuleVector::default());

        storage.write_anchors(anchors.clone()).unwrap();

        // Read back
        let result = storage.get("rule-1").unwrap();
        assert!(result.is_some());
    }
}
