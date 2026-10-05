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
