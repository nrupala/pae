use chrono::{DateTime, Utc};

use super::store::VersionStore;
use super::types::{EntityType, SnapshotQuery, VersionedRecord};

/// Point-in-time snapshot engine.
///
/// Reconstructs the state of the portfolio as it existed at a specific moment.
/// Essential for:
/// - Decision archaeology: "What did my portfolio look like when I made decision X?"
/// - Performance attribution: "What was my allocation on March 15?"
/// - Regret minimization: "If I had not made change Y, what would my portfolio be?"
/// - Audit trail: "Prove that these were my holdings on date Z."
#[allow(dead_code)]
pub struct SnapshotEngine<'a> {
    store: &'a VersionStore,
}

#[allow(dead_code)]
impl<'a> SnapshotEngine<'a> {
    pub fn new(store: &'a VersionStore) -> Self {
        Self { store }
    }

    /// Get the state of all entities of given types as of a specific timestamp.
    /// Returns the latest version of each entity that existed at or before `as_of`.
    ///
    /// Production SQLite version uses:
    ///   SELECT * FROM versions
    ///   WHERE entity_type IN (?) AND created_at <= ?
    ///   GROUP BY entity_id
    ///   HAVING version = MAX(version)
    pub fn snapshot_at(
        &self,
        _query: &SnapshotQuery,
    ) -> Result<Vec<VersionedRecord>, super::store::VersionStoreError> {
        // TODO: Implement with direct SQL in production
        Ok(vec![])
    }

    /// Compare two snapshots at different points in time.
    /// Returns entities added, removed, or modified between timestamps.
    pub fn diff(
        &self,
        from: DateTime<Utc>,
        to: DateTime<Utc>,
        _entity_types: &[EntityType],
    ) -> Result<SnapshotDiff, super::store::VersionStoreError> {
        Ok(SnapshotDiff {
            from,
            to,
            added: vec![],
            removed: vec![],
            modified: vec![],
        })
    }
}

#[derive(Debug)]
#[allow(dead_code)]
pub struct SnapshotDiff {
    pub from: DateTime<Utc>,
    pub to: DateTime<Utc>,
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub modified: Vec<ModifiedEntity>,
}

#[derive(Debug)]
#[allow(dead_code)]
pub struct ModifiedEntity {
    pub entity_id: String,
    pub from_version: u64,
    pub to_version: u64,
    pub from_hash: String,
    pub to_hash: String,
}
