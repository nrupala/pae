use axum::http::StatusCode;
use axum::Json;
use serde::{Deserialize, Serialize};

use crate::crypto::vault;
use crate::crypto::vault::CryptoError;

#[derive(Deserialize)]
pub struct DeriveKeyRequest {
    pub passphrase: String,
    pub salt: Option<String>,
}

#[derive(Serialize)]
pub struct DeriveKeyResponse {
    pub key_hash: String,
    pub salt: String,
}

#[derive(Deserialize)]
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
pub struct DecryptRequest {
    pub ciphertext_b64: String,
    pub nonce_b64: String,
    pub key_b64: String,
}

#[derive(Serialize)]
pub struct DecryptResponse {
    pub plaintext: String,
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

/// POST /api/v1/crypto/derive-key
///
/// Derives a 256-bit key from a passphrase using Argon2id.
/// Returns 400 if passphrase is empty or salt is invalid.
/// Returns 422 if key derivation fails.
pub async fn derive_key(
    Json(req): Json<DeriveKeyRequest>,
) -> Result<Json<DeriveKeyResponse>, (StatusCode, Json<CryptoErrorResponse>)> {
    let (key_hash, salt) = vault::derive_key(&req.passphrase, req.salt.as_deref())
        .map_err(into_error_response)?;
    Ok(Json(DeriveKeyResponse { key_hash, salt }))
}

/// POST /api/v1/crypto/encrypt
///
/// Encrypts plaintext with AES-256-GCM.
/// Returns 400 if key_b64 is not valid base64 or wrong length.
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
/// Decrypts ciphertext with AES-256-GCM.
/// Returns 400 if any base64 field is invalid or nonce is wrong length.
/// Returns 422 if decryption fails (wrong key, tampered data).
pub async fn decrypt(
    Json(req): Json<DecryptRequest>,
) -> Result<Json<DecryptResponse>, (StatusCode, Json<CryptoErrorResponse>)> {
    let plaintext = vault::decrypt(&req.ciphertext_b64, &req.nonce_b64, &req.key_b64)
        .map_err(into_error_response)?;
    Ok(Json(DecryptResponse { plaintext }))
}
