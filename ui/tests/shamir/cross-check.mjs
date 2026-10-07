// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

// Cross-implementation check: PAE shamir.ts vs the independently-audited
// privy-io `shamir-secret-sharing` 0.0.4 (Cure53 + Zellic audits).
// Both use GF(2^8) / x^8+x^4+x^3+x+1, so shares are interchangeable at the
// (x, y) level. This script is verification-only; the library is NOT
// vendored into the repo.
import { split, combine } from './oracle-lib/package/esm/index.js';
import { splitSecret, recoverSecret } from './out/shamir.js';
import { randomBytes, createHash } from 'node:crypto';

const eq = (a, b) => Buffer.from(a).equals(Buffer.from(b));
const decodeMy = (s) => {
  const m = /^PAE-SSS1-T(\d+)-N(\d+)-X(\d+)-([0-9A-F]+)$/.exec(s);
  if (!m) throw new Error('bad share: ' + s);
  return { x: parseInt(m[3], 10), y: Buffer.from(m[4], 'hex') };
};
const encodeMine = (x, yHex) =>
  `PAE-SSS1-T3-N5-X${String(x).padStart(2, '0')}-${yHex.toUpperCase()}`;

let pass = 0;
// Direction A: THEIR split -> MY recover (payload = secret||sha256[0:4]).
for (let round = 0; round < 5; round++) {
  const secret = randomBytes(32);
  const payload = Buffer.concat([secret, createHash('sha256').update(secret).digest().subarray(0, 4)]);
  const theirShares = await split(new Uint8Array(payload), 5, 3); // [y(36)..., x]
  const mine = theirShares.slice(0, 3).map((sh) => {
    const x = sh[sh.length - 1];
    const y = sh.subarray(0, sh.length - 1);
    return encodeMine(x, Buffer.from(y).toString('hex'));
  });
  const rec = recoverSecret(mine);
  if (!eq(rec, secret)) throw new Error(`A round ${round}: mismatch`);
  pass++;
}
// Direction B: MY split -> THEIR combine (recovers my checksummed payload).
for (let round = 0; round < 5; round++) {
  const secret = randomBytes(32);
  const myShares = splitSecret(secret, 5, 3).slice(0, 3).map(decodeMy);
  const theirs = myShares.map(({ x, y }) => new Uint8Array(Buffer.concat([y, Buffer.from([x])])));
  const payload = Buffer.from(await combine(theirs));
  const expected = Buffer.concat([secret, createHash('sha256').update(secret).digest().subarray(0, 4)]);
  if (!eq(payload, expected)) throw new Error(`B round ${round}: mismatch`);
  pass++;
}
console.log(`CROSS-CHECK PASS: ${pass}/10 rounds, both directions`);
