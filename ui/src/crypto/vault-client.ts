// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * PAE client-side vault.
 *
 * This module is the ONLY place key derivation happens. The passphrase is
 * hashed with Argon2id locally (vendored hash-wasm, no CDN, no network)
 * and only the derived base64 key is ever sent to the engine — a
 * passphrase never appears in any request body.
 *
 * The engine has NO passphrase-derivation endpoint (POST
 * /api/v1/crypto/derive-key was deleted 2026-10-05). /encrypt and
 * /decrypt remain caller-supplied key+data oracles BY DESIGN for
 * local-first deployment; see docs/THREAT_MODEL.md.
 *
 * DEPLOY NOTE: ui/vendor must be copied next to the compiled output
 * (dist/vendor). The import below resolves at runtime to
 * dist/vendor/hash-wasm/hash-wasm.esm.js.
 */

import { argon2id } from '../../vendor/hash-wasm/hash-wasm.esm.js';

/** KDF parameters as served by GET /api/v1/crypto/kdf-params. */
export interface KdfParams {
  algorithm: 'argon2id';
  version: 19;
  memory_kib: number;
  iterations: number;
  parallelism: number;
  output_bytes: number;
  /** Base64 (standard alphabet) of the per-database 16-byte salt. */
  salt_b64: string;
}

export interface EncryptResult {
  ciphertext_b64: string;
  nonce_b64: string;
}

function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) {
    out[i] = bin.charCodeAt(i);
  }
  return out;
}

function bytesToB64(bytes: Uint8Array): string {
  let bin = '';
  for (const b of bytes) {
    bin += String.fromCharCode(b);
  }
  return btoa(bin);
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`POST ${url} failed: ${res.status} ${text}`);
  }
  return (await res.json()) as T;
}

/**
 * Fetch the Argon2id parameters (and per-database salt) from the engine.
 * Validates the shape; rejects anything that is not the expected spec.
 */
export async function getKdfParams(engineUrl: string): Promise<KdfParams> {
  const res = await fetch(`${engineUrl}/api/v1/crypto/kdf-params`);
  if (!res.ok) {
    throw new Error(`GET kdf-params failed: ${res.status}`);
  }
  const p = (await res.json()) as Partial<KdfParams>;
  if (
    p.algorithm !== 'argon2id' ||
    p.version !== 19 ||
    typeof p.memory_kib !== 'number' ||
    typeof p.iterations !== 'number' ||
    typeof p.parallelism !== 'number' ||
    typeof p.output_bytes !== 'number' ||
    typeof p.salt_b64 !== 'string'
  ) {
    throw new Error('kdf-params response failed shape validation');
  }
  return p as KdfParams;
}

/**
 * Derive the 32-byte data key from a passphrase, CLIENT-SIDE, using the
 * server-advertised parameters. Returns base64 of the raw 32 bytes —
 * the exact format POST /encrypt expects as `key_b64`.
 *
 * The passphrase never leaves this function.
 */
export async function deriveKeyB64(
  passphrase: string,
  params: KdfParams
): Promise<string> {
  if (!passphrase) {
    throw new Error('passphrase must not be empty');
  }
  const raw = await argon2id({
    password: new TextEncoder().encode(passphrase),
    salt: b64ToBytes(params.salt_b64),
    iterations: params.iterations,
    parallelism: params.parallelism,
    memorySize: params.memory_kib,
    hashLength: params.output_bytes,
    outputType: 'binary',
  });
  return bytesToB64(raw);
}

/** Encrypt plaintext via the engine (key+data oracle, local-first). */
export async function encrypt(
  engineUrl: string,
  keyB64: string,
  plaintext: string
): Promise<EncryptResult> {
  return postJson<EncryptResult>(`${engineUrl}/api/v1/crypto/encrypt`, {
    plaintext,
    key_b64: keyB64,
  });
}

/** Decrypt ciphertext via the engine (key+data oracle, local-first). */
export async function decrypt(
  engineUrl: string,
  keyB64: string,
  ciphertext_b64: string,
  nonce_b64: string
): Promise<string> {
  const out = await postJson<{ plaintext: string }>(
    `${engineUrl}/api/v1/crypto/decrypt`,
    { ciphertext_b64, nonce_b64, key_b64: keyB64 }
  );
  return out.plaintext;
}

