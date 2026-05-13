use aes_gcm::{
    aead::{Aead, KeyInit, OsRng},
    Aes256Gcm, Nonce,
};
use argon2::{Argon2, PasswordHasher, password_hash::SaltString};
use base64::{Engine as _, engine::general_purpose::STANDARD as B64};
use rand::RngCore;
use thiserror::Error;

const ARGON2_ITERATIONS: u32 = 600_000;
const AES_KEY_LEN: usize = 32;
const NONCE_LEN: usize = 12;

/// Errors that can occur during cryptographic operations.
#[derive(Debug, Error)]
pub enum CryptoError {
    #[error("Invalid salt: {0}")]
    InvalidSalt(String),
    #[error("Invalid Argon2 parameters: {0}")]
    InvalidParams(String),
    #[error("Password hashing failed: {0}")]
    HashingFailed(String),
    #[error("Invalid base64 input: {0}")]
    InvalidBase64(String),
    #[error("Invalid key length: expected {expected} bytes, got {actual}")]
    InvalidKeyLength { expected: usize, actual: usize },
    #[error("Invalid nonce length: expected {expected} bytes, got {actual}")]
    InvalidNonceLength { expected: usize, actual: usize },
    #[error("Encryption failed: {0}")]
    EncryptionFailed(String),
    #[error("Decryption failed: {0}")]
    DecryptionFailed(String),
    #[error("Invalid UTF-8 in decrypted content")]
    InvalidUtf8,
    #[error("Passphrase must not be empty")]
    EmptyPassphrase,
    #[error("Plaintext must not be empty")]
    EmptyPlaintext,
}

/// Derive a 256-bit key from a passphrase using Argon2id.
/// Returns (key_hash_b64, salt_b64).
/// Uses 600K iterations (6x Google's standard) per the PAE/mykey security spec.
///
/// # Errors
/// Returns `CryptoError` if the passphrase is empty, the salt is invalid,
/// or the hashing operation fails.
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
        .map_err(|e| CryptoError::HashingFailed(e.to_string()))?;

    Ok((hash.to_string(), salt.to_string()))
}

/// Encrypt plaintext with AES-256-GCM.
/// key_b64: base64-encoded 32-byte key.
/// Returns (ciphertext_b64, nonce_b64).
///
/// # Errors
/// Returns `CryptoError` if the key is invalid, the plaintext is empty,
/// or encryption fails.
pub fn encrypt(plaintext: &str, key_b64: &str) -> Result<(String, String), CryptoError> {
    if plaintext.is_empty() {
        return Err(CryptoError::EmptyPlaintext);
    }

    let key_bytes = B64.decode(key_b64)
        .map_err(|e| CryptoError::InvalidBase64(format!("key: {e}")))?;

    if key_bytes.len() != AES_KEY_LEN {
        return Err(CryptoError::InvalidKeyLength {
            expected: AES_KEY_LEN,
            actual: key_bytes.len(),
        });
    }

    let cipher = Aes256Gcm::new_from_slice(&key_bytes)
        .map_err(|e| CryptoError::InvalidKeyLength {
            expected: AES_KEY_LEN,
            actual: key_bytes.len(),
        })?;

    let mut nonce_bytes = [0u8; NONCE_LEN];
    OsRng.fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);

    let ciphertext = cipher
        .encrypt(nonce, plaintext.as_bytes())
        .map_err(|e| CryptoError::EncryptionFailed(e.to_string()))?;

    Ok((B64.encode(&ciphertext), B64.encode(nonce_bytes)))
}

/// Decrypt ciphertext with AES-256-GCM.
/// Returns plaintext string.
///
/// # Errors
/// Returns `CryptoError` if any base64 input is invalid, the key length
/// is wrong, decryption fails, or the result is not valid UTF-8.
pub fn decrypt(ciphertext_b64: &str, nonce_b64: &str, key_b64: &str) -> Result<String, CryptoError> {
    let key_bytes = B64.decode(key_b64)
        .map_err(|e| CryptoError::InvalidBase64(format!("key: {e}")))?;
    let ciphertext = B64.decode(ciphertext_b64)
        .map_err(|e| CryptoError::InvalidBase64(format!("ciphertext: {e}")))?;
    let nonce_bytes = B64.decode(nonce_b64)
        .map_err(|e| CryptoError::InvalidBase64(format!("nonce: {e}")))?;

    if key_bytes.len() != AES_KEY_LEN {
        return Err(CryptoError::InvalidKeyLength {
            expected: AES_KEY_LEN,
            actual: key_bytes.len(),
        });
    }

    if nonce_bytes.len() != NONCE_LEN {
        return Err(CryptoError::InvalidNonceLength {
            expected: NONCE_LEN,
            actual: nonce_bytes.len(),
        });
    }

    let cipher = Aes256Gcm::new_from_slice(&key_bytes)
        .map_err(|e| CryptoError::InvalidKeyLength {
            expected: AES_KEY_LEN,
            actual: key_bytes.len(),
        })?;
    let nonce = Nonce::from_slice(&nonce_bytes);

    let plaintext = cipher
        .decrypt(nonce, ciphertext.as_ref())
        .map_err(|e| CryptoError::DecryptionFailed(e.to_string()))?;

    String::from_utf8(plaintext).map_err(|_| CryptoError::InvalidUtf8)
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
    fn test_empty_plaintext_rejected() {
        let mut key = [0u8; 32];
        OsRng.fill_bytes(&mut key);
        let key_b64 = B64.encode(key);

        let result = encrypt("", &key_b64);
        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), CryptoError::EmptyPlaintext));
    }

    #[test]
    fn test_invalid_key_length_rejected() {
        let short_key = B64.encode([0u8; 16]);
        let result = encrypt("test", &short_key);
        assert!(result.is_err());
    }

    #[test]
    fn test_invalid_base64_rejected() {
        let result = encrypt("test", "not-valid-base64!!!");
        assert!(result.is_err());
    }

    #[test]
    fn test_decrypt_wrong_key_fails() {
        let mut key1 = [0u8; 32];
        let mut key2 = [0u8; 32];
        OsRng.fill_bytes(&mut key1);
        OsRng.fill_bytes(&mut key2);

        let (ct, nonce) = encrypt("secret", &B64.encode(key1)).unwrap();
        let result = decrypt(&ct, &nonce, &B64.encode(key2));
        assert!(result.is_err());
    }
}
