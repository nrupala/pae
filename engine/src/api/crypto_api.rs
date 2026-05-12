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

pub async fn derive_key(Json(req): Json<DeriveKeyRequest>) -> Json<DeriveKeyResponse> {
    let (key_hash, salt) = vault::derive_key(&req.passphrase, req.salt.as_deref());
    Json(DeriveKeyResponse { key_hash, salt })
}

pub async fn encrypt(Json(req): Json<EncryptRequest>) -> Json<EncryptResponse> {
    let (ciphertext, nonce) = vault::encrypt(&req.plaintext, &req.key_b64);
    Json(EncryptResponse {
        ciphertext_b64: ciphertext,
        nonce_b64: nonce,
    })
}

pub async fn decrypt(Json(req): Json<DecryptRequest>) -> Json<DecryptResponse> {
    let plaintext = vault::decrypt(&req.ciphertext_b64, &req.nonce_b64, &req.key_b64);
    Json(DecryptResponse { plaintext })
}
