// Copyright (C) 2026 2351485 ALBERTA INC.
// SPDX-License-Identifier: AGPL-3.0-or-later

use chrono::{DateTime, Utc};
use std::collections::HashMap;

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
///
/// Implementation (Phase 2, 2026-10-26): the version store is append-only, so
/// a snapshot at `as_of` is, per entity, the latest version with
/// `created_at <= as_of`. Since Phase 3B the store's in-memory index is
/// backed by SQLite (the `version_records` table, reloaded on open), so
/// snapshots and diffs cover history from before the current process
/// started, not just this run's appends.
pub struct SnapshotEngine<'a> {
    store: &'a VersionStore,
}

impl<'a> SnapshotEngine<'a> {
    pub fn new(store: &'a VersionStore) -> Self {
        Self { store }
    }

    /// Get the state of all entities matching the query as of its timestamp:
    /// the latest version of each entity that existed at or before `as_of`.
    pub fn snapshot_at(
        &self,
        query: &SnapshotQuery,
    ) -> Result<Vec<VersionedRecord>, super::store::VersionStoreError> {
        self.store
            .snapshot_records(query.as_of, &query.entity_types, &query.entity_ids)
    }

    /// Compare two snapshots at different points in time.
    /// Returns entities added, removed, or modified between the timestamps.
    /// With an append-only store and `from < to`, `removed` is empty by
    /// construction; it is non-empty when the window runs backwards or if a
    /// future backend supports tombstones.
    pub fn diff(
        &self,
        from: DateTime<Utc>,
        to: DateTime<Utc>,
        entity_types: &[EntityType],
    ) -> Result<SnapshotDiff, super::store::VersionStoreError> {
        let from_records = self.store.snapshot_records(from, entity_types, &[])?;
        let to_records = self.store.snapshot_records(to, entity_types, &[])?;

        let from_map: HashMap<&str, &VersionedRecord> = from_records
            .iter()
            .map(|r| (r.entity_id.as_str(), r))
            .collect();
        let to_map: HashMap<&str, &VersionedRecord> = to_records
            .iter()
            .map(|r| (r.entity_id.as_str(), r))
            .collect();

        let mut added: Vec<String> = to_map
            .keys()
            .filter(|id| !from_map.contains_key(*id))
            .map(|id| id.to_string())
            .collect();
        let mut removed: Vec<String> = from_map
            .keys()
            .filter(|id| !to_map.contains_key(*id))
            .map(|id| id.to_string())
            .collect();
        let mut modified: Vec<ModifiedEntity> = Vec::new();
        for (id, to_rec) in &to_map {
            if let Some(from_rec) = from_map.get(id) {
                if from_rec.version_hash != to_rec.version_hash {
                    modified.push(ModifiedEntity {
                        entity_id: id.to_string(),
                        from_version: from_rec.version,
                        to_version: to_rec.version,
                        from_hash: from_rec.version_hash.clone(),
                        to_hash: to_rec.version_hash.clone(),
                    });
                }
            }
        }
        added.sort();
        removed.sort();
        modified.sort_by(|a, b| a.entity_id.cmp(&b.entity_id));

        Ok(SnapshotDiff {
            from,
            to,
            added,
            removed,
            modified,
        })
    }
}

#[derive(Debug)]
#[allow(dead_code)] // from/to are carried for API/reporting callers
pub struct SnapshotDiff {
    pub from: DateTime<Utc>,
    pub to: DateTime<Utc>,
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub modified: Vec<ModifiedEntity>,
}

#[derive(Debug)]
pub struct ModifiedEntity {
    pub entity_id: String,
    pub from_version: u64,
    pub to_version: u64,
    pub from_hash: String,
    pub to_hash: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::versioning::types::VersionAuthor;
    use chrono::Duration;

    fn append(store: &VersionStore, id: &str, ty: EntityType, content: &[u8]) {
        store
            .append(
                id,
                ty,
                content.to_vec(),
                b"nonce-12byte".to_vec(),
                VersionAuthor::User,
                None,
                vec![],
            )
            .unwrap();
    }

