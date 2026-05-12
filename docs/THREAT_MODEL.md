# PAE Threat Model

## Principles

1. **Zero-knowledge**: Server stores only ciphertext. Operator cannot read user data.
2. **Zero-trust**: Every component assumes breach. No implicit trust between services.
3. **User-held keys**: Master key derived from user passphrase. Server never sees passphrase or derived key.

## Attack Vectors

### Server Compromise
- **Threat**: Attacker gains full access to server, database, and filesystem.
- **Mitigation**: All stored data is AES-256-GCM encrypted with per-record DEKs. DEKs are wrapped with user's KEK. KEK is derived client-side via Argon2id and never transmitted. Attacker gets ciphertext only.
- **Residual risk**: None for data confidentiality. Availability may be affected.

### Client Compromise
- **Threat**: Malware on user's device captures decrypted data or passphrase.
- **Mitigation**: Standard endpoint security applies. KEK exists in memory only during active sessions. Session timeout clears memory. No persistent key storage on disk.
- **Residual risk**: Client compromise is outside PAE's threat model boundary. Users should maintain standard device security.

### Plaid/Broker Token Theft
- **Threat**: Attacker obtains stored Plaid or brokerage API tokens.
- **Mitigation**: Tokens are encrypted with user's KEK before storage. Attacker needs both the encrypted token AND the user's passphrase to use them.
- **Residual risk**: If both token and passphrase are compromised, attacker could access brokerage data.

### Man-in-the-Middle
- **Threat**: Attacker intercepts communication between client and server.
- **Mitigation**: TLS 1.3 mandatory. HSTS enabled. Certificate pinning for API calls. No mixed content.
- **Residual risk**: Minimal with proper TLS configuration.

### Brute Force on Passphrase
- **Threat**: Attacker attempts to brute-force the user's passphrase from the stored key hash.
- **Mitigation**: Argon2id with 600K iterations, 64MB memory, 4 threads. Cost per attempt is deliberately high. At current hardware rates, brute-forcing a 12-character passphrase is computationally infeasible.
- **Residual risk**: Weak passphrases remain vulnerable. Onboarding enforces minimum passphrase complexity.

## Encryption Specifications

| Property | Value |
|----------|-------|
| Symmetric cipher | AES-256-GCM |
| Key derivation | Argon2id (600K iterations, 64MB, 4 threads) |
| Key length | 256 bits |
| Nonce | 96 bits, random per encryption |
| DEK wrapping | Each record encrypted with unique DEK, DEKs wrapped with user KEK |
| Key recovery | Optional Shamir's Secret Sharing (3-of-5 shards) |
| No server-side recovery | By design. Lost passphrase = inaccessible data. |
