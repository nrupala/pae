use chrono::Utc;
use std::collections::HashMap;
use std::sync::{Arc, RwLock};

use super::types::{
    EntityType, VersionAuthor, VersionMetadata, VersionQuery, VersionedRecord,
};

/// Append-only version store.
/// Every mutation creates a new version. Old versions are never deleted.
///
/// Current: in-memory with RwLock.
/// Production: SQLite with WAL mode for crash recovery.
pub struct VersionStore {
    records: Arc<RwLock<HashMap<String, Vec<VersionedRecord>>>>,
}

impl VersionStore {
    pub fn new() -> Self {
        Self {
            records: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Append a new version. Returns the version hash.
    pub fn append(
        &self,
        entity_id: &str,
        entity_type: EntityType,
        content_encrypted: Vec<u8>,
        nonce: Vec<u8>,
        author: VersionAuthor,
        change_summary: Option<String>,
        tags: Vec<String>,
    ) -> Result<String, VersionStoreError> {
        let mut records = self.records.write()
            .map_err(|_| VersionStoreError::LockFailed)?;

        let entity_versions = records.entry(entity_id.to_string()).or_default();
        let version = entity_versions.len() as u64 + 1;
        let parent_hash = entity_versions.last().map(|r| r.version_hash.clone());

        let version_hash = VersionedRecord::compute_hash(
            entity_id, version, &content_encrypted,
        );

        let record = VersionedRecord {
            version_hash: version_hash.clone(),
            entity_id: entity_id.to_string(),
            entity_type,
            version,
            author,
            created_at: Utc::now(),
            content_encrypted,
            nonce,
            metadata: VersionMetadata {
                change_summary,
                content_size_bytes: 0,
                tags,
            },
            parent_hash,
        };

        entity_versions.push(record);
        Ok(version_hash)
    }

    /// Get the latest version of an entity.
    pub fn get_latest(&self, entity_id: &str) -> Result<Option<VersionedRecord>, VersionStoreError> {
        let records = self.records.read()
            .map_err(|_| VersionStoreError::LockFailed)?;
        Ok(records.get(entity_id).and_then(|v| v.last().cloned()))
    }

    /// Get a specific version by hash.
    pub fn get_by_hash(&self, version_hash: &str) -> Result<Option<VersionedRecord>, VersionStoreError> {
        let records = self.records.read()
            .map_err(|_| VersionStoreError::LockFailed)?;
        for versions in records.values() {
            for record in versions {
                if record.version_hash == version_hash {
                    return Ok(Some(record.clone()));
                }
            }
        }
        Ok(None)
    }

    /// Query version history for an entity.
    pub fn query(&self, query: &VersionQuery) -> Result<Vec<VersionedRecord>, VersionStoreError> {
        let records = self.records.read()
            .map_err(|_| VersionStoreError::LockFailed)?;

        let Some(versions) = records.get(&query.entity_id) else {
            return Ok(vec![]);
        };

        if query.latest_only {
            return Ok(versions.last().cloned().into_iter().collect());
        }

        let mut results: Vec<VersionedRecord> = versions.iter()
            .filter(|r| {
                if let Some(since) = query.since {
                    if r.created_at < since { return false; }
                }
                if let Some(until) = query.until {
                    if r.created_at > until { return false; }
                }
                true
            })
            .cloned()
            .collect();

        if let Some(limit) = query.limit {
            results.truncate(limit);
        }
        Ok(results)
    }

    /// Count total versions across all entities.
    pub fn total_versions(&self) -> Result<usize, VersionStoreError> {
        let records = self.records.read()
            .map_err(|_| VersionStoreError::LockFailed)?;
        Ok(records.values().map(|v| v.len()).sum())
    }

    /// Verify integrity of the entire version chain for an entity.
    pub fn verify_chain(&self, entity_id: &str) -> Result<bool, VersionStoreError> {
        let records = self.records.read()
            .map_err(|_| VersionStoreError::LockFailed)?;

        let Some(versions) = records.get(entity_id) else {
            return Ok(true);
        };

        for (i, record) in versions.iter().enumerate() {
            if !record.verify_integrity() { return Ok(false); }
            if i == 0 {
                if record.parent_hash.is_some() { return Ok(false); }
            } else {
                let expected_parent = &versions[i - 1].version_hash;
                match &record.parent_hash {
                    Some(parent) if parent == expected_parent => {},
                    _ => return Ok(false),
                }
            }
        }
        Ok(true)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum VersionStoreError {
    #[error("Failed to acquire lock on version store")]
    LockFailed,
    #[error("Entity not found: {0}")]
    NotFound(String),
    #[error("Integrity check failed for entity: {0}")]
    IntegrityFailed(String),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_append_and_retrieve() {
        let store = VersionStore::new();
        let hash = store.append(
            "holding-001", EntityType::Position,
            b"encrypted-content-v1".to_vec(), b"nonce-12bytes".to_vec(),
            VersionAuthor::User, Some("Initial import".to_string()), vec![],
        ).unwrap();

        assert!(!hash.is_empty());
        let latest = store.get_latest("holding-001").unwrap().unwrap();
        assert_eq!(latest.version, 1);
        assert!(latest.parent_hash.is_none());
    }

    #[test]
    fn test_version_chain() {
        let store = VersionStore::new();
        store.append(
            "journal-001", EntityType::DecisionEntry,
            b"entry-v1".to_vec(), b"nonce-v1-12b!".to_vec(),
            VersionAuthor::User, Some("Initial entry".to_string()),
            vec!["quarterly-review".to_string()],
        ).unwrap();

        store.append(
            "journal-001", EntityType::DecisionEntry,
            b"entry-v2-updated".to_vec(), b"nonce-v2-12b!".to_vec(),
            VersionAuthor::User, Some("Added 30-day outcome".to_string()), vec![],
        ).unwrap();

        let latest = store.get_latest("journal-001").unwrap().unwrap();
        assert_eq!(latest.version, 2);
        assert!(latest.parent_hash.is_some());
        assert!(store.verify_chain("journal-001").unwrap());
    }

    #[test]
    fn test_integrity_verification() {
        let store = VersionStore::new();
        store.append(
            "config-001", EntityType::Configuration,
            b"config-data".to_vec(), b"nonce-12byte".to_vec(),
            VersionAuthor::System, None, vec![],
        ).unwrap();

        let record = store.get_latest("config-001").unwrap().unwrap();
        assert!(record.verify_integrity());
    }
}