    fn query(as_of: DateTime<Utc>) -> SnapshotQuery {
        SnapshotQuery {
            as_of,
            entity_types: vec![],
            entity_ids: vec![],
        }
    }

    #[test]
    fn snapshot_of_empty_store_is_empty() {
        let store = VersionStore::new();
        let engine = SnapshotEngine::new(&store);
        let snap = engine.snapshot_at(&query(Utc::now())).unwrap();
        assert!(snap.is_empty());
    }

    #[test]
    fn snapshot_before_any_version_is_empty() {
        let store = VersionStore::new();
        append(&store, "h-1", EntityType::Position, b"v1");
        let engine = SnapshotEngine::new(&store);
        let past = Utc::now() - Duration::days(1);
        assert!(engine.snapshot_at(&query(past)).unwrap().is_empty());
    }

    #[test]
    fn snapshot_returns_latest_version_at_or_before() {
        let store = VersionStore::new();
        append(&store, "h-1", EntityType::Position, b"v1");
        let t1 = Utc::now();
        // Small sleep so v2's created_at is strictly after t1.
        std::thread::sleep(std::time::Duration::from_millis(5));
        append(&store, "h-1", EntityType::Position, b"v2");

        let engine = SnapshotEngine::new(&store);
        let at_t1 = engine.snapshot_at(&query(t1)).unwrap();
        assert_eq!(at_t1.len(), 1);
        assert_eq!(at_t1[0].version, 1);

        let now = engine.snapshot_at(&query(Utc::now())).unwrap();
        assert_eq!(now.len(), 1);
        assert_eq!(now[0].version, 2);
    }

    #[test]
    fn snapshot_filters_by_type_and_id() {
        let store = VersionStore::new();
        append(&store, "h-1", EntityType::Position, b"p");
        append(&store, "j-1", EntityType::DecisionEntry, b"j");
        let engine = SnapshotEngine::new(&store);

        let mut q = query(Utc::now());
        q.entity_types = vec![EntityType::DecisionEntry];
        let snap = engine.snapshot_at(&q).unwrap();
        assert_eq!(snap.len(), 1);
        assert_eq!(snap[0].entity_id, "j-1");

        let mut q2 = query(Utc::now());
        q2.entity_ids = vec!["h-1".to_string()];
        let snap2 = engine.snapshot_at(&q2).unwrap();
        assert_eq!(snap2.len(), 1);
        assert_eq!(snap2[0].entity_id, "h-1");
    }

    #[test]
    fn diff_reports_added_and_modified() {
        let store = VersionStore::new();
        append(&store, "h-1", EntityType::Position, b"v1");
        let t1 = Utc::now();
        std::thread::sleep(std::time::Duration::from_millis(5));
        append(&store, "h-1", EntityType::Position, b"v2");
        append(&store, "h-2", EntityType::Position, b"new");

        let engine = SnapshotEngine::new(&store);
        let d = engine.diff(t1, Utc::now(), &[]).unwrap();
        assert_eq!(d.added, vec!["h-2".to_string()]);
        assert!(d.removed.is_empty());
        assert_eq!(d.modified.len(), 1);
        assert_eq!(d.modified[0].entity_id, "h-1");
        assert_eq!(d.modified[0].from_version, 1);
        assert_eq!(d.modified[0].to_version, 2);
        assert_ne!(d.modified[0].from_hash, d.modified[0].to_hash);
    }

    #[test]
    fn diff_backwards_reports_removed() {
        let store = VersionStore::new();
        let t0 = Utc::now();
        append(&store, "h-1", EntityType::Position, b"v1");
        let engine = SnapshotEngine::new(&store);
        // Looking from "now" back to before the entity existed: it reads as removed.
        let d = engine.diff(Utc::now(), t0 - Duration::seconds(1), &[]).unwrap();
        assert_eq!(d.removed, vec!["h-1".to_string()]);
        assert!(d.added.is_empty());
    }
}
