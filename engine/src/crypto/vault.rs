// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

use aes_gcm::{
    aead::{Aead, KeyInit, OsRng},
    Aes256Gcm, Nonce,
};
use base64::{Engine as _, engine::general_purpose::STANDARD as B64};
use rand::RngCore;
use thiserror::Error;

/// Errors that can occur during cryptographic operations.
#[derive(Debug, Error)]
pub enum CryptoError {
    #[error("Invalid base64 encoding: {context}")]
    InvalidBase64 { context: String },

    #[error("Invalid key length: expected 32 bytes")]
    InvalidKeyLength,

    #[error("Encryption failed: {0}")]
    EncryptionFailed(String),

    #[error("Decryption failed: authentication or data corrupted")]
    DecryptionFailed,

    #[error("Invalid nonce length: expected 12 bytes, got {0}")]
    InvalidNonceLength(usize),

    #[error("Invalid UTF-8 in decrypted plaintext")]
    InvalidUtf8,
}

// NOTE (2026-10-05, zero-knowledge fix): this module used to contain
// `derive_key()`, a server-side Argon2id passphrase-derivation function
// exposed via POST /api/v1/crypto/derive-key. That inverted the threat
// model: a deployed operator saw every passphrase submitted. Key
// derivation now happens exclusively client-side
// (ui/src/crypto/vault-client.ts) against the parameters served by
// GET /api/v1/crypto/kdf-params. The server holds NO passphrase-
// derivation code path, so the passphrase-related error variants
// (InvalidSalt, DerivationFailed, EmptyPassphrase, InvalidParams)
// were removed 2026-10-05 along with it.

/// Encrypt plaintext with AES-256-GCM.
///
/// `key_b64`: base64-encoded 32-byte key.
/// Returns `(ciphertext_b64, nonce_b64)`.
///
/// # Errors
///
/// Returns `CryptoError::InvalidBase64` if the key is not valid base64.
/// Returns `CryptoError::InvalidKeyLength` if the decoded key is not 32 bytes.
/// Returns `CryptoError::EncryptionFailed` if AES-GCM encryption fails.
pub fn encrypt(plaintext: &str, key_b64: &str) -> Result<(String, String), CryptoError> {
    let key_bytes = B64.decode(key_b64)
        .map_err(|_| CryptoError::InvalidBase64 { context: "key".to_string() })?;

    let cipher = Aes256Gcm::new_from_slice(&key_bytes)
        .map_err(|_| CryptoError::InvalidKeyLength)?;

    let mut nonce_bytes = [0u8; 12];
    OsRng.fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);

    let ciphertext = cipher
        .encrypt(nonce, plaintext.as_bytes())
        .map_err(|e| CryptoError::EncryptionFailed(e.to_string()))?;

    Ok((B64.encode(&ciphertext), B64.encode(nonce_bytes)))
}

/// Decrypt ciphertext with AES-256-GCM.
///
/// Returns the plaintext string.
///
/// # Errors
///
/// Returns `CryptoError::InvalidBase64` if any input is not valid base64.
/// Returns `CryptoError::InvalidKeyLength` if the decoded key is not 32 bytes.
/// Returns `CryptoError::InvalidNonceLength` if the decoded nonce is not 12 bytes.
/// Returns `CryptoError::DecryptionFailed` if authentication fails (wrong key, tampered data).
/// Returns `CryptoError::InvalidUtf8` if decrypted bytes are not valid UTF-8.
pub fn decrypt(ciphertext_b64: &str, nonce_b64: &str, key_b64: &str) -> Result<String, CryptoError> {
    let key_bytes = B64.decode(key_b64)
        .map_err(|_| CryptoError::InvalidBase64 { context: "key".to_string() })?;
    let ciphertext = B64.decode(ciphertext_b64)
        .map_err(|_| CryptoError::InvalidBase64 { context: "ciphertext".to_string() })?;
    let nonce_bytes = B64.decode(nonce_b64)
        .map_err(|_| CryptoError::InvalidBase64 { context: "nonce".to_string() })?;

    if nonce_bytes.len() != 12 {
        return Err(CryptoError::InvalidNonceLength(nonce_bytes.len()));
    }

    let cipher = Aes256Gcm::new_from_slice(&key_bytes)
        .map_err(|_| CryptoError::InvalidKeyLength)?;
    let nonce = Nonce::from_slice(&nonce_bytes);

    let plaintext = cipher
        .decrypt(nonce, ciphertext.as_ref())
        .map_err(|_| CryptoError::DecryptionFailed)?;

    String::from_utf8(plaintext)
        .map_err(|_| CryptoError::InvalidUtf8)
}

#[cfg(test)]
mod tests {
    use super::*;
    // (no local import: `base64::Engine` trait is in scope via `super::*`)

    #[test]
    fn test_encrypt_decrypt_roundtrip() {
        let mut key = [0u8; 32];
        OsRng.fill_bytes(&mut key);
        let key_b64 = B64.encode(key);

        let plaintext = "PAE zero-knowledge test payload";
        let (ct, nonce) = encrypt(plaintext, &key_b64).unwrap();
        let result = decrypt(&ct, &nonce, &key_b64).unwrap();

        assert_eq!(result, plaintext);
    }

    #[test]
    fn test_invalid_key_base64_rejected() {
        let result = encrypt("hello", "not-valid-base64!!!");
        assert!(result.is_err());
    }

    #[test]
    fn test_wrong_key_decryption_fails() {
        let mut key1 = [0u8; 32];
        let mut key2 = [0u8; 32];
        OsRng.fill_bytes(&mut key1);
        OsRng.fill_bytes(&mut key2);

        let (ct, nonce) = encrypt("secret", &B64.encode(key1)).unwrap();
        let result = decrypt(&ct, &nonce, &B64.encode(key2));
        assert!(result.is_err());
    }

    #[test]
    fn test_invalid_nonce_length_rejected() {
        let mut key = [0u8; 32];
        OsRng.fill_bytes(&mut key);
        let key_b64 = B64.encode(key);
        let bad_nonce = B64.encode([0u8; 8]); // 8 bytes instead of 12
        let result = decrypt(&B64.encode(b"ciphertext"), &bad_nonce, &key_b64);
        assert!(matches!(result.unwrap_err(), CryptoError::InvalidNonceLength(8)));
    }
}
