use aes_gcm::{
    aead::{Aead, KeyInit, OsRng},
    Aes256Gcm, Nonce,
};
use argon2::{Argon2, PasswordHasher, password_hash::SaltString};
use base64::{Engine as _, engine::general_purpose::STANDARD as B64};
use rand::RngCore;
use thiserror::Error;

const ARGON2_ITERATIONS: u32 = 600_000;

/// Errors that can occur during cryptographic operations.
#[derive(Debug, Error)]
pub enum CryptoError {
    #[error("Invalid base64 encoding: {context}")]
    InvalidBase64 { context: String },

    #[error("Invalid key length: expected 32 bytes")]
    InvalidKeyLength,

    #[error("Invalid salt: {0}")]
    InvalidSalt(String),

    #[error("Key derivation failed: {0}")]
    DerivationFailed(String),

    #[error("Encryption failed: {0}")]
    EncryptionFailed(String),

    #[error("Decryption failed: authentication or data corrupted")]
    DecryptionFailed,

    #[error("Invalid nonce length: expected 12 bytes, got {0}")]
    InvalidNonceLength(usize),

    #[error("Invalid UTF-8 in decrypted plaintext")]
    InvalidUtf8,

    #[error("Empty passphrase is not allowed")]
    EmptyPassphrase,

    #[error("Invalid Argon2 parameters: {0}")]
    InvalidParams(String),
}

/// Derive a 256-bit key from a passphrase using Argon2id.
///
/// Returns `(key_hash_b64, salt_b64)`.
/// Uses 600K iterations (6x Google's standard) per the PAE/mykey security spec.
///
/// # Errors
///
/// Returns `CryptoError::EmptyPassphrase` if the passphrase is empty.
/// Returns `CryptoError::InvalidSalt` if `existing_salt` is not valid base64.
/// Returns `CryptoError::DerivationFailed` if Argon2 hashing fails.
pub fn derive_key(passphrase: &str, existing_salt: Option<&str>) -> Result<(String, String), CryptoError> {
    if passphrase.is_empty() {
        return Err(CryptoError::EmptyPassphrase);
    }

    let salt = match existing_salt {
        Some(s) => SaltString::from_b64(s)
            .map_err(|e| CryptoError::InvalidSalt(e.to_string()))?,
        None => SaltString::generate(&mut OsRng),
    };

    let argon2 = Argon2::new(
        argon2::Algorithm::Argon2id,
        argon2::Version::V0x13,
        argon2::Params::new(65536, ARGON2_ITERATIONS, 4, Some(32))
            .map_err(|e| CryptoError::InvalidParams(e.to_string()))?,
    );

    let hash = argon2
        .hash_password(passphrase.as_bytes(), &salt)
        .map_err(|e| CryptoError::DerivationFailed(e.to_string()))?;

    Ok((hash.to_string(), salt.to_string()))
}

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
    use base64::Engine as _;

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
    fn test_derive_key_deterministic_with_salt() {
        let passphrase = "test-passphrase-for-pae";
        let (_, salt) = derive_key(passphrase, None).unwrap();
        let (hash1, _) = derive_key(passphrase, Some(&salt)).unwrap();
        let (hash2, _) = derive_key(passphrase, Some(&salt)).unwrap();

        assert_eq!(hash1, hash2);
    }

    #[test]
    fn test_empty_passphrase_rejected() {
        let result = derive_key("", None);
        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), CryptoError::EmptyPassphrase));
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
