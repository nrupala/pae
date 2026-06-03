//! Holdings and portfolio CRUD endpoints, backed by encrypted SQLite.
//!
//! Endpoints:
//! - `GET    /api/v1/holdings`        list holdings (filter by portfolio/account)
//! - `POST   /api/v1/holdings`        add a holding
//! - `PUT    /api/v1/holdings/:id`    update a holding
//! - `DELETE /api/v1/holdings/:id`    delete a holding
//! - `GET    /api/v1/portfolios`      list portfolios
//! - `POST   /api/v1/portfolios`      create a portfolio
//!
//! Zero-knowledge: every sensitive field is a client-side AES-256-GCM
//! ciphertext blob. The engine validates structure and persists ciphertext;
//! it never sees plaintext symbols or financials. Base64 is used on the wire,
//! decoded to raw bytes before storage.

use std::sync::Arc;

use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::Json;
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use serde::{Deserialize, Serialize};

use crate::storage::{Holding, NewHolding, Portfolio, StorageError, Store};

/// Standard error response body for holdings/portfolio endpoints.
#[derive(Serialize)]
pub struct HoldingsErrorResponse {
    pub error: String,
    pub code: String,
}

/// Map a [`StorageError`] to an HTTP status + response body.
///
/// - NotFound            -> 404
/// - Validation / bad type -> 400
/// - everything else (Sqlite, pool, open) -> 500
fn storage_error_response(e: StorageError) -> (StatusCode, Json<HoldingsErrorResponse>) {
    let (status, code) = match &e {
        StorageError::NotFound { .. } => (StatusCode::NOT_FOUND, "NOT_FOUND"),
        StorageError::Validation(_) => (StatusCode::BAD_REQUEST, "VALIDATION_ERROR"),
        StorageError::InvalidAccountType(_) => (StatusCode::BAD_REQUEST, "INVALID_ACCOUNT_TYPE"),
        StorageError::Sqlite(_)
        | StorageError::OpenFailed { .. }
        | StorageError::PoolPoisoned
        | StorageError::PoolExhausted => (StatusCode::INTERNAL_SERVER_ERROR, "STORAGE_ERROR"),
    };

    if status.is_server_error() {
        tracing::error!(code = code, "storage error: {e}");
    } else {
        tracing::warn!(code = code, "storage error: {e}");
    }

    (
        status,
        Json(HoldingsErrorResponse {
            error: e.to_string(),
            code: code.to_string(),
        }),
    )
}

/// Build a 400 response for request-level validation failures.
fn bad_request(msg: impl Into<String>, code: &str) -> (StatusCode, Json<HoldingsErrorResponse>) {
    let msg = msg.into();
    tracing::warn!(code = code, "holdings API bad request: {msg}");
    (
        StatusCode::BAD_REQUEST,
        Json(HoldingsErrorResponse {
            error: msg,
            code: code.to_string(),
        }),
    )
}

/// Decode a required base64 field into bytes, or return a 400.
fn decode_required(
    value: &str,
    field: &str,
) -> Result<Vec<u8>, (StatusCode, Json<HoldingsErrorResponse>)> {
    if value.is_empty() {
        return Err(bad_request(format!("{field} must not be empty"), "EMPTY_FIELD"));
    }
    B64.decode(value)
        .map_err(|e| bad_request(format!("{field} is not valid base64: {e}"), "INVALID_BASE64"))
}

// --- Wire representations ---

/// A holding as returned to clients. Encrypted fields are base64-encoded;
/// the client decrypts them with the user-held key.
#[derive(Serialize)]
pub struct HoldingResponse {
    pub id: String,
    pub portfolio_id: String,
    pub account_id: Option<String>,
    pub symbol_encrypted_b64: String,
    pub symbol_nonce_b64: String,
    pub payload_encrypted_b64: String,
    pub payload_nonce_b64: String,
    pub created_at: String,
    pub updated_at: String,
}

impl From<Holding> for HoldingResponse {
    fn from(h: Holding) -> Self {
        HoldingResponse {
            id: h.id,
            portfolio_id: h.portfolio_id,
            account_id: h.account_id,
            symbol_encrypted_b64: B64.encode(&h.symbol_encrypted),
            symbol_nonce_b64: B64.encode(&h.symbol_nonce),
            payload_encrypted_b64: B64.encode(&h.payload_encrypted),
            payload_nonce_b64: B64.encode(&h.payload_nonce),
            created_at: h.created_at,
            updated_at: h.updated_at,
        }
    }
}

/// A portfolio as returned to clients.
#[derive(Serialize)]
pub struct PortfolioResponse {
    pub id: String,
    pub name_encrypted_b64: String,
    pub name_nonce_b64: String,
    pub created_at: String,
}

impl From<Portfolio> for PortfolioResponse {
    fn from(p: Portfolio) -> Self {
        PortfolioResponse {
            id: p.id,
            name_encrypted_b64: B64.encode(&p.name_encrypted),
            name_nonce_b64: B64.encode(&p.name_nonce),
            created_at: p.created_at,
        }
    }
}

