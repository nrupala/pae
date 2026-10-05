use std::sync::Arc;

use axum::extract::State;
use axum::http::StatusCode;
use axum::Json;
use serde::{Deserialize, Serialize};

use crate::crypto::vault;
use crate::crypto::vault::CryptoError;
use crate::storage::Store;

/// Argon2id parameters the server expects clients to use for key
/// derivation. These MUST match the production spec served by
/// `GET /api/v1/crypto/kdf-params` (kept in sync by the kdf-params test).
pub const KDF_ALGORITHM: &str = "argon2id";
pub const KDF_VERSION: u32 = 19;
pub const KDF_MEMORY_KIB: u32 = 65536;
pub const KDF_ITERATIONS: u32 = 600_000;
pub const KDF_PARALLELISM: u32 = 4;
pub const KDF_OUTPUT_BYTES: u32 = 32;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EncryptRequest {
    pub plaintext: String,
    pub key_b64: String,
}

#[derive(Serialize)]
pub struct EncryptResponse {
    pub ciphertext_b64: String,
    pub nonce_b64: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecryptRequest {
    pub ciphertext_b64: String,
    pub nonce_b64: String,
    pub key_b64: String,
}

#[derive(Serialize)]
pub struct DecryptResponse {
    pub plaintext: String,
}

#[derive(Serialize)]
pub struct KdfParamsResponse {
    pub algorithm: &'static str,
    pub version: u32,
    pub memory_kib: u32,
    pub iterations: u32,
    pub parallelism: u32,
    pub output_bytes: u32,
    pub salt_b64: String,
}

/// Standard error response body for crypto endpoints.
#[derive(Serialize)]
pub struct CryptoErrorResponse {
    pub error: String,
    pub code: String,
}

/// Map CryptoError to an HTTP status code.
/// - Input validation errors (empty passphrase, bad base64, bad lengths) -> 400
/// - Processing failures (encryption/decryption failed) -> 422
/// - Internal/unexpected errors -> 500
fn crypto_error_to_status(err: &CryptoError) -> StatusCode {
    match err {
        CryptoError::EmptyPassphrase
        | CryptoError::InvalidBase64 { .. }
        | CryptoError::InvalidKeyLength
        | CryptoError::InvalidSalt(_)
        | CryptoError::InvalidNonceLength(_) => StatusCode::BAD_REQUEST,

        CryptoError::DecryptionFailed
        | CryptoError::EncryptionFailed(_)
        | CryptoError::DerivationFailed(_)
        | CryptoError::InvalidUtf8 => StatusCode::UNPROCESSABLE_ENTITY,

        CryptoError::InvalidParams(_) => StatusCode::INTERNAL_SERVER_ERROR,
    }
}

/// Map a CryptoError to an error code string for the response body.
fn crypto_error_code(err: &CryptoError) -> &'static str {
    match err {
        CryptoError::EmptyPassphrase => "EMPTY_PASSPHRASE",
        CryptoError::InvalidBase64 { .. } => "INVALID_BASE64",
        CryptoError::InvalidKeyLength => "INVALID_KEY_LENGTH",
        CryptoError::InvalidSalt(_) => "INVALID_SALT",
        CryptoError::InvalidNonceLength(_) => "INVALID_NONCE_LENGTH",
        CryptoError::DecryptionFailed => "DECRYPTION_FAILED",
        CryptoError::EncryptionFailed(_) => "ENCRYPTION_FAILED",
        CryptoError::DerivationFailed(_) => "DERIVATION_FAILED",
        CryptoError::InvalidUtf8 => "INVALID_UTF8",
        CryptoError::InvalidParams(_) => "INVALID_PARAMS",
    }
}

/// Helper to convert CryptoError into an axum-compatible error response.
fn into_error_response(err: CryptoError) -> (StatusCode, Json<CryptoErrorResponse>) {
    let status = crypto_error_to_status(&err);
    let code = crypto_error_code(&err).to_string();
    (status, Json(CryptoErrorResponse {
        error: err.to_string(),
        code,
    }))
}

// NOTE (2026-10-05, zero-knowledge fix): the old
// `POST /api/v1/crypto/derive-key` handler and its
// `DeriveKeyRequest`/`DeriveKeyResponse` types were deleted here. The
// endpoint took a RAW passphrase server-side, inverting the threat model.
// Key derivation now happens exclusively client-side
// (ui/src/crypto/vault-client.ts). The server never sees a passphrase.

/// GET /api/v1/crypto/kdf-params
///
/// Serves the Argon2id parameters clients must use for key derivation,
/// plus the per-database salt (base64). The salt is NOT secret — it is
/// persisted in the SQLite `meta` table so it stays stable across
/// restarts; only stability matters, not secrecy.
pub async fn kdf_params(
    State(store): State<Arc<Store>>,
) -> Result<Json<KdfParamsResponse>, (StatusCode, Json<CryptoErrorResponse>)> {
    let salt_b64 = store.get_kdf_salt().map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(CryptoErrorResponse {
                error: format!("failed to load KDF salt: {e}"),
                code: "KDF_SALT_UNAVAILABLE".to_string(),
            }),
        )
    })?;
    Ok(Json(KdfParamsResponse {
        algorithm: KDF_ALGORITHM,
        version: KDF_VERSION,
        memory_kib: KDF_MEMORY_KIB,
        iterations: KDF_ITERATIONS,
        parallelism: KDF_PARALLELISM,
        output_bytes: KDF_OUTPUT_BYTES,
        salt_b64,
    }))
}

/// POST /api/v1/crypto/encrypt
///
/// Encrypts plaintext with AES-256-GCM using a caller-supplied key.
/// Returns 400 if key_b64 is not valid base64 or wrong length, or if the
/// request body contains unknown fields (e.g. "passphrase").
/// Returns 422 if encryption fails.
pub async fn encrypt(
    Json(req): Json<EncryptRequest>,
) -> Result<Json<EncryptResponse>, (StatusCode, Json<CryptoErrorResponse>)> {
    let (ciphertext, nonce) = vault::encrypt(&req.plaintext, &req.key_b64)
        .map_err(into_error_response)?;
    Ok(Json(EncryptResponse {
        ciphertext_b64: ciphertext,
        nonce_b64: nonce,
    }))
}

/// POST /api/v1/crypto/decrypt
///
/// Decrypts ciphertext with AES-256-GCM using a caller-supplied key.
/// Returns 400 if any base64 field is invalid or nonce is wrong length,
/// or if the request body contains unknown fields (e.g. "passphrase").
/// Returns 422 if decryption fails (wrong key, tampered data).
pub async fn decrypt(
    Json(req): Json<DecryptRequest>,
) -> Result<Json<DecryptResponse>, (StatusCode, Json<CryptoErrorResponse>)> {
    let plaintext = vault::decrypt(&req.ciphertext_b64, &req.nonce_b64, &req.key_b64)
        .map_err(into_error_response)?;
    Ok(Json(DecryptResponse { plaintext }))
}
