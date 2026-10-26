// Copyright (C) 2026 2351485 ALBERTA INC.
// SPDX-License-Identifier: AGPL-3.0-or-later

use chrono::Utc;
use std::collections::HashMap;
use std::sync::{Arc, RwLock};

use super::types::{
    EntityType, VersionAuthor, VersionMetadata, VersionQuery, VersionedRecord,
};
use crate::storage::Store;

/// Append-only version store.
/// Every mutation creates a new version. Old versions are never deleted.
///
/// Two modes, same API and semantics:
/// - [`VersionStore::new`] — in-memory only (tests, ephemeral tools).
/// - [`VersionStore::open_persistent`] — backed by the engine's SQLite
///   database (WAL mode, see `storage::Store`): every append is written
///   to the `version_records` table before it lands in the in-memory
///   index, and the full history is reloaded on open, so version
///   chains survive engine restarts.
pub struct VersionStore {
    records: Arc<RwLock<HashMap<String, Vec<VersionedRecord>>>>,
    backend: Option<Arc<Store>>,
}

impl VersionStore {
    pub fn new() -> Self {
        Self {
            records: Arc::new(RwLock::new(HashMap::new())),
            backend: None,
        }
    }

    /// Open a store backed by the engine's SQLite database.
    ///
    /// Loads every persisted record into the in-memory index, grouped
    /// by entity and ordered by version, restoring history, version
    /// numbering, and the parent-hash chain exactly as the previous
    /// process left them.
    ///
    /// # Errors
    /// Returns [`VersionStoreError::Persistence`] if the persisted
    /// history cannot be read back.
    pub fn open_persistent(backend: Arc<Store>) -> Result<Self, VersionStoreError> {
        let persisted = backend
            .load_version_records()
            .map_err(|e| VersionStoreError::Persistence(e.to_string()))?;

        let mut records: HashMap<String, Vec<VersionedRecord>> = HashMap::new();
        for record in persisted {
            records
                .entry(record.entity_id.clone())
                .or_default()
                .push(record);
        }
        // The load query already orders by (entity_id, version); sort
        // defensively so chain order never depends on the backend.
        for versions in records.values_mut() {
            versions.sort_by_key(|r| r.version);
        }

        Ok(Self {
            records: Arc::new(RwLock::new(records)),
            backend: Some(backend),
        })
    }

    /// Append a new version. Returns the version hash.
    #[allow(clippy::too_many_arguments)]
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

        // Persist before indexing: a failed write must not create a
        // version that exists only in this process's memory.
        if let Some(backend) = &self.backend {
            backend
                .insert_version_record(&record)
                .map_err(|e| VersionStoreError::Persistence(e.to_string()))?;
        }

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
    #[allow(dead_code)]
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

    /// Point-in-time read across all entities: for each entity, the latest
    /// version whose `created_at` is at or before `as_of`. Empty filter
    /// slices mean "no filter". Results are sorted by entity_id so callers
    /// (snapshots, diffs) get a deterministic order.
    pub fn snapshot_records(
        &self,
        as_of: chrono::DateTime<chrono::Utc>,
        entity_types: &[EntityType],
        entity_ids: &[String],
    ) -> Result<Vec<VersionedRecord>, VersionStoreError> {
        let records = self.records.read()
            .map_err(|_| VersionStoreError::LockFailed)?;

        let mut out: Vec<VersionedRecord> = Vec::new();
        for (entity_id, versions) in records.iter() {
            if !entity_ids.is_empty() && !entity_ids.contains(entity_id) {
                continue;
            }
            let latest_at = versions.iter().rev().find(|r| {
                r.created_at <= as_of
                    && (entity_types.is_empty() || entity_types.contains(&r.entity_type))
            });
            if let Some(record) = latest_at {
                out.push(record.clone());
            }
        }
        out.sort_by(|a, b| a.entity_id.cmp(&b.entity_id));
        Ok(out)
    }

