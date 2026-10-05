/**
 * PAE client-side Shamir's Secret Sharing (3-of-5) for KEK recovery.
 *
 * Split the 32-byte Key Encryption Key into 5 human-transcribable shares;
 * any 3 reconstruct it. Everything here runs CLIENT-SIDE — the KEK (and
 * the passphrase it derives from) never leaves the browser. See
 * `ui/src/crypto/vault-client.ts` for the derivation side.
 *
 * ---------------------------------------------------------------------------
 * LIBRARY DECISION (evaluated 2026-10-05, documented per workstream brief)
 * ---------------------------------------------------------------------------
 * Candidates checked against the npm registry (publish date / weekly
 * downloads / license):
 *   - `shamir-secret-sharing` 0.0.4 (privy-io) — published 2025-01-10,
 *     ~94k dl/wk, Apache-2.0, zero deps, audited by Cure53 + Zellic.
 *     API is split/combine over raw Uint8Array; opaque binary share format,
 *     and the audit explicitly EXCLUDES reconstruction-integrity — the
 *     caller must verify recovered output itself.
 *   - `secrets.js-grempe` 2.0.0 — last published 2019-09-08 (~48k dl/wk),
 *     hex-string API, unmaintained since 2019. Rejected: stale.
 *   - `shamir` 0.7.1 / `secrets.js` 0.1.8 — marginal activity. Rejected.
 *
 * Decision: hand-roll the GF(256) core in this file instead of vendoring.
 * Why this is the better call here:
 *   1. The requirements a library cannot supply dominate the work: a
 *      self-describing, human-transcribable share encoding (version tag +
 *      share index), and an embedded integrity checksum so corrupted
 *      shares fail LOUDLY. Those layers are ours either way.
 *   2. Shamir over GF(256) is finite-field interpolation (~80 lines), not
 *      a cipher — it is fully pinned by standard test vectors and is
 *      cross-checked in the test suite against the audited privy-io
 *      implementation's published field tables/algorithm as an independent
 *      oracle (same field, same polynomial x^8+x^4+x^3+x+1).
 *   3. One self-contained file keeps the diff disjoint from the DEK/KEK
 *      workstream, adds zero dependency/vendoring surface to the key-
 *      recovery path (the highest-trust code in the vault), and needs no
 *      async bundler-friendly wrapper.
 * A Rust crate + wasm was considered and rejected: it would add a wasm
 * build/packaging step to the UI for ~80 lines of field arithmetic with
 * no security gain (the audit-relevant properties live in OUR encoding
 * and checksum layers, not in the field math).
 *
 * ---------------------------------------------------------------------------
 * SHARE FORMAT (v1) — human-transcribable, self-describing
 * ---------------------------------------------------------------------------
 *   PAE-SSS1-T3-N5-X01-<HEX>
 *    |    |   |  |   |    +-- share payload: one GF(256) y-value per byte
 *    |    |   |  |   |        of (secret || checksum), uppercase hex
 *    |    |   |  |   +------- share index X (x-coordinate), decimal,
 *    |    |   |  |            zero-padded, 1..255 — printed on each share
 *    |    |   |  +----------- N: total shares issued (informational)
 *    |    |   +-------------- T: reconstruction threshold
 *    |    +------------------ format version tag
 *    +----------------------- issuer tag
 *
 * Example (3-of-5, 32-byte KEK -> 36-byte payload -> 72 hex chars):
 *   PAE-SSS1-T3-N5-X01-9F2A... (90 chars total)
 *
 * ---------------------------------------------------------------------------
 * TAMPER / CORRUPTION DETECTION
 * ---------------------------------------------------------------------------
 * Shamir's scheme is unauthenticated: any t shares interpolate to *some*
 * polynomial, so a corrupted share (bit flip, transcription typo, shares
 * from two different splits mixed together) would silently yield a WRONG
 * key. To fail loudly, the split payload is:
 *
 *     payload = secret || SHA256(secret)[0..4]      (4-byte checksum)
 *
 * Recovery re-computes SHA-256 over the recovered secret and compares the
 * first 4 bytes; mismatch throws. This catches random corruption and
 * cross-split mixing with probability 1 - 2^-32 per attempt.
 *
 * This is corruption detection, NOT a MAC: a malicious party who crafts
 * shares can forge a valid checksum. Key-recovery shares are handled by
 * the key owner, so the threat addressed is accident, not forgery.
 *
 * ---------------------------------------------------------------------------
 * SECURITY NOTES
 * ---------------------------------------------------------------------------
 * - Coefficients are drawn from crypto.getRandomValues (CSPRNG). The
 *   optional `rand` parameter on splitSecret exists ONLY for deterministic
 *   tests — never pass a weak RNG in production.
 * - With fewer than t shares, recovery is information-theoretically
 *   impossible; recoverSecret throws rather than returning garbage.
 * - The checksum covers the secret, so a wrong share SET is detected
 *   even when every individual share parses cleanly.
 *
 * ---------------------------------------------------------------------------
 * UI INTEGRATION POINT
 * ---------------------------------------------------------------------------
 * There is currently no vault setup/recovery view in ui/src/components
 * (checked 2026-10-05). When one is built, it should call:
 *   - generateRecoveryShares(kekB64)  after KEK derivation, to display/
 *     print the 5 shares for the user to store separately;
 *   - recoverKEKFromShares(shares)    on the recovery screen, feeding the
 *     3+ shares the user types back in.
 * No other module needs to change; vault-client.ts is untouched.
 */