// --- GET /api/v1/holdings ---

/// Query params for listing holdings.
#[derive(Deserialize)]
pub struct ListHoldingsQuery {
    /// Required: which portfolio to list holdings for.
    pub portfolio_id: String,
    /// Optional: further restrict to a single account.
    pub account_id: Option<String>,
}

/// Response wrapper for a list of holdings.
#[derive(Serialize)]
pub struct ListHoldingsResponse {
    pub portfolio_id: String,
    pub count: usize,
    pub holdings: Vec<HoldingResponse>,
}

/// `GET /api/v1/holdings?portfolio_id=...&account_id=...`
///
/// Lists every holding in a portfolio, optionally filtered by account.
/// Returns 400 if `portfolio_id` is missing/empty.
pub async fn list_holdings(
    State(store): State<Arc<Store>>,
    Query(q): Query<ListHoldingsQuery>,
) -> Result<Json<ListHoldingsResponse>, (StatusCode, Json<HoldingsErrorResponse>)> {
    if q.portfolio_id.trim().is_empty() {
        return Err(bad_request("portfolio_id query param is required", "MISSING_PORTFOLIO_ID"));
    }

    let rows = store
        .get_holdings_by_portfolio(&q.portfolio_id, q.account_id.as_deref())
        .map_err(storage_error_response)?;

    let holdings: Vec<HoldingResponse> = rows.into_iter().map(HoldingResponse::from).collect();
    Ok(Json(ListHoldingsResponse {
        portfolio_id: q.portfolio_id,
        count: holdings.len(),
        holdings,
    }))
}

// --- POST /api/v1/holdings ---

/// Request body to create a holding. All financial content is supplied as
/// client-side ciphertext (base64).
#[derive(Deserialize)]
pub struct CreateHoldingRequest {
    pub portfolio_id: String,
    pub account_id: Option<String>,
    pub symbol_encrypted_b64: String,
    pub symbol_nonce_b64: String,
    pub payload_encrypted_b64: String,
    pub payload_nonce_b64: String,
}

/// Response after creating a holding.
#[derive(Serialize)]
pub struct CreateHoldingResponse {
    pub id: String,
}

/// `POST /api/v1/holdings`
///
/// Adds a holding to a portfolio. Returns 400 for empty/invalid base64 or
/// an empty portfolio_id; 404 if the portfolio does not exist.
pub async fn create_holding(
    State(store): State<Arc<Store>>,
    Json(req): Json<CreateHoldingRequest>,
) -> Result<(StatusCode, Json<CreateHoldingResponse>), (StatusCode, Json<HoldingsErrorResponse>)> {
    if req.portfolio_id.trim().is_empty() {
        return Err(bad_request("portfolio_id must not be empty", "EMPTY_PORTFOLIO_ID"));
    }

    let symbol_encrypted = decode_required(&req.symbol_encrypted_b64, "symbol_encrypted_b64")?;
    let symbol_nonce = decode_required(&req.symbol_nonce_b64, "symbol_nonce_b64")?;
    let payload_encrypted = decode_required(&req.payload_encrypted_b64, "payload_encrypted_b64")?;
    let payload_nonce = decode_required(&req.payload_nonce_b64, "payload_nonce_b64")?;

    let new = NewHolding {
        portfolio_id: req.portfolio_id,
        account_id: req.account_id,
        symbol_encrypted,
        symbol_nonce,
        payload_encrypted,
        payload_nonce,
    };

    let id = store.insert_holding(&new).map_err(storage_error_response)?;
    Ok((StatusCode::CREATED, Json(CreateHoldingResponse { id })))
}

// --- PUT /api/v1/holdings/:id ---

/// Request body to update a holding's encrypted symbol/payload.
#[derive(Deserialize)]
pub struct UpdateHoldingRequest {
    pub symbol_encrypted_b64: String,
    pub symbol_nonce_b64: String,
    pub payload_encrypted_b64: String,
    pub payload_nonce_b64: String,
}

/// Generic `{ "status": "ok" }`-style acknowledgement.
#[derive(Serialize)]
pub struct AckResponse {
    pub id: String,
    pub status: &'static str,
}

/// `PUT /api/v1/holdings/:id`
///
/// Replaces the encrypted symbol and payload of an existing holding.
/// Returns 400 for invalid base64; 404 if the holding does not exist.
pub async fn update_holding(
    State(store): State<Arc<Store>>,
    Path(id): Path<String>,
    Json(req): Json<UpdateHoldingRequest>,
) -> Result<Json<AckResponse>, (StatusCode, Json<HoldingsErrorResponse>)> {
    if id.trim().is_empty() {
        return Err(bad_request("holding id must not be empty", "EMPTY_ID"));
    }

    let symbol_encrypted = decode_required(&req.symbol_encrypted_b64, "symbol_encrypted_b64")?;
    let symbol_nonce = decode_required(&req.symbol_nonce_b64, "symbol_nonce_b64")?;
    let payload_encrypted = decode_required(&req.payload_encrypted_b64, "payload_encrypted_b64")?;
    let payload_nonce = decode_required(&req.payload_nonce_b64, "payload_nonce_b64")?;

    store
        .update_holding(&id, &symbol_encrypted, &symbol_nonce, &payload_encrypted, &payload_nonce)
        .map_err(storage_error_response)?;

    Ok(Json(AckResponse { id, status: "updated" }))
}

