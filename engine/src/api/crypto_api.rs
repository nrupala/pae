// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

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

/// Request body for `POST /api/v1/crypto/dek-envelope`. The envelope is
/// the client's KEK-wrapped DEK as a JSON string — opaque ciphertext to
/// the engine. Unknown fields (e.g. "passphrase", "kek") are rejected.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SetDekEnvelopeRequest {
    pub envelope: String,
}

/// Response body for `GET /api/v1/crypto/dek-envelope`. `envelope` is
/// `None` for v1 vaults that have no DEK envelope yet.
#[derive(Serialize)]
pub struct DekEnvelopeResponse {
    pub envelope: Option<String>,
}

/// Standard error response body for crypto endpoints.
#[derive(Serialize)]
pub struct CryptoErrorResponse {
    pub error: String,
    pub code: String,
}

/// Map CryptoError to an HTTP status code.
/// - Input validation errors (bad base64, bad lengths) -> 400
/// - Processing failures (encryption/decryption failed) -> 422
fn crypto_error_to_status(err: &CryptoError) -> StatusCode {
    match err {
        CryptoError::InvalidBase64 { .. }
        | CryptoError::InvalidKeyLength
        | CryptoError::InvalidNonceLength(_) => StatusCode::BAD_REQUEST,

        CryptoError::DecryptionFailed
        | CryptoError::EncryptionFailed(_)
        | CryptoError::InvalidUtf8 => StatusCode::UNPROCESSABLE_ENTITY,
    }
}

/// Map a CryptoError to an error code string for the response body.
fn crypto_error_code(err: &CryptoError) -> &'static str {
    match err {
        CryptoError::InvalidBase64 { .. } => "INVALID_BASE64",
        CryptoError::InvalidKeyLength => "INVALID_KEY_LENGTH",
        CryptoError::InvalidNonceLength(_) => "INVALID_NONCE_LENGTH",
        CryptoError::DecryptionFailed => "DECRYPTION_FAILED",
        CryptoError::EncryptionFailed(_) => "ENCRYPTION_FAILED",
        CryptoError::InvalidUtf8 => "INVALID_UTF8",
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

/// GET /api/v1/crypto/dek-envelope
///
/// Returns the stored v2 DEK envelope JSON (opaque to the engine — the
/// client's KEK-wrapped DEK), or `{"envelope": null}` when the vault has
/// no envelope yet (v1 vaults: records encrypted directly with the
/// KEK-derived key).
pub async fn get_dek_envelope(
    State(store): State<Arc<Store>>,
) -> Result<Json<DekEnvelopeResponse>, (StatusCode, Json<CryptoErrorResponse>)> {
    let envelope = store.get_dek_envelope().map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(CryptoErrorResponse {
                error: format!("failed to load DEK envelope: {e}"),
                code: "DEK_ENVELOPE_UNAVAILABLE".to_string(),
            }),
        )
    })?;
    Ok(Json(DekEnvelopeResponse { envelope }))
}

/// Validate the envelope JSON shape opaquely: it must be an object with
/// `v == 2` and non-empty base64 wrap fields. The engine cannot (and must
/// not) inspect the wrapped key material — this is a client-bug guard,
/// not a cryptographic check.
fn validate_envelope_shape(envelope: &str) -> Result<(), String> {
    let v: serde_json::Value =
        serde_json::from_str(envelope).map_err(|e| format!("envelope is not valid JSON: {e}"))?;
    let obj = v
        .as_object()
        .ok_or_else(|| "envelope must be a JSON object".to_string())?;
    if obj.get("v").and_then(serde_json::Value::as_u64) != Some(2) {
        return Err("envelope.v must be 2".to_string());
    }
    for field in ["wrapped_dek_b64", "dek_nonce_b64"] {
        match obj.get(field).and_then(serde_json::Value::as_str) {
            Some(s) if !s.is_empty() => {}
            _ => return Err(format!("envelope.{field} must be a non-empty string")),
        }
    }
    Ok(())
}

/// POST /api/v1/crypto/dek-envelope
///
/// Stores the client's KEK-wrapped DEK envelope verbatim. The envelope is
/// opaque ciphertext — the engine never sees the passphrase, the KEK, or
/// the unwrapped DEK. Returns 400 if the envelope JSON is malformed;
/// unknown request fields (e.g. "passphrase", "kek") are rejected with
/// 422 by `deny_unknown_fields`.
pub async fn set_dek_envelope(
    State(store): State<Arc<Store>>,
    Json(req): Json<SetDekEnvelopeRequest>,
) -> Result<StatusCode, (StatusCode, Json<CryptoErrorResponse>)> {
    if let Err(msg) = validate_envelope_shape(&req.envelope) {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(CryptoErrorResponse {
                error: msg,
                code: "INVALID_ENVELOPE".to_string(),
            }),
        ));
    }
    store.set_dek_envelope(&req.envelope).map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(CryptoErrorResponse {
                error: format!("failed to store DEK envelope: {e}"),
                code: "DEK_ENVELOPE_UNAVAILABLE".to_string(),
            }),
        )
    })?;
    Ok(StatusCode::NO_CONTENT)
}