/** Injectable randomness; default is the platform CSPRNG. Test-only seam. */
export type RandFn = (byteCount: number) => Uint8Array;

const defaultRand: RandFn = (byteCount: number): Uint8Array => {
  const buf = new Uint8Array(byteCount);
  const c = globalThis.crypto;
  if (!c || typeof c.getRandomValues !== 'function') {
    throw new Error('shamir: no CSPRNG available (crypto.getRandomValues)');
  }
  c.getRandomValues(buf);
  return buf;
};

/* ------------------------------------------------------------------ */
/* GF(256) — AES field polynomial x^8 + x^4 + x^3 + x + 1 (0x11b).      */
/* Tables are generated at module init from generator 0x03 and         */
/* self-checked by the test suite (AES reference vectors).             */
/* ------------------------------------------------------------------ */

const GF_EXP = new Uint8Array(512);
const GF_LOG = new Uint8Array(256);

(function initGf256(): void {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    GF_EXP[i] = x;
    GF_LOG[x] = i;
    // Multiply by the generator 0x03 = xtime(x) ^ x. The & 0xff mask
    // reduces the shifted value mod the field polynomial (0x11b).
    const xt = ((x << 1) ^ (x & 0x80 ? 0x11b : 0)) & 0xff;
    x = xt ^ x;
  }
  for (let i = 255; i < 512; i++) {
    GF_EXP[i] = GF_EXP[i - 255];
  }
})();

/** Field addition/subtraction: XOR in characteristic 2. (Exported for audit/testing.) */
export function gfAdd(a: number, b: number): number {
  return a ^ b;
}

export function gfMul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0;
  return GF_EXP[GF_LOG[a] + GF_LOG[b]];
}

export function gfDiv(a: number, b: number): number {
  if (b === 0) throw new Error('shamir: GF(256) division by zero');
  if (a === 0) return 0;
  return GF_EXP[GF_LOG[a] - GF_LOG[b] + 255];
}

/** Evaluate polynomial (coeffs[0] = constant term) at x via Horner. */
function evalPoly(coeffs: Uint8Array, x: number): number {
  let r = 0;
  for (let i = coeffs.length - 1; i >= 0; i--) {
    r = gfAdd(gfMul(r, x), coeffs[i]!);
  }
  return r;
}

