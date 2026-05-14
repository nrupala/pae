use axum::http::StatusCode;
use axum::Json;
use axum::extract::{Path, State};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

use crate::versioning::store::{VersionStore, VersionStoreError};
use crate::versioning::types::{EntityType, VersionAuthor};

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
#[allow(dead_code)]
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

#[derive(Serialize)]
pub struct ApiError {
    pub error: String,
    pub code: String,
}

type ApiResult<T> = Result<Json<T>, (StatusCode, Json<ApiError>)>;

fn bad_request(msg: impl Into<String>, code: impl Into<String>) -> (StatusCode, Json<ApiError>) {
    let msg = msg.into();
    let code = code.into();
    tracing::warn!(error = %msg, code = %code, "versioning API error");
    (
        StatusCode::BAD_REQUEST,
        Json(ApiError { error: msg, code }),
    )
}

fn internal_error(e: VersionStoreError) -> (StatusCode, Json<ApiError>) {
    tracing::error!(error = %e, "versioning store error");
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(ApiError {
            error: e.to_string(),
            code: "STORE_ERROR".to_string(),
        }),
    )
}

/// Parse a string entity type into the enum.
fn parse_entity_type(s: &str) -> Result<EntityType, (StatusCode, Json<ApiError>)> {
    match s {
        "holdings" => Ok(EntityType::Holdings),
        "position" => Ok(EntityType::Position),
        "decision_entry" => Ok(EntityType::DecisionEntry),
        "calibration_record" => Ok(EntityType::CalibrationRecord),
        "knowledge_chunk" => Ok(EntityType::KnowledgeChunk),
        "knowledge_annotation" => Ok(EntityType::KnowledgeAnnotation),
        "configuration" => Ok(EntityType::Configuration),
        "stress_test_config" => Ok(EntityType::StressTestConfig),
        "monte_carlo_config" => Ok(EntityType::MonteCarloConfig),
        "carry_snapshot" => Ok(EntityType::CarrySnapshot),
        other => Err(bad_request(
            format!("Unknown entity type: '{other}'"),
            "INVALID_ENTITY_TYPE",
        )),
    }
}

/// Convert an internal VersionedRecord to the API response format.
fn to_api_record(r: &crate::versioning::types::VersionedRecord) -> VersionRecord {
    use base64::{Engine as _, engine::general_purpose::STANDARD as B64};
    VersionRecord {
        version_hash: r.version_hash.clone(),
        entity_id: r.entity_id.clone(),
        entity_type: format!("{:?}", r.entity_type),
        version: r.version,
        created_at: r.created_at.to_rfc3339(),
        change_summary: r.metadata.change_summary.clone(),
        tags: r.metadata.tags.clone(),
        content_encrypted_b64: B64.encode(&r.content_encrypted),
        nonce_b64: B64.encode(&r.nonce),
        parent_hash: r.parent_hash.clone(),
    }
}

// --- Handlers (wired to VersionStore via Axum shared state) ---

pub async fn append_version(
    State(store): State<Arc<VersionStore>>,
    Json(input): Json<AppendVersionRequest>,
) -> ApiResult<AppendVersionResponse> {
    if input.entity_id.is_empty() {
        return Err(bad_request("entity_id must not be empty", "EMPTY_ENTITY_ID"));
    }
    if input.content_encrypted_b64.is_empty() {
        return Err(bad_request("content_encrypted_b64 must not be empty", "EMPTY_CONTENT"));
    }
    if input.nonce_b64.is_empty() {
        return Err(bad_request("nonce_b64 must not be empty", "EMPTY_NONCE"));
    }

    let entity_type = parse_entity_type(&input.entity_type)?;

    use base64::{Engine as _, engine::general_purpose::STANDARD as B64};
    let content = B64.decode(&input.content_encrypted_b64)
        .map_err(|e| bad_request(format!("Invalid content base64: {e}"), "INVALID_BASE64"))?;
    let nonce = B64.decode(&input.nonce_b64)
        .map_err(|e| bad_request(format!("Invalid nonce base64: {e}"), "INVALID_BASE64"))?;

    let version_hash = store
        .append(
            &input.entity_id,
            entity_type,
            content,
            nonce,
            VersionAuthor::User,
            input.change_summary,
            input.tags,
        )
        .map_err(internal_error)?;

    // Retrieve the version number from the latest record
    let latest = store
        .get_latest(&input.entity_id)
        .map_err(internal_error)?;
    let version = latest.map(|r| r.version).unwrap_or(1);

    tracing::info!(entity_id = %input.entity_id, version = version, "version appended");

    Ok(Json(AppendVersionResponse {
        version_hash,
        version,
    }))
}

pub async fn get_history(
    State(store): State<Arc<VersionStore>>,
    Json(input): Json<GetHistoryRequest>,
) -> ApiResult<GetHistoryResponse> {
    if input.entity_id.is_empty() {
        return Err(bad_request("entity_id must not be empty", "EMPTY_ENTITY_ID"));
    }

    let query = crate::versioning::types::VersionQuery {
        entity_id: input.entity_id.clone(),
        entity_type: None,
        since: None,
        until: None,
        limit: input.limit,
        latest_only: input.latest_only.unwrap_or(false),
    };

    let records = store.query(&query).map_err(internal_error)?;
    let total = records.len();
    let versions: Vec<VersionRecord> = records.iter().map(to_api_record).collect();

    Ok(Json(GetHistoryResponse {
        entity_id: input.entity_id,
        total_versions: total,
        versions,
    }))
}

pub async fn get_snapshot(
    Json(_input): Json<SnapshotRequest>,
) -> ApiResult<SnapshotResponse> {
    // TODO: Wire to SnapshotEngine once production SQLite backend is ready
    Ok(Json(SnapshotResponse {
        as_of: _input.as_of,
        entities: vec![],
    }))
}

pub async fn verify_integrity(
    State(store): State<Arc<VersionStore>>,
    Path(entity_id): Path<String>,
) -> ApiResult<IntegrityResponse> {
    if entity_id.is_empty() {
        return Err(bad_request("entity_id must not be empty", "EMPTY_ENTITY_ID"));
    }

    let chain_valid = store.verify_chain(&entity_id).map_err(internal_error)?;

    let query = crate::versioning::types::VersionQuery {
        entity_id: entity_id.clone(),
        entity_type: None,
        since: None,
        until: None,
        limit: None,
        latest_only: false,
    };
    let records = store.query(&query).map_err(internal_error)?;

    Ok(Json(IntegrityResponse {
        entity_id,
        chain_valid,
        total_versions: records.len(),
    }))
}
