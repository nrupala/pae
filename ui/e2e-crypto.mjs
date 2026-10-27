#!/usr/bin/env node
// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * PAE crypto end-to-end check (MANUAL — not wired into CI).
 *
 * Status: NOT-RUN in this environment (no engine binary available;
 * the Oracle box cannot build Rust — cargo 1.75 predates edition2024 —
 * and no toolchain install was in scope).
 *
 * What it does, against a LIVE engine:
 *   1. GET /api/v1/crypto/kdf-params
 *   2. derive the key CLIENT-SIDE via the compiled vault-client module
 *      (Argon2id, vendored hash-wasm — the passphrase never leaves this process)
 *   3. POST /encrypt with the derived key (agent token required
 *      since Phase 3C — read from PAE_AGENT_TOKEN / the token file;
 *      the script first asserts a tokenless call gets 401)
 *   4. POST /decrypt with the derived key + token
 *   5. assert the roundtripped plaintext matches
 *   6. assert NO request body in the whole flow ever contained the passphrase
 *
 * Prerequisites:
 *   1. Build the UI:            cd ui && npm install && npm run build
 *   2. Stage the vendored WASM:  cp -r ui/vendor ui/dist/vendor
 *   3. Start the engine:        PAE_DB_PATH=/tmp/pae-e2e.db PAE_ENGINE_PORT=3101 \
 *                                 ./engine/target/debug/pae-engine
 *      (or target/release). Wait for it to log "PAE Engine listening".
 *   4. Run:  node ui/e2e-crypto.mjs http://127.0.0.1:3101
 *
 * NOTE: step 2 derives at PRODUCTION params (Argon2id: 3 iterations,
 * 64 MiB, 4 lanes — RFC 9106 first-recommended profile). In Node/WASM
 * this takes a few seconds. (Before Phase 3C the engine advertised
 * 600K iterations, which never finished; that is fixed.)
 */

import { getKdfParams, deriveKeyB64, encrypt, decrypt } from './dist/crypto/vault-client.js';

const ENGINE = process.argv[2] ?? 'http://127.0.0.1:3101';
const PASSPHRASE = 'e2e-test-passphrase-do-not-reuse-2026-10-05';
const PLAINTEXT = 'PAE e2e roundtrip payload: holdings/AAPL';

// Record every request body so we can prove the passphrase never travels.
const seenBodies = [];
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, init) => {
  if (init?.body) seenBodies.push(String(init.body));
  return realFetch(url, init);
};

const fail = (msg) => {
  console.error(`E2E FAIL: ${msg}`);
  process.exit(1);
};

console.log(`engine: ${ENGINE}`);

// 1. kdf-params
const params = await getKdfParams(ENGINE);
console.log('kdf-params:', JSON.stringify({ ...params, salt_b64: params.salt_b64.slice(0, 8) + '…' }));

// 2. client-side derivation (slow at production params — expected)
console.log('deriving key client-side (production params, may take minutes)…');
const t0 = Date.now();
const keyB64 = await deriveKeyB64(PASSPHRASE, params);
console.log(`derived in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
const keyBytes = Buffer.from(keyB64, 'base64');
if (keyBytes.length !== 32) fail(`derived key is ${keyBytes.length} bytes, expected 32`);
if (keyB64 === PASSPHRASE) fail('derived key equals passphrase?!');

// 2b. agent token (Phase 3C): /encrypt and /decrypt require it.
// Resolution mirrors the engine: PAE_AGENT_TOKEN env, else the token
// file (PAE_AGENT_TOKEN_PATH, else ~/.pae/agent-token). Read from
// disk here; never printed.
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

let AGENT_TOKEN = process.env.PAE_AGENT_TOKEN ?? '';
if (!AGENT_TOKEN) {
  const tokenPath =
    process.env.PAE_AGENT_TOKEN_PATH ?? join(homedir(), '.pae', 'agent-token');
  try {
    AGENT_TOKEN = readFileSync(tokenPath, 'utf8').trim();
  } catch {
    fail(`no agent token: set PAE_AGENT_TOKEN or start the engine once so it creates ${tokenPath}`);
  }
}

// 2c. no token -> 401 (the endpoints must not be open oracles)
{
  const res = await realFetch(`${ENGINE}/api/v1/crypto/encrypt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plaintext: 'x', key_b64: keyB64 }),
  });
  if (res.status !== 401) fail(`encrypt without agent token returned ${res.status}, expected 401`);
  console.log('no-token encrypt correctly rejected with 401');
}

// 3. encrypt via engine (token-gated since Phase 3C)
const { ciphertext_b64, nonce_b64 } = await encrypt(ENGINE, keyB64, PLAINTEXT, AGENT_TOKEN);
console.log('encrypt ok, ciphertext bytes:', Buffer.from(ciphertext_b64, 'base64').length);

// 4. decrypt via engine
const back = await decrypt(ENGINE, keyB64, ciphertext_b64, nonce_b64, AGENT_TOKEN);
if (back !== PLAINTEXT) fail(`roundtrip mismatch: got ${JSON.stringify(back)}`);
console.log('decrypt roundtrip ok');

// 5. wrong key must fail
let wrongKeyFailed = false;
try {
  const wrongB64 = Buffer.from(new Uint8Array(32).fill(7)).toString('base64');
  await decrypt(ENGINE, wrongB64, ciphertext_b64, nonce_b64, AGENT_TOKEN);
} catch {
  wrongKeyFailed = true;
}
if (!wrongKeyFailed) fail('decrypt with wrong key unexpectedly succeeded');
console.log('wrong-key decrypt correctly rejected');

// 6. the passphrase must never have appeared in any request body
for (const body of seenBodies) {
  if (body.includes(PASSPHRASE)) {
    fail(`passphrase leaked into request body: ${body.slice(0, 120)}…`);
  }
}
console.log(`passphrase-leak check ok (${seenBodies.length} request bodies inspected)`);

console.log('E2E PASS');