/** Lagrange interpolation of the polynomial's value at x = 0. */
function lagrangeAtZero(xs: Uint8Array, ys: Uint8Array): number {
  let result = 0;
  for (let i = 0; i < xs.length; i++) {
    let basis = 1;
    for (let j = 0; j < xs.length; j++) {
      if (i === j) continue;
      // At x=0: numerator (0 - xj) = xj, denominator (xi - xj) = xi ^ xj.
      basis = gfMul(basis, gfDiv(xs[j]!, gfAdd(xs[i]!, xs[j]!)));
    }
    result = gfAdd(result, gfMul(ys[i]!, basis));
  }
  return result;
}

/* ------------------------------------------------------------------ */
/* SHA-256 (compact, sync). Needed because the public API is sync and  */
/* the UI must not gain a new vendor dependency for a 4-byte checksum. */
/* Verified against NIST test vectors in the test suite.               */
/* ------------------------------------------------------------------ */

const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const rotr = (x: number, n: number): number => ((x >>> n) | (x << (32 - n))) >>> 0;

/** Standard SHA-256 over arbitrary bytes. */
export function sha256Bytes(data: Uint8Array): Uint8Array {
  const h = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const paddedLen = (((data.length + 9 + 63) >> 6) << 6);
  const msg = new Uint8Array(paddedLen);
  msg.set(data);
  msg[data.length] = 0x80;
  const dv = new DataView(msg.buffer);
  // 64-bit big-endian bit length.
  dv.setUint32(paddedLen - 8, Math.floor(data.length / 0x20000000));
  dv.setUint32(paddedLen - 4, (data.length << 3) >>> 0);

  const w = new Uint32Array(64);
  for (let off = 0; off < paddedLen; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15]!, 7) ^ rotr(w[i - 15]!, 18) ^ (w[i - 15]! >>> 3);
      const s1 = rotr(w[i - 2]!, 17) ^ rotr(w[i - 2]!, 19) ^ (w[i - 2]! >>> 10);
      w[i] = (w[i - 16]! + s0 + w[i - 7]! + s1) >>> 0;
    }
    let a = h[0]!, b = h[1]!, c = h[2]!, d = h[3]!;
    let e = h[4]!, f = h[5]!, g = h[6]!, hh = h[7]!;
    for (let i = 0; i < 64; i++) {
      const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + s1 + ch + SHA256_K[i]! + w[i]!) >>> 0;
      const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (s0 + maj) >>> 0;
      hh = g; g = f; f = e; e = (d + t1) >>> 0;
      d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    h[0] = (h[0]! + a) >>> 0; h[1] = (h[1]! + b) >>> 0;
    h[2] = (h[2]! + c) >>> 0; h[3] = (h[3]! + d) >>> 0;
    h[4] = (h[4]! + e) >>> 0; h[5] = (h[5]! + f) >>> 0;
    h[6] = (h[6]! + g) >>> 0; h[7] = (h[7]! + hh) >>> 0;
  }
  const out = new Uint8Array(32);
  const odv = new DataView(out.buffer);
  for (let i = 0; i < 8; i++) odv.setUint32(i * 4, h[i]!);
  return out;
}

/* ------------------------------------------------------------------ */
/* Small codecs (hex, base64 standard alphabet).                       */
/* ------------------------------------------------------------------ */

const HEX_DIGITS = '0123456789ABCDEF';

function bytesToHex(bytes: Uint8Array): string {
  let s = '';
  for (const b of bytes) s += HEX_DIGITS[(b >> 4) & 0xf]! + HEX_DIGITS[b & 0xf]!;
  return s;
}

