// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Ambient type shim for the vendored hash-wasm ESM bundle
 * (ui/vendor/hash-wasm/hash-wasm.esm.js).
 *
 * The bundle is plain JS outside the TS rootDir, so we declare its shape
 * here instead of importing its types. This keeps `tsc` green without a
 * runtime CDN dependency (vendor-natively doctrine: the file ships with
 * the app, see ui/vendor/README.md).
 *
 * DEPLOY NOTE: ui/vendor must be copied next to the compiled output
 * (dist/vendor) — vault-client.js resolves the bundle at
 * ../../vendor/hash-wasm/hash-wasm.esm.js relative to dist/crypto/.
 */
declare module '*hash-wasm.esm.js' {
  export interface Argon2Options {
    /** Password bytes (or string). */
    password: string | Uint8Array;
    /** Salt bytes (or string). */
    salt: string | Uint8Array;
    /** Optional secret for keyed hashing. */
    secret?: Uint8Array;
    /** Number of iterations (t). */
    iterations: number;
    /** Degree of parallelism (p). */
    parallelism: number;
    /** Memory in kibibytes (m). */
    memorySize: number;
    /** Output length in bytes. */
    hashLength: number;
    /** Desired output type. Defaults to 'hex'. */
    outputType?: 'hex' | 'binary' | 'encoded';
  }

  export function argon2id(
    options: Argon2Options & { outputType: 'binary' }
  ): Promise<Uint8Array>;
  export function argon2id(options: Argon2Options): Promise<string>;
}