// ============================================================================
// DEK/KEK hierarchy (2026-10-05).
//
// GRANULARITY DECISION: per-VAULT DEK (one DEK per database), NOT per-record.
// Justification:
//
// 1. One KEK per vault, by construction. The KDF salt is per-database (meta
//    table), so one passphrase derives exactly one KEK for the whole vault.
//    The vault is the natural trust boundary: one user's data under one
//    passphrase.
// 2. The flows do not support per-record DEKs cleanly. The engine schema
//    stores records as opaque ciphertext+nonce pairs (accounts name/broker,
//    portfolios name, holdings symbol/payload) and the holdings/portfolio
//    APIs accept only pre-encrypted blobs; the UI has no record-creation
//    flows yet. Per-record envelopes would force DB migrations on three
//    tables plus API field changes, with NO threat-model win today: the
//    server sees only ciphertext either way, and client memory holds the
//    working key either way.
// 3. The envelope format is versioned and carries a `kid` (key id). The
//    per-record extension stores the SAME JSON alongside each record with
//    `kid` = the record id — no format change, no client API change beyond
//    scope selection.
// 4. Rotation economics: KEK rotation re-wraps only the envelope (no
//    re-encryption); DEK rotation re-encrypts records once. Per-record
//    DEKs would multiply that work without moving the adversary.
//
// Zero-knowledge is the hard constraint: the passphrase never leaves
// deriveKeyB64, the KEK never leaves this module's memory, and the server
// stores the envelope as an opaque string (it IS AES-GCM ciphertext).
// All wrap/unwrap and record encrypt/decrypt below use WebCrypto
// (SubtleCrypto) — no new dependencies, works in browsers and node 24.
// ============================================================================

/**
 * WebCrypto algorithm identifier for AES-GCM. NOTE: this is 'AES-GCM' —
 * the key length (256-bit) comes from the imported key material, not the
 * name. The envelope's `alg` field carries the human descriptor
 * 'AES-256-GCM' for interop documentation.
 */
const WRAP_ALG = 'AES-GCM';
/** Envelope format version for DEK-wrapped-by-KEK vaults. */
const DEK_ENVELOPE_VERSION = 2;
/** DEK and KEK length in bytes (256-bit keys). */
const KEY_BYTES = 32;
/** AES-GCM nonce length in bytes (96-bit). */
const NONCE_BYTES = 12;

/**
 * Versioned DEK envelope. Stored server-side as an opaque JSON string
 * (meta table `dek_envelope_v2`, via GET/POST /api/v1/crypto/dek-envelope).
 *
 * The server can read every field and learn nothing: `wrapped_dek_b64`
 * is the 32-byte DEK encrypted under the user's KEK.
 *
 * INTEROP SPEC (any implementation can produce/consume this envelope):
 * - wrap   = AES-256-GCM encrypt of the raw 32-byte DEK under the raw
 *            32-byte KEK, random 12-byte nonce, empty AAD. Ciphertext
 *            INCLUDES the 16-byte GCM tag (encrypt-then-MAC construction
 *            as emitted by WebCrypto / Rust aes-gcm / Python cryptography).
 * - unwrap = AES-256-GCM decrypt; authentication failure (wrong KEK,
 *            tampered wrapped_dek or nonce) MUST be treated as fatal.
 * - base64 = standard alphabet WITH padding (btoa / RFC 4648 §4), same
 *            alphabet the engine uses on its crypto endpoints.
 * - v = 2. Consumers MUST reject any other version.
 */
export interface DekEnvelope {
  /** Format version. 2 = DEK wrapped by KEK. */
  v: 2;
  /** Cipher descriptor. Only 'AES-256-GCM' is defined. */
  alg: 'AES-256-GCM';
  /**
   * Key id / scope. 'vault' for the per-vault DEK. Per-record DEKs (future)
   * reuse this exact envelope shape with `kid` = the record id.
   */
  kid: string;
  /** Base64 of the KEK-encrypted 32-byte DEK (ciphertext + 16-byte tag). */
  wrapped_dek_b64: string;
  /** Base64 of the 12-byte wrap nonce. */
  dek_nonce_b64: string;
}

/** An unlocked vault: the working data key plus its envelope version. */
export interface VaultSession {
  /**
   * Working data key (32 bytes). v2 = the unwrapped vault DEK;
   * v1 = the raw KEK (legacy vaults whose records were encrypted
   * directly with the KEK-derived key — read support, see unlockVault).
   */
  dek: Uint8Array;
  /** Envelope version in use: 2 = DEK/KEK, 1 = direct-KEK legacy. */
  version: 1 | 2;
}

function randomBytes(n: number): Uint8Array {
  return crypto.getRandomValues(new Uint8Array(n));
}

