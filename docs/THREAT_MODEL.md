# PAE Threat Model

> **Implementation status (2026-10-05).** The client-side crypto path is now
> implemented: the UI derives keys locally (`ui/src/crypto/vault-client.ts`,
> vendored hash-wasm Argon2id) and the server-side passphrase endpoint
> (`POST /api/v1/crypto/derive-key`) has been deleted. Rows below marked
> **[NOT IMPLEMENTED]** describe design intent, not code — do not present
> them as shipped.

## Principles

1. **Zero-knowledge**: Server stores only ciphertext. Operator cannot read user data.
2. **Zero-trust**: Every component assumes breach. No implicit trust between services.
3. **User-held keys**: Master key derived from user passphrase **client-side**.
   Server never sees passphrase or derived key.

## Attack Vectors

### Server Compromise
- **Threat**: Attacker gains full access to server, database, and filesystem.
- **Mitigation**: All stored data is AES-256-GCM ciphertext (random 96-bit
  nonce per encryption). Record keys are derived client-side via Argon2id
  (600K iterations, 64 MiB, 4 lanes, v19, 32-byte output) against the
  per-database salt served by `GET /api/v1/crypto/kdf-params`. The server
  holds no passphrase-derivation code path — there is nothing to coerce or
  dump for user passphrases. Attacker gets ciphertext only.
- **Residual**: `/encrypt` and `/decrypt` accept caller-supplied key+data
  (see Residual risks below).

### Client Compromise
- **Threat**: Malware on user's device captures decrypted data or passphrase.
- **Mitigation**: Standard endpoint security applies. The derived key exists
  in UI memory only for the duration of a derivation/encrypt/decrypt call.
  No persistent key storage on disk.
- **[NOT IMPLEMENTED]**: "Session timeout clears memory" — there is no
  session layer; keys are passed per request. Do not claim session-scoped
  key hygiene.

### Plaid/Broker Token Theft
- **Threat**: Attacker obtains stored Plaid or brokerage API tokens.
- **Mitigation**: Tokens are encrypted with the user's key before storage;
  the storage path accepts only pre-encrypted blobs. Attacker needs both
  the ciphertext AND the user's passphrase-derived key.

### Man-in-the-Middle
- **Threat**: Attacker intercepts communication between client and server.
- **Mitigation**: **[NOT IMPLEMENTED in the engine]** — the engine serves
  plain HTTP; TLS 1.3, HSTS, and certificate pinning are deployment
  (reverse-proxy) responsibilities, not code. Any hosted deployment MUST
  terminate TLS at a reverse proxy; localhost deployment is loopback-only
  by convention.

### Brute Force on Passphrase
- **Threat**: Attacker attempts to brute-force the user's passphrase from
  stored material.
- **Mitigation**: Argon2id with 600K iterations, 64 MiB memory, 4 lanes.
  The per-database salt is stored in the SQLite `meta` table and served by
  `GET /api/v1/crypto/kdf-params` — it is NOT secret (only stable), so it
  adds no brute-force resistance beyond forcing per-database attacks.
  Resistance comes from the memory-hard parameters plus passphrase entropy.

## Encryption Specifications

| Property | Value |
|----------|-------|
| Symmetric cipher | AES-256-GCM |
| Key derivation | Argon2id, client-side only (600K iterations, 64 MiB, 4 lanes, v19) |
| KDF salt | Per-database 16-byte salt, SQLite `meta` table, served via `GET /api/v1/crypto/kdf-params` (not secret) |
| Client KDF | `ui/src/crypto/vault-client.ts`, vendored hash-wasm (no CDN) |
| Server-side derivation | **REMOVED 2026-10-05** — `POST /api/v1/crypto/derive-key` deleted |
| Key length | 256 bits |
| Nonce | 96 bits, random per encryption |
| DEK wrapping | **[NOT IMPLEMENTED]** — records are encrypted directly with the caller-supplied key. No per-record DEK/KEK hierarchy, no wrapping, no rotation exists. |
| Key recovery | **[NOT IMPLEMENTED]** — Shamir 3-of-5 was spec-only. Lost passphrase = inaccessible data. |
| No server-side recovery | By design. Lost passphrase = inaccessible data. |
| CORS | Restrictive: origin from `PAE_CORS_ORIGIN` (default `http://localhost:3000`), methods GET/POST, `Content-Type` header only |
| Unknown request fields | Rejected with 400 (`deny_unknown_fields` on crypto request bodies — a `passphrase` field can never be smuggled in) |

## Residual risks (read before any hosted deployment)

1. **`/encrypt` and `/decrypt` are key+data oracles BY DESIGN.** In the
   local-first deployment (engine on the user's own machine, UI served
   locally) this is the intended division of labor: the browser derives
   the key, the engine does AES-GCM as a compute service, and no trust
   boundary is crossed. In ANY hosted or multi-user deployment these two
   endpoints let anyone who can reach the server encrypt/decrypt arbitrary
   data with arbitrary keys — they MUST be disabled (or removed from the
   router), or encryption must move fully client-side (WebCrypto), before
   the engine is exposed beyond loopback.
2. **No authentication on any endpoint.** CORS narrows which web origins
   can call the API from a browser, but it is not access control —
   non-browser callers ignore it. Localhost deployment is the current
   access-control story; a hosted deployment needs real auth first.
3. **No TLS in the engine.** See Man-in-the-Middle above.
4. **Single-key model.** There is no DEK/KEK separation: one passphrase-
   derived key protects everything. Key rotation means re-encrypting all
   records under a new key — no tooling for that exists yet.
