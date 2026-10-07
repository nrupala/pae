// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

// Independent KAT oracle for PAE shamir.ts.
// Uses a COMPLETELY different code path from the implementation under test:
//   - GF(256) multiply: Russian-peasant shift/XOR (no log/exp tables)
//   - SHA-256: node:crypto (not the hand-rolled TS implementation)
//   - fixed coefficient stream 0xA5, 0xA6, ... (mirrors the test's rand seam)
import { createHash } from 'node:crypto';

function pmul(a, b) {
  let p = 0;
  a &= 0xff; b &= 0xff;
  while (b > 0) {
    if (b & 1) p ^= a;
    const hi = a & 0x80;
    a = (a << 1) & 0xff;
    if (hi) a ^= 0x1b; // AES field polynomial x^8+x^4+x^3+x+1
    b >>= 1;
  }
  return p;
}

function horner(coeffs, x) {
  let r = 0;
  for (let i = coeffs.length - 1; i >= 0; i--) r = pmul(r, x) ^ coeffs[i];
  return r;
}

// Sanity: FIPS-197 AES example {57} * {83} = {c1}.
if (pmul(0x57, 0x83) !== 0xc1) throw new Error('oracle field broken');

const secret = Uint8Array.from([0x01, 0x02, 0x03]);
const checksum = createHash('sha256').update(secret).digest().subarray(0, 4);
const payload = Buffer.concat([Buffer.from(secret), checksum]);

let c = 0;
// One polynomial per payload byte (coefficients drawn once per byte),
// evaluated at every share x — mirrors the fixed splitSecret structure.
const polys = [];
for (let i = 0; i < payload.length; i++) {
  polys.push([payload[i], (0xa5 + c++) & 0xff, (0xa5 + c++) & 0xff]);
}
const shares = [];
for (let s = 0; s < 5; s++) {
  const x = s + 1;
  const y = polys.map((coeffs) => horner(coeffs, x));
  shares.push(`PAE-SSS1-T3-N5-X${String(x).padStart(2, '0')}-${Buffer.from(y).toString('hex').toUpperCase()}`);
}
console.log('CHECKSUM=' + Buffer.from(checksum).toString('hex'));
for (const sh of shares) console.log(sh);