// --- DELETE /api/v1/holdings/:id ---

/// `DELETE /api/v1/holdings/:id`
///
/// Deletes a holding. Returns 404 if it does not exist.
pub async fn delete_holding(
    State(store): State<Arc<Store>>,
    Path(id): Path<String>,
) -> Result<Json<AckResponse>, (StatusCode, Json<HoldingsErrorResponse>)> {
    if id.trim().is_empty() {
        return Err(bad_request("holding id must not be empty", "EMPTY_ID"));
    }

    store.delete_holding(&id).map_err(storage_error_response)?;
    Ok(Json(AckResponse { id, status: "deleted" }))
}

// --- GET /api/v1/portfolios ---

/// Response wrapper for a list of portfolios.
#[derive(Serialize)]
pub struct ListPortfoliosResponse {
    pub count: usize,
    pub portfolios: Vec<PortfolioResponse>,
}

/// `GET /api/v1/portfolios`
///
/// Lists all portfolios, newest first.
pub async fn list_portfolios(
    State(store): State<Arc<Store>>,
) -> Result<Json<ListPortfoliosResponse>, (StatusCode, Json<HoldingsErrorResponse>)> {
    let rows = store.list_portfolios().map_err(storage_error_response)?;
    let portfolios: Vec<PortfolioResponse> =
        rows.into_iter().map(PortfolioResponse::from).collect();
    Ok(Json(ListPortfoliosResponse {
        count: portfolios.len(),
        portfolios,
    }))
}

// --- POST /api/v1/portfolios ---

/// Request body to create a portfolio. The name is client-side ciphertext.
#[derive(Deserialize)]
pub struct CreatePortfolioRequest {
    pub name_encrypted_b64: String,
    pub name_nonce_b64: String,
}

/// Response after creating a portfolio.
#[derive(Serialize)]
pub struct CreatePortfolioResponse {
    pub id: String,
}

/// `POST /api/v1/portfolios`
///
/// Creates a portfolio. Returns 400 for empty/invalid base64 fields.
pub async fn create_portfolio(
    State(store): State<Arc<Store>>,
    Json(req): Json<CreatePortfolioRequest>,
) -> Result<(StatusCode, Json<CreatePortfolioResponse>), (StatusCode, Json<HoldingsErrorResponse>)> {
    let name_encrypted = decode_required(&req.name_encrypted_b64, "name_encrypted_b64")?;
    let name_nonce = decode_required(&req.name_nonce_b64, "name_nonce_b64")?;

    let id = store
        .insert_portfolio(&name_encrypted, &name_nonce)
        .map_err(storage_error_response)?;

    Ok((StatusCode::CREATED, Json(CreatePortfolioResponse { id })))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_holding_response_roundtrip_encodes_base64() {
        let h = Holding {
            id: "h1".to_string(),
            portfolio_id: "p1".to_string(),
            account_id: None,
            symbol_encrypted: b"enc".to_vec(),
            symbol_nonce: b"non".to_vec(),
            payload_encrypted: b"pay".to_vec(),
            payload_nonce: b"pn".to_vec(),
            created_at: "2026-01-01T00:00:00+00:00".to_string(),
            updated_at: "2026-01-01T00:00:00+00:00".to_string(),
        };
        let resp = HoldingResponse::from(h);
        assert_eq!(resp.symbol_encrypted_b64, B64.encode(b"enc"));
        assert_eq!(resp.payload_encrypted_b64, B64.encode(b"pay"));
    }

    #[test]
    fn test_decode_required_rejects_empty_and_bad_base64() {
        assert!(decode_required("", "f").is_err());
        assert!(decode_required("not valid base64!!!", "f").is_err());
        assert!(decode_required(&B64.encode(b"ok"), "f").is_ok());
    }

    #[test]
    fn test_storage_error_status_mapping() {
        let (s, _) = storage_error_response(StorageError::NotFound {
            entity: "holding",
            id: "x".to_string(),
        });
        assert_eq!(s, StatusCode::NOT_FOUND);

        let (s, _) = storage_error_response(StorageError::Validation("bad".to_string()));
        assert_eq!(s, StatusCode::BAD_REQUEST);

        let (s, _) = storage_error_response(StorageError::Sqlite("boom".to_string()));
        assert_eq!(s, StatusCode::INTERNAL_SERVER_ERROR);
    }
}