async function importAesGcmKey(raw: Uint8Array): Promise<CryptoKey> {
  if (raw.length !== KEY_BYTES) {
    throw new Error(`key must be ${KEY_BYTES} bytes, got ${raw.length}`);
  }
  // slice() detaches from any larger backing buffer the view may alias.
  return crypto.subtle.importKey('raw', raw.slice().buffer, WRAP_ALG, false, [
    'encrypt',
    'decrypt',
  ]);
}

/**
 * Validate an unknown value as a v2 DEK envelope. The engine stores the
 * envelope opaquely, so the CLIENT is the validation point — validate
 * before every unwrap, never trust the stored bytes.
 */
export function validateDekEnvelope(value: unknown): asserts value is DekEnvelope {
  const e = value as Partial<DekEnvelope>;
  if (typeof e !== 'object' || e === null) {
    throw new Error('DEK envelope must be an object');
  }
  if (e.v !== DEK_ENVELOPE_VERSION) {
    throw new Error(
      `unsupported DEK envelope version: ${String(e.v)} (expected ${DEK_ENVELOPE_VERSION})`
    );
  }
  if (e.alg !== 'AES-256-GCM') {
    throw new Error(`unsupported DEK envelope alg: ${String(e.alg)}`);
  }
  if (typeof e.kid !== 'string' || e.kid.length === 0) {
    throw new Error('DEK envelope kid must be a non-empty string');
  }
  if (typeof e.wrapped_dek_b64 !== 'string' || e.wrapped_dek_b64.length === 0) {
    throw new Error('DEK envelope wrapped_dek_b64 must be a non-empty string');
  }
  if (typeof e.dek_nonce_b64 !== 'string' || e.dek_nonce_b64.length === 0) {
    throw new Error('DEK envelope dek_nonce_b64 must be a non-empty string');
  }
}

/** Parse and validate a stored envelope JSON string. */
export function parseDekEnvelope(json: string): DekEnvelope {
  let raw: unknown;
  try {
    raw = JSON.parse(json);
  } catch {
    throw new Error('DEK envelope is not valid JSON');
  }
  validateDekEnvelope(raw);
  return raw;
}

/**
 * Generate a fresh random 32-byte DEK client-side. Called once per vault
 * at creation (and again on DEK rotation).
 */
export function generateDEK(): Uint8Array {
  return randomBytes(KEY_BYTES);
}

/**
 * Wrap a DEK with the KEK (AES-256-GCM, random 96-bit nonce), client-side.
 * Returns the versioned envelope for opaque server storage.
 */
export async function wrapDEK(dek: Uint8Array, kek: Uint8Array): Promise<DekEnvelope> {
  if (dek.length !== KEY_BYTES) {
    throw new Error(`DEK must be ${KEY_BYTES} bytes, got ${dek.length}`);
  }
  const kekKey = await importAesGcmKey(kek);
  const nonce = randomBytes(NONCE_BYTES);
  const wrapped = new Uint8Array(
    await crypto.subtle.encrypt({ name: WRAP_ALG, iv: nonce.slice().buffer }, kekKey, dek.slice().buffer)
  );
  return {
    v: DEK_ENVELOPE_VERSION,
    alg: 'AES-256-GCM',
    kid: 'vault',
    wrapped_dek_b64: bytesToB64(wrapped),
    dek_nonce_b64: bytesToB64(nonce),
  };
}

/**
 * Unwrap a DEK envelope with the KEK, client-side. Throws on the wrong
 * KEK or any tampering (AES-GCM authentication failure) — callers must
 * treat this as fatal, never fall back to another key.
 */
export async function unwrapDEK(envelope: DekEnvelope, kek: Uint8Array): Promise<Uint8Array> {
  validateDekEnvelope(envelope);
  const kekKey = await importAesGcmKey(kek);
  const nonce = b64ToBytes(envelope.dek_nonce_b64);
  if (nonce.length !== NONCE_BYTES) {
    throw new Error(`DEK wrap nonce must be ${NONCE_BYTES} bytes, got ${nonce.length}`);
  }
  const wrapped = b64ToBytes(envelope.wrapped_dek_b64);
  let dek: ArrayBuffer;
  try {
    dek = await crypto.subtle.decrypt(
      { name: WRAP_ALG, iv: nonce.slice().buffer },
      kekKey,
      wrapped.slice().buffer
    );
  } catch {
    // AES-GCM auth failed: wrong KEK or tampered envelope. The DEK is
    // unrecoverable from here by design — surface, don't retry.
    throw new Error('DEK unwrap failed: wrong KEK or tampered envelope');
  }
  const out = new Uint8Array(dek);
  if (out.length !== KEY_BYTES) {
    throw new Error(`unwrapped DEK must be ${KEY_BYTES} bytes, got ${out.length}`);
  }
  return out;
}