function hexToBytes(hex: string): Uint8Array {
  if (hex.length % 2 !== 0 || !/^[0-9a-fA-F]*$/.test(hex)) {
    throw new Error('shamir: share payload is not valid hex');
  }
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

const B64_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

function bytesToB64(bytes: Uint8Array): string {
  let s = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const b0 = bytes[i]!, b1 = i + 1 < bytes.length ? bytes[i + 1]! : 0;
    const b2 = i + 2 < bytes.length ? bytes[i + 2]! : 0;
    const n = (b0 << 16) | (b1 << 8) | b2;
    s += B64_ALPHABET[(n >> 18) & 0x3f]! + B64_ALPHABET[(n >> 12) & 0x3f]!;
    s += i + 1 < bytes.length ? B64_ALPHABET[(n >> 6) & 0x3f]! : '=';
    s += i + 2 < bytes.length ? B64_ALPHABET[n & 0x3f]! : '=';
  }
  return s;
}

function b64ToBytes(b64: string): Uint8Array {
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(b64) || b64.length % 4 !== 0) {
    throw new Error('shamir: not valid base64');
  }
  const vals = new Uint8Array(b64.length);
  let len = b64.length;
  for (let i = 0; i < b64.length; i++) {
    const ch = b64[i]!;
    if (ch === '=') { len = i; break; }
    vals[i] = B64_ALPHABET.indexOf(ch);
    if (vals[i] < 0) throw new Error('shamir: not valid base64');
  }
  const outLen = Math.floor((len * 6) / 8);
  const out = new Uint8Array(outLen);
  let acc = 0, bits = 0, pos = 0;
  for (let i = 0; i < len; i++) {
    acc = (acc << 6) | vals[i]!;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out[pos++] = (acc >> bits) & 0xff;
    }
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* Share encoding: PAE-SSS1-T{t}-N{n}-X{xx}-<HEX>                      */
/* ------------------------------------------------------------------ */

interface ParsedShare {
  x: number;
  t: number;
  n: number;
  y: Uint8Array;
}

function encodeShare(x: number, t: number, n: number, y: Uint8Array): string {
  return `PAE-SSS1-T${t}-N${n}-X${String(x).padStart(2, '0')}-${bytesToHex(y)}`;
}

const SHARE_RE = /^PAE-SSS1-T(\d{1,3})-N(\d{1,3})-X(\d{1,3})-([0-9A-Fa-f]+)$/;

function parseShare(s: string): ParsedShare {
  const m = SHARE_RE.exec(s.trim());
  if (!m) {
    throw new Error(
      'shamir: malformed share (expected PAE-SSS1-T{t}-N{n}-X{xx}-<HEX>)'
    );
  }
  const t = parseInt(m[1]!, 10), n = parseInt(m[2]!, 10), x = parseInt(m[3]!, 10);
  if (t < 2 || t > 255 || n < 2 || n > 255 || t > n) {
    throw new Error(`shamir: share has invalid threshold/total T${t}-N${n}`);
  }
  if (x < 1 || x > 255) {
    throw new Error(`shamir: share index X${x} out of range 1..255`);
  }
  const y = hexToBytes(m[4]!);
  if (y.length === 0) throw new Error('shamir: share has empty payload');
  return { x, t, n, y };
}

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

/**
 * Split a secret into n shares; any t reconstruct it.
 * The payload carries a 4-byte SHA-256 checksum so corrupted share sets
 * fail loudly on recovery. Coefficients come from the platform CSPRNG
 * unless `rand` is supplied (test seam only).
 */
