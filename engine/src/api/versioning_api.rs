use axum::Json;
use serde::{Deserialize, Serialize};

/// API endpoints for the versioning system.
/// All content is encrypted client-side before reaching these endpoints.

#[derive(Deserialize)]
pub struct AppendVersionRequest {
    pub entity_id: String,
    pub entity_type: String,
    pub content_encrypted_b64: String,
    pub nonce_b64: String,
    pub change_summary: Option<String>,
    pub tags: Vec<String>,
}

#[derive(Serialize)]
pub struct AppendVersionResponse {
    pub version_hash: String,
    pub version: u64,
}

#[derive(Deserialize)]
pub struct GetHistoryRequest {
    pub entity_id: String,
    pub limit: Option<usize>,
    pub latest_only: Option<bool>,
}

#[derive(Serialize)]
pub struct VersionRecord {
    pub version_hash: String,
    pub entity_id: String,
    pub entity_type: String,
    pub version: u64,
    pub created_at: String,
    pub change_summary: Option<String>,
    pub tags: Vec<String>,
    pub content_encrypted_b64: String,
    pub nonce_b64: String,
    pub parent_hash: Option<String>,
}

#[derive(Serialize)]
pub struct GetHistoryResponse {
    pub entity_id: String,
    pub total_versions: usize,
    pub versions: Vec<VersionRecord>,
}

#[derive(Deserialize)]
pub struct SnapshotRequest {
    pub as_of: String,
    pub entity_types: Vec<String>,
}

#[derive(Serialize)]
pub struct SnapshotResponse {
    pub as_of: String,
    pub entities: Vec<VersionRecord>,
}

#[derive(Serialize)]
pub struct IntegrityResponse {
    pub entity_id: String,
    pub chain_valid: bool,
    pub total_versions: usize,
}

// --- Handlers (stubs -- will wire to VersionStore in Axum state) ---

pub async fn append_version(
    Json(_input): Json<AppendVersionRequest>,
) -> Json<AppendVersionResponse> {
    Json(AppendVersionResponse {
        version_hash: "stub".to_string(),
        version: 0,
    })
}

pub async fn get_history(
    Json(_input): Json<GetHistoryRequest>,
) -> Json<GetHistoryResponse> {
    Json(GetHistoryResponse {
        entity_id: "stub".to_string(),
        total_versions: 0,
        versions: vec![],
    })
}

pub async fn get_snapshot(
    Json(_input): Json<SnapshotRequest>,
) -> Json<SnapshotResponse> {
    Json(SnapshotResponse {
        as_of: "stub".to_string(),
        entities: vec![],
    })
}

pub async fn verify_integrity(
    axum::extract::Path(entity_id): axum::extract::Path<String>,
) -> Json<IntegrityResponse> {
    Json(IntegrityResponse {
        entity_id,
        chain_valid: true,
        total_versions: 0,
    })
}