/**
 * Encrypt a record field with the vault DEK (AES-256-GCM, random nonce),
 * fully client-side. Output shape matches the engine's /encrypt response
 * and the storage layer's (ciphertext_b64, nonce_b64) columns, so records
 * encrypted here can be persisted through the existing holdings/portfolio
 * APIs unchanged.
 */
export async function encryptWithDEK(dek: Uint8Array, plaintext: string): Promise<EncryptResult> {
  const key = await importAesGcmKey(dek);
  const nonce = randomBytes(NONCE_BYTES);
  const ct = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: WRAP_ALG, iv: nonce.slice().buffer },
      key,
      new TextEncoder().encode(plaintext)
    )
  );
  return { ciphertext_b64: bytesToB64(ct), nonce_b64: bytesToB64(nonce) };
}

/**
 * Decrypt a record field with the vault DEK, fully client-side. Throws
 * on the wrong DEK or tampered ciphertext (AES-GCM authentication).
 */
export async function decryptWithDEK(
  dek: Uint8Array,
  ciphertext_b64: string,
  nonce_b64: string
): Promise<string> {
  const key = await importAesGcmKey(dek);
  const nonce = b64ToBytes(nonce_b64);
  if (nonce.length !== NONCE_BYTES) {
    throw new Error(`nonce must be ${NONCE_BYTES} bytes, got ${nonce.length}`);
  }
  const ct = b64ToBytes(ciphertext_b64);
  let pt: ArrayBuffer;
  try {
    pt = await crypto.subtle.decrypt(
      { name: WRAP_ALG, iv: nonce.slice().buffer },
      key,
      ct.slice().buffer
    );
  } catch {
    throw new Error('DEK decrypt failed: wrong DEK or tampered ciphertext');
  }
  return new TextDecoder().decode(pt);
}

interface DekEnvelopeDoc {
  envelope: string | null;
}

async function getStoredEnvelope(engineUrl: string): Promise<string | null> {
  const res = await fetch(`${engineUrl}/api/v1/crypto/dek-envelope`);
  if (!res.ok) {
    throw new Error(`GET dek-envelope failed: ${res.status}`);
  }
  const doc = (await res.json()) as Partial<DekEnvelopeDoc>;
  if (doc.envelope !== null && doc.envelope !== undefined && typeof doc.envelope !== 'string') {
    throw new Error('dek-envelope response failed shape validation');
  }
  return doc.envelope ?? null;
}

async function storeEnvelope(engineUrl: string, envelope: DekEnvelope): Promise<void> {
  // Sent as a JSON string: the engine stores it verbatim and opaquely.
  await postJson(`${engineUrl}/api/v1/crypto/dek-envelope`, {
    envelope: JSON.stringify(envelope),
  });
}

/**
 * Create a new vault: derive the KEK from the passphrase (client-side),
 * generate a fresh DEK, wrap it with the KEK, and store the envelope
 * opaquely on the engine. Returns the unlocked session (DEK in memory).
 *
 * The passphrase and KEK never leave this function; the server sees only
 * the wrapped-DEK envelope (ciphertext).
 */
export async function createVault(engineUrl: string, passphrase: string): Promise<VaultSession> {
  const params = await getKdfParams(engineUrl);
  const kek = b64ToBytes(await deriveKeyB64(passphrase, params));
  const dek = generateDEK();
  const envelope = await wrapDEK(dek, kek);
  await storeEnvelope(engineUrl, envelope);
  return { dek, version: 2 };
}

/**
 * Unlock a vault: derive the KEK from the passphrase (client-side), fetch
 * the stored envelope, and unwrap the DEK. If no envelope is stored the
 * vault is v1 (records encrypted directly with the KEK-derived key — the
 * pre-DEK format) and the KEK itself is the working data key.
 *
 * v1 read support is a one-line branch here, so we keep it rather than
 * forcing a pre-release break: existing vaults keep opening, and the next
 * write path (createVault / a future rotateKEK) produces v2.
 */
export async function unlockVault(engineUrl: string, passphrase: string): Promise<VaultSession> {
  const params = await getKdfParams(engineUrl);
  const kek = b64ToBytes(await deriveKeyB64(passphrase, params));
  const stored = await getStoredEnvelope(engineUrl);
  if (stored === null) {
    return { dek: kek, version: 1 };
  }
  const dek = await unwrapDEK(parseDekEnvelope(stored), kek);
  return { dek, version: 2 };
}
