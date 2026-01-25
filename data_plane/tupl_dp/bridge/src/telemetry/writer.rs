//! # Hitlog Writer
//!
//! Atomic, rotation-capable writer for enforcement session logs.
//! Analogous to syslog for enforcement decisions.

use super::recorder::TelemetryConfig;
use super::session::EnforcementSession;
use parking_lot::Mutex;
use rusqlite::{params, Connection};
use std::fs::{self, File, OpenOptions};
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// Hitlog writer configuration
#[derive(Debug, Clone)]
pub struct HitlogConfig {
    /// Base directory for hitlogs
    pub base_dir: String,

    /// Rotation policy
    pub rotation: RotationPolicy,

    /// Enable compression for rotated logs
    pub compress_rotated: bool,

    /// Maximum number of rotated files to keep
    pub max_rotated_files: usize,

    /// Buffer size for writes (bytes)
    pub buffer_size: usize,

    /// Enable immediate flush (disable buffering)
    pub immediate_flush: bool,
}

/// File rotation policy
#[derive(Debug, Clone)]
pub enum RotationPolicy {
    /// Rotate when file exceeds size (bytes)
    BySize(u64),

    /// Rotate every N seconds
    ByTime(u64),

    /// Rotate daily at midnight UTC
    Daily,

    /// Rotate hourly
    Hourly,

    /// No rotation
    Never,
}

impl Default for HitlogConfig {
    fn default() -> Self {
        HitlogConfig {
            base_dir: "/var/hitlogs".to_string(),
            rotation: RotationPolicy::BySize(100 * 1024 * 1024), // 100 MB
            compress_rotated: true,
            max_rotated_files: 10,
            buffer_size: 8192,
            immediate_flush: false,
        }
    }
}

impl HitlogConfig {
    pub fn from_telemetry_config(config: &TelemetryConfig) -> Self {
        HitlogConfig {
            base_dir: config.hitlog_dir.clone(),
            rotation: RotationPolicy::BySize(100 * 1024 * 1024),
            compress_rotated: true,
            max_rotated_files: 10,
            buffer_size: 8192,
            immediate_flush: false,
        }
    }
}

/// Thread-safe hitlog writer
pub struct HitlogWriter {
    config: HitlogConfig,
    current_file: Mutex<Option<HitlogFile>>,
    base_path: PathBuf,
    sqlite: Option<Mutex<Connection>>,
}

struct HitlogFile {
    writer: BufWriter<File>,
    path: PathBuf,
    created_at: SystemTime,
    bytes_written: u64,
    sessions_written: u64,
}

impl HitlogWriter {
    /// Create a new hitlog writer
    pub fn new(config: HitlogConfig) -> Result<Self, String> {
        // Create base directory if it doesn't exist
        fs::create_dir_all(&config.base_dir)
            .map_err(|e| format!("Failed to create hitlog directory: {}", e))?;

        let base_path = PathBuf::from(&config.base_dir);

        // Optional SQLite sink (controlled via env HITLOG_SQLITE_PATH)
        let sqlite = std::env::var("HITLOG_SQLITE_PATH")
            .ok()
            .map(|path| {
                let p = PathBuf::from(path);
                if let Some(parent) = p.parent() {
                    if let Err(e) = fs::create_dir_all(parent) {
                        return Err(format!("Failed to create sqlite parent dir: {}", e));
                    }
                }

                match Connection::open(&p) {
                    Ok(conn) => {
                        if let Err(e) = conn.execute(
                            "CREATE TABLE IF NOT EXISTS hitlogs (
                                session_id TEXT PRIMARY KEY,
                                tenant_id TEXT,
                                agent_id TEXT,
                                layer TEXT,
                                timestamp_ms INTEGER,
                                final_decision INTEGER,
                                duration_us INTEGER,
                                session_json TEXT NOT NULL
                            )",
                            [],
                        ) {
                            return Err(format!("Failed to create hitlogs table: {}", e));
                        }
                        Ok(Mutex::new(conn))
                    }
                    Err(e) => Err(format!("Failed to open sqlite db: {}", e)),
                }
            })
            .transpose()?;

