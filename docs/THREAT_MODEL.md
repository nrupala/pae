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
  (3 iterations, 64 MiB, 4 lanes, v19, 32-byte output — the RFC 9106
  first-recommended profile) against the
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

### Broker Credential Theft — NOT APPLICABLE (no broker connections)
- PAE has **no live brokerage integrations** (no Plaid, no broker APIs, no
  stored broker credentials). Holdings enter only as files the user
  provides (CSV, including IBKR Flex export files) or manual entry, so
  there are no third-party tokens to steal. If a live integration is ever
  added, this section must be rewritten before it ships: credentials would
  be encrypted with the user's key before storage, and the storage path
  accepts only pre-encrypted blobs.

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
- **Mitigation**: Argon2id with 3 iterations, 64 MiB memory, 4 lanes
  (RFC 9106 first-recommended profile).
  The per-database salt is stored in the SQLite `meta` table and served by
  `GET /api/v1/crypto/kdf-params` — it is NOT secret (only stable), so it
  adds no brute-force resistance beyond forcing per-database attacks.
  Resistance comes from the memory-hard parameters plus passphrase entropy.

## Encryption Specifications

| Property | Value |
|----------|-------|
| Symmetric cipher | AES-256-GCM |
| Key derivation | Argon2id, client-side only (3 iterations, 64 MiB, 4 lanes, v19 — RFC 9106 first-recommended profile) |
| KDF salt | Per-database 16-byte salt, SQLite `meta` table, served via `GET /api/v1/crypto/kdf-params` (not secret) |
| Client KDF | `ui/src/crypto/vault-client.ts`, vendored hash-wasm (no CDN) |
| Server-side derivation | **REMOVED 2026-10-05** — `POST /api/v1/crypto/derive-key` deleted |
| Key length | 256 bits |
| Nonce | 96 bits, random per encryption |
| DEK wrapping | **Implemented (v2 envelope, 2026-10-05).** Per-vault DEK (random 32-byte) wrapped with the user's KEK via AES-256-GCM, all client-side (`ui/src/crypto/vault-client.ts`: `wrapDEK`/`unwrapDEK`); the versioned envelope (`{v:2, alg, kid, wrapped_dek_b64, dek_nonce_b64}`) is stored opaquely in the engine `meta` table (`dek_envelope_v2`, GET/POST `/api/v1/crypto/dek-envelope`). v1 vaults (records encrypted directly with the KEK-derived key) remain readable via `unlockVault`. Granularity rationale (per-vault, not per-record) is documented in `vault-client.ts`. Replacement of a stored envelope requires the per-install agent token (Phase 3C): the first write is open for vault creation, but an unauthenticated overwrite - which would permanently brick every encrypted record - is refused with 409 and the stored envelope left untouched. |
| Key recovery | **[NOT IMPLEMENTED]** — Shamir 3-of-5 was spec-only. Lost passphrase = inaccessible data. |
| No server-side recovery | By design. Lost passphrase = inaccessible data. |
| CORS | Restrictive: origin from `PAE_CORS_ORIGIN` (default `http://localhost:3000`), methods GET/POST, `Content-Type` header only |
| Unknown request fields | Rejected with 422 (`deny_unknown_fields` on crypto request bodies — a `passphrase` field can never be smuggled in; axum maps serde failures to 422) |

## Residual risks (read before any hosted deployment)

1. **`/encrypt` and `/decrypt` are caller-supplied-key helpers BY
   DESIGN.** In the local-first deployment (engine on the user's own
   machine, UI served locally) this is the intended division of labor:
   the browser derives the key, the engine does AES-GCM as a compute
   service, and no trust boundary is crossed. Since Phase 3C both
   endpoints additionally require the per-install agent token
   (`Authorization: Bearer`), so they are no longer open to anything
   that can reach the port — but the token is a single bearer
   credential for the whole install, not per-user auth. In ANY hosted
   or multi-user deployment these two endpoints still let anyone
   holding the token encrypt/decrypt arbitrary data with arbitrary
   keys — they MUST be disabled (or removed from the router), or
   encryption must move fully client-side (WebCrypto), before
   the engine is exposed beyond loopback.
2. **Most endpoints have no authentication.** Since Phase 3C the
   agent/A2A surfaces and the two crypto helpers above require the
   per-install agent token; everything else (portfolio data, version
   store, dashboard proxy) is unauthenticated, and analytics' data
   routes authenticate only when `PAE_API_KEY` is set. CORS narrows
   which web origins can call the API from a browser, but it is not
   access control — non-browser callers ignore it. Localhost deployment is the current
   access-control story; a hosted deployment needs real auth first.
3. **No TLS in the engine.** See Man-in-the-Middle above.
4. **Single-DEK model.** The vault uses one per-vault DEK wrapped by the KEK
   (per-vault, not per-record — the granularity justification is in
   `ui/src/crypto/vault-client.ts`). KEK rotation re-wraps only the envelope;
   DEK rotation still means re-encrypting all records under the new DEK — no
   rotation tooling exists yet.
