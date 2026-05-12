use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Every mutable entity in PAE is versioned.
/// Versions are append-only, content-addressed, and encrypted.
/// Old versions are never deleted or overwritten.

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VersionedRecord {
    /// Content-addressed ID: SHA-256(entity_id + version + content)
    pub version_hash: String,
    /// Stable identifier for the entity
    pub entity_id: String,
    /// Entity type for querying
    pub entity_type: EntityType,
    /// Monotonically increasing version number per entity
    pub version: u64,
    /// Who or what created this version
    pub author: VersionAuthor,
    /// When this version was created (UTC)
    pub created_at: DateTime<Utc>,
    /// Encrypted content (ciphertext). Server never sees plaintext.
    pub content_encrypted: Vec<u8>,
    /// Nonce used for AES-256-GCM encryption
    pub nonce: Vec<u8>,
    /// Plaintext metadata (not sensitive -- used for querying without decryption)
    pub metadata: VersionMetadata,
    /// Hash of the previous version (chain integrity)
    pub parent_hash: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum EntityType {
    Holdings,
    Position,
    DecisionEntry,
    CalibrationRecord,
    KnowledgeChunk,
    KnowledgeAnnotation,
    Configuration,
    StressTestConfig,
    MonteCarloConfig,
    CarrySnapshot,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum VersionAuthor {
    User,
    System,
    DataFeed(String),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VersionMetadata {
    pub change_summary: Option<String>,
    pub content_size_bytes: u64,
    pub tags: Vec<String>,
}

impl VersionedRecord {
    /// Compute content-addressed hash. Makes each version tamper-evident.
    pub fn compute_hash(entity_id: &str, version: u64, content: &[u8]) -> String {
        let mut hasher = Sha256::new();
        hasher.update(entity_id.as_bytes());
        hasher.update(version.to_le_bytes());
        hasher.update(content);
        format!("{:x}", hasher.finalize())
    }

    /// Verify the integrity of this version record.
    pub fn verify_integrity(&self) -> bool {
        let expected = Self::compute_hash(
            &self.entity_id,
            self.version,
            &self.content_encrypted,
        );
        self.version_hash == expected
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VersionQuery {
    pub entity_id: String,
    pub entity_type: Option<EntityType>,
    pub since: Option<DateTime<Utc>>,
    pub until: Option<DateTime<Utc>>,
    pub limit: Option<usize>,
    pub latest_only: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SnapshotQuery {
    pub as_of: DateTime<Utc>,
    pub entity_types: Vec<EntityType>,
    pub entity_ids: Vec<String>,
}