    /// Count total versions across all entities.
    #[allow(dead_code)]
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
#[allow(dead_code)]
pub enum VersionStoreError {
    #[error("Failed to acquire lock on version store")]
    LockFailed,
    #[error("Entity not found: {0}")]
    NotFound(String),
    #[error("Integrity check failed for entity: {0}")]
    IntegrityFailed(String),
    #[error("Failed to persist version record: {0}")]
    Persistence(String),
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

    /// Regression test for the pre-3B defect: versioned records lived
    /// only in memory and vanished on engine restart. Append under one
    /// store instance, drop it, reopen over the same database file,
    /// and require the full history — numbering, metadata, and the
    /// parent-hash chain — to be intact.
    #[test]
    fn test_persistence_across_reopen() {
        use crate::storage::Store;

        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("pae-versions-test.db");

        let (hash1, hash2) = {
            let backend = Arc::new(Store::open(&path).unwrap());
            let store = VersionStore::open_persistent(backend).unwrap();
            let h1 = store.append(
                "journal-001", EntityType::DecisionEntry,
                b"entry-v1".to_vec(), b"nonce-v1-12b!".to_vec(),
                VersionAuthor::User, Some("Initial entry".to_string()),
                vec!["quarterly-review".to_string()],
            ).unwrap();
            let h2 = store.append(
                "journal-001", EntityType::DecisionEntry,
                b"entry-v2-updated".to_vec(), b"nonce-v2-12b!".to_vec(),
                VersionAuthor::User, Some("Added 30-day outcome".to_string()), vec![],
            ).unwrap();
            assert_eq!(store.total_versions().unwrap(), 2);
            (h1, h2)
        };

        // Reopen over the same file, as a restarted engine would.
        let backend = Arc::new(Store::open(&path).unwrap());
        let store = VersionStore::open_persistent(backend).unwrap();

        assert_eq!(store.total_versions().unwrap(), 2);
        let history = store.query(&VersionQuery {
            entity_id: "journal-001".to_string(),
            entity_type: None,
            since: None,
            until: None,
            limit: None,
            latest_only: false,
        }).unwrap();
        assert_eq!(history.len(), 2);
        assert_eq!(history[0].version_hash, hash1);
        assert_eq!(history[0].content_encrypted, b"entry-v1".to_vec());
        assert_eq!(history[0].metadata.tags, vec!["quarterly-review".to_string()]);

        let latest = store.get_latest("journal-001").unwrap().unwrap();
        assert_eq!(latest.version, 2);
        assert_eq!(latest.version_hash, hash2);
        assert_eq!(latest.parent_hash.as_deref(), Some(hash1.as_str()));
        assert_eq!(
            latest.metadata.change_summary.as_deref(),
            Some("Added 30-day outcome"),
        );
        assert!(store.verify_chain("journal-001").unwrap());

        // Version numbering continues where the previous process stopped.
        store.append(
            "journal-001", EntityType::DecisionEntry,
            b"entry-v3".to_vec(), b"nonce-v3-12b!".to_vec(),
            VersionAuthor::User, None, vec![],
        ).unwrap();
        assert_eq!(store.get_latest("journal-001").unwrap().unwrap().version, 3);
        assert!(store.verify_chain("journal-001").unwrap());

        // Snapshots (the point-in-time read path) see reloaded history.
        let snap = store.snapshot_records(
            Utc::now(), &[EntityType::DecisionEntry], &[],
        ).unwrap();
        assert_eq!(snap.len(), 1);
        assert_eq!(snap[0].version, 3);
    }

    /// The in-memory constructor stays ephemeral: nothing is written
    /// anywhere and a fresh store starts empty.
    #[test]
    fn test_in_memory_store_starts_empty() {
        let store = VersionStore::new();
        assert_eq!(store.total_versions().unwrap(), 0);
        assert!(store.get_latest("anything").unwrap().is_none());
    }
}