        let writer = HitlogWriter {
            config,
            current_file: Mutex::new(None),
            base_path,
            sqlite,
        };

        // Create initial file
        writer.rotate_if_needed(true)?;

        Ok(writer)
    }

    /// Write an enforcement session to hitlog
    pub fn write_session(&self, session: &EnforcementSession) -> Result<(), String> {
        // Serialize session to JSON
        let json = serde_json::to_string(session)
            .map_err(|e| format!("Failed to serialize session: {}", e))?;

        // Write JSON line
        let line = format!("{}\n", json);
        let bytes = line.as_bytes();

        // Acquire lock and write
        let mut file_guard = self.current_file.lock();

        // Ensure file exists
        if file_guard.is_none() {
            drop(file_guard);
            self.rotate_if_needed(true)?;
            file_guard = self.current_file.lock();
        }

        if let Some(ref mut file) = *file_guard {
            file.writer
                .write_all(bytes)
                .map_err(|e| format!("Failed to write to hitlog: {}", e))?;

            if self.config.immediate_flush {
                file.writer
                    .flush()
                    .map_err(|e| format!("Failed to flush hitlog: {}", e))?;
            }

            file.bytes_written += bytes.len() as u64;
            file.sessions_written += 1;

            // Check if rotation needed
            if self.should_rotate(file) {
                drop(file_guard);
                self.rotate_if_needed(false)?;
            }
        }

        // Also persist to SQLite when configured
        if let Some(ref sqlite_mutex) = self.sqlite {
            let conn_guard = sqlite_mutex.lock();
            let tenant = session.tenant_id.clone().unwrap_or_default();
            let agent = session.agent_id.clone().unwrap_or_default();
            let layer = session.layer.clone();
            let ts = session.timestamp_ms as i64;
            let final_decision = session.final_decision as i64;
            let duration = session.duration_us as i64;

            conn_guard
                .execute(
                    "INSERT OR REPLACE INTO hitlogs (
                        session_id, tenant_id, agent_id, layer, timestamp_ms, final_decision, duration_us, session_json
                    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                    params![
                        session.session_id,
                        tenant,
                        agent,
                        layer,
                        ts,
                        final_decision,
                        duration,
                        json
                    ],
                )
                .map_err(|e| format!("Failed to upsert hitlog into sqlite: {}", e))?;
        }

        Ok(())
    }

    /// Flush pending writes
    pub fn flush(&self) -> Result<(), String> {
        let mut file_guard = self.current_file.lock();

        if let Some(ref mut file) = *file_guard {
            file.writer
                .flush()
                .map_err(|e| format!("Failed to flush hitlog: {}", e))?;
        }

        Ok(())
    }

    /// Check if rotation is needed
    fn should_rotate(&self, file: &HitlogFile) -> bool {
        match self.config.rotation {
            RotationPolicy::BySize(max_bytes) => file.bytes_written >= max_bytes,
            RotationPolicy::ByTime(seconds) => {
                let elapsed = SystemTime::now()
                    .duration_since(file.created_at)
                    .unwrap_or_default()
                    .as_secs();
                elapsed >= seconds
            }
            RotationPolicy::Daily => {
                // Check if day has changed
                let created_day = file
                    .created_at
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs()
                    / 86400;
                let current_day = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs()
                    / 86400;
                current_day > created_day
            }
            RotationPolicy::Hourly => {
                let created_hour = file
                    .created_at
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs()
                    / 3600;
                let current_hour = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs()
                    / 3600;
                current_hour > created_hour
            }
            RotationPolicy::Never => false,
        }
    }

    /// Rotate log file if needed
    fn rotate_if_needed(&self, force_create: bool) -> Result<(), String> {
        let mut file_guard = self.current_file.lock();

        // Check if rotation needed
        let should_rotate = if let Some(ref file) = *file_guard {
            self.should_rotate(file) || force_create
        } else {
            force_create
        };

        if !should_rotate {
            return Ok(());
        }

        // Close current file
        if let Some(mut old_file) = file_guard.take() {
            old_file
                .writer
                .flush()
                .map_err(|e| format!("Failed to flush before rotation: {}", e))?;

            // Move to rotated name
            let rotated_path = self.generate_rotated_path(&old_file.path)?;
            fs::rename(&old_file.path, &rotated_path)
                .map_err(|e| format!("Failed to rotate file: {}", e))?;

            // Compress if enabled
            if self.config.compress_rotated {
                self.compress_file(&rotated_path)?;
            }

            // Clean up old rotated files
            self.cleanup_old_files()?;
        }

        // Create new file
        let new_path = self.generate_current_path();
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&new_path)
            .map_err(|e| format!("Failed to create hitlog file: {}", e))?;

        let writer = BufWriter::with_capacity(self.config.buffer_size, file);

        *file_guard = Some(HitlogFile {
            writer,
            path: new_path,
            created_at: SystemTime::now(),
            bytes_written: 0,
            sessions_written: 0,
        });

        Ok(())
    }

    /// Generate path for current hitlog file
    fn generate_current_path(&self) -> PathBuf {
        self.base_path.join("enforcement.hitlog")
    }

    /// Generate path for rotated file
    fn generate_rotated_path(&self, _current_path: &Path) -> Result<PathBuf, String> {
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        let filename = format!("enforcement.hitlog.{}", timestamp);
        Ok(self.base_path.join(filename))
    }

    /// Compress a rotated file (gzip)
    fn compress_file(&self, path: &Path) -> Result<(), String> {
        use flate2::write::GzEncoder;
        use flate2::Compression;

        let input =
            fs::read(path).map_err(|e| format!("Failed to read file for compression: {}", e))?;

        let output_path = path.with_extension("hitlog.gz");
        let output_file = File::create(&output_path)
            .map_err(|e| format!("Failed to create compressed file: {}", e))?;

        let mut encoder = GzEncoder::new(output_file, Compression::default());
        encoder
            .write_all(&input)
            .map_err(|e| format!("Failed to compress: {}", e))?;
        encoder
            .finish()
            .map_err(|e| format!("Failed to finish compression: {}", e))?;

        // Remove uncompressed file
        fs::remove_file(path).ok();

        Ok(())
    }

    /// Clean up old rotated files
    fn cleanup_old_files(&self) -> Result<(), String> {
        let mut rotated_files: Vec<PathBuf> = fs::read_dir(&self.base_path)
            .map_err(|e| format!("Failed to read hitlog directory: {}", e))?
            .filter_map(|entry| entry.ok())
            .map(|entry| entry.path())
            .filter(|path| {
                path.file_name()
                    .and_then(|n| n.to_str())
                    .map(|name| name.starts_with("enforcement.hitlog."))
                    .unwrap_or(false)
            })
            .collect();

        // Sort by modification time (newest first)
        rotated_files.sort_by_key(|path| {
            fs::metadata(path)
                .and_then(|m| m.modified())
                .unwrap_or(SystemTime::UNIX_EPOCH)
        });
        rotated_files.reverse();

        // Remove excess files
        if rotated_files.len() > self.config.max_rotated_files {
            for path in &rotated_files[self.config.max_rotated_files..] {
                fs::remove_file(path).ok();
            }
        }

        Ok(())
    }

    /// Get current file stats
    pub fn stats(&self) -> Option<HitlogStats> {
        let file_guard = self.current_file.lock();
        file_guard.as_ref().map(|file| HitlogStats {
            path: file.path.clone(),
            bytes_written: file.bytes_written,
            sessions_written: file.sessions_written,
            created_at: file.created_at,
        })
    }
}

/// Statistics for current hitlog file
#[derive(Debug, Clone)]
pub struct HitlogStats {
    pub path: PathBuf,
    pub bytes_written: u64,
    pub sessions_written: u64,
    pub created_at: SystemTime,
}