export function splitSecret(
  secretBytes: Uint8Array,
  n = 5,
  t = 3,
  rand: RandFn = defaultRand
): string[] {
  if (!(secretBytes instanceof Uint8Array) || secretBytes.length === 0) {
    throw new Error('shamir: secret must be a non-empty Uint8Array');
  }
  if (!Number.isInteger(n) || n < 2 || n > 255) {
    throw new Error('shamir: n must be an integer in [2, 255]');
  }
  if (!Number.isInteger(t) || t < 2 || t > n) {
    throw new Error('shamir: t must be an integer in [2, n]');
  }
  // Payload = secret || SHA256(secret)[0..4]: tamper/corruption detection.
  const checksum = sha256Bytes(secretBytes).subarray(0, 4);
  const payload = new Uint8Array(secretBytes.length + 4);
  payload.set(secretBytes);
  payload.set(checksum, secretBytes.length);

  // One random polynomial per payload byte; every share evaluates the
  // SAME polynomials at its own x. (Generating fresh coefficients per
  // share would produce points on unrelated polynomials and recovery
  // would be impossible.)
  const ys: Uint8Array[] = [];
  for (let s = 0; s < n; s++) ys.push(new Uint8Array(payload.length));
  for (let i = 0; i < payload.length; i++) {
    const coeffs = new Uint8Array(t);
    coeffs[0] = payload[i]!;
    coeffs.set(rand(t - 1), 1);
    for (let s = 0; s < n; s++) {
      ys[s]![i] = evalPoly(coeffs, s + 1);
    }
  }
  const shares: string[] = [];
  for (let s = 0; s < n; s++) {
    shares.push(encodeShare(s + 1, t, n, ys[s]!));
  }
  return shares;
}

/**
 * Recover the secret from >= t shares. Throws if fewer than t shares are
 * supplied, if shares are inconsistent (mixed splits, duplicate index),
 * or if the embedded checksum does not verify (corrupted share).
 */
export function recoverSecret(shares: string[]): Uint8Array {
  if (!Array.isArray(shares) || shares.length === 0) {
    throw new Error('shamir: at least one share is required');
  }
  const parsed = shares.map(parseShare);
  const t = parsed[0]!.t, n = parsed[0]!.n, payloadLen = parsed[0]!.y.length;
  for (const p of parsed) {
    if (p.t !== t || p.n !== n || p.y.length !== payloadLen) {
      throw new Error(
        'shamir: shares are inconsistent (threshold/total/length mismatch — possibly mixed splits)'
      );
    }
  }
  if (parsed.length < t) {
    throw new Error(
      `shamir: need at least ${t} shares to recover, got ${parsed.length}`
    );
  }
  const seen = new Set<number>();
  for (const p of parsed) {
    if (seen.has(p.x)) throw new Error(`shamir: duplicate share index X${p.x}`);
    seen.add(p.x);
  }
  const xs = new Uint8Array(parsed.map((p) => p.x));
  const payload = new Uint8Array(payloadLen);
  for (let i = 0; i < payloadLen; i++) {
    const ys = new Uint8Array(parsed.map((p) => p.y[i]!));
    payload[i] = lagrangeAtZero(xs, ys);
  }
  const secret = payload.subarray(0, payloadLen - 4);
  const checksum = payload.subarray(payloadLen - 4);
  const expected = sha256Bytes(secret).subarray(0, 4);
  for (let i = 0; i < 4; i++) {
    if (checksum[i] !== expected[i]) {
      throw new Error(
        'shamir: integrity check failed — shares are corrupted or do not belong together'
      );
    }
  }
  return secret.slice();
}

/* ------------------------------------------------------------------ */
/* Vault UI integration surface (KEK = base64 of 32 bytes).            */
/* ------------------------------------------------------------------ */

/**
 * Split the client-side KEK (base64 of 32 bytes, as produced by
 * deriveKeyB64 in vault-client.ts) into 5 recovery shares (3-of-5).
 * Call after KEK derivation on the vault setup screen.
 */
export function generateRecoveryShares(kekB64: string): string[] {
  const kek = b64ToBytes(kekB64);
  if (kek.length !== 32) {
    throw new Error(`shamir: KEK must be 32 bytes, got ${kek.length}`);
  }
  return splitSecret(kek, 5, 3);
}

/**
 * Recover the KEK (base64 of 32 bytes) from >= 3 recovery shares.
 * Call on the vault recovery screen with the shares the user provides.
 */
export function recoverKEKFromShares(shares: string[]): string {
  const secret = recoverSecret(shares);
  if (secret.length !== 32) {
    throw new Error(
      `shamir: recovered secret is ${secret.length} bytes, expected 32-byte KEK`
    );
  }
  return bytesToB64(secret);
}
