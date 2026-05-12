use aes_gcm::{
    aead::{Aead, KeyInit, OsRng},
    Aes256Gcm, Nonce,
};
use argon2::{Argon2, PasswordHasher, password_hash::SaltString};
use base64::{Engine as _, engine::general_purpose::STANDARD as B64};
use rand::RngCore;

const ARGON2_ITERATIONS: u32 = 600_000;

/// Derive a 256-bit key from a passphrase using Argon2id.
/// Returns (key_hash_b64, salt_b64).
/// Uses 600K iterations (6x Google's standard) per the PAE/mykey security spec.
pub fn derive_key(passphrase: &str, existing_salt: Option<&str>) -> (String, String) {
    let salt = match existing_salt {
        Some(s) => SaltString::from_b64(s).expect("invalid salt"),
        None => SaltString::generate(&mut OsRng),
    };

    let argon2 = Argon2::new(
        argon2::Algorithm::Argon2id,
        argon2::Version::V0x13,
        argon2::Params::new(65536, ARGON2_ITERATIONS, 4, Some(32))
            .expect("invalid argon2 params"),
    );

    let hash = argon2
        .hash_password(passphrase.as_bytes(), &salt)
        .expect("hashing failed");

    (hash.to_string(), salt.to_string())
}

/// Encrypt plaintext with AES-256-GCM.
/// key_b64: base64-encoded 32-byte key.
/// Returns (ciphertext_b64, nonce_b64).
pub fn encrypt(plaintext: &str, key_b64: &str) -> (String, String) {
    let key_bytes = B64.decode(key_b64).expect("invalid key base64");
    let cipher = Aes256Gcm::new_from_slice(&key_bytes).expect("invalid key length");

    let mut nonce_bytes = [0u8; 12];
    OsRng.fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);

    let ciphertext = cipher
        .encrypt(nonce, plaintext.as_bytes())
        .expect("encryption failed");

    (B64.encode(&ciphertext), B64.encode(nonce_bytes))
}

/// Decrypt ciphertext with AES-256-GCM.
/// Returns plaintext string.
pub fn decrypt(ciphertext_b64: &str, nonce_b64: &str, key_b64: &str) -> String {
    let key_bytes = B64.decode(key_b64).expect("invalid key base64");
    let ciphertext = B64.decode(ciphertext_b64).expect("invalid ciphertext base64");
    let nonce_bytes = B64.decode(nonce_b64).expect("invalid nonce base64");

    let cipher = Aes256Gcm::new_from_slice(&key_bytes).expect("invalid key length");
    let nonce = Nonce::from_slice(&nonce_bytes);

    let plaintext = cipher
        .decrypt(nonce, ciphertext.as_ref())
        .expect("decryption failed");

    String::from_utf8(plaintext).expect("invalid utf8")
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
        let (ct, nonce) = encrypt(plaintext, &key_b64);
        let result = decrypt(&ct, &nonce, &key_b64);

        assert_eq!(result, plaintext);
    }

    #[test]
    fn test_derive_key_deterministic_with_salt() {
        let passphrase = "test-passphrase-for-pae";
        let (_, salt) = derive_key(passphrase, None);
        let (hash1, _) = derive_key(passphrase, Some(&salt));
        let (hash2, _) = derive_key(passphrase, Some(&salt));

        assert_eq!(hash1, hash2);
    }
}
