use axum::http::StatusCode;
use axum::Json;
use serde::{Deserialize, Serialize};

use crate::crypto::vault;

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

#[derive(Serialize)]
pub struct ApiError {
    pub error: String,
    pub code: String,
}

type ApiResult<T> = Result<Json<T>, (StatusCode, Json<ApiError>)>;

fn crypto_err(e: vault::CryptoError) -> (StatusCode, Json<ApiError>) {
    let (status, code) = match &e {
        vault::CryptoError::EmptyPassphrase
        | vault::CryptoError::EmptyPlaintext
        | vault::CryptoError::InvalidBase64(_)
        | vault::CryptoError::InvalidKeyLength { .. }
        | vault::CryptoError::InvalidNonceLength { .. }
        | vault::CryptoError::InvalidSalt(_) => (StatusCode::BAD_REQUEST, "INVALID_INPUT"),
        vault::CryptoError::DecryptionFailed(_) => (StatusCode::UNPROCESSABLE_ENTITY, "DECRYPTION_FAILED"),
        _ => (StatusCode::INTERNAL_SERVER_ERROR, "CRYPTO_ERROR"),
    };
    tracing::warn!(error = %e, code = code, "crypto API error");
    (
        status,
        Json(ApiError {
            error: e.to_string(),
            code: code.to_string(),
        }),
    )
}

pub async fn derive_key(Json(req): Json<DeriveKeyRequest>) -> ApiResult<DeriveKeyResponse> {
    let (key_hash, salt) = vault::derive_key(&req.passphrase, req.salt.as_deref())
        .map_err(crypto_err)?;
    Ok(Json(DeriveKeyResponse { key_hash, salt }))
}

pub async fn encrypt(Json(req): Json<EncryptRequest>) -> ApiResult<EncryptResponse> {
    let (ciphertext, nonce) = vault::encrypt(&req.plaintext, &req.key_b64)
        .map_err(crypto_err)?;
    Ok(Json(EncryptResponse {
        ciphertext_b64: ciphertext,
        nonce_b64: nonce,
    }))
}

pub async fn decrypt(Json(req): Json<DecryptRequest>) -> ApiResult<DecryptResponse> {
    let plaintext = vault::decrypt(&req.ciphertext_b64, &req.nonce_b64, &req.key_b64)
        .map_err(crypto_err)?;
    Ok(Json(DecryptResponse { plaintext }))
}
