// Copyright (C) 2026 Nrupal Akolkar
// SPDX-License-Identifier: AGPL-3.0-or-later

// PAE shamir.ts test suite — runs against the tsc-compiled output.
//   tsc ... --outDir out ui/src/crypto/shamir.ts && node --test shamir.test.mjs
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes, createHash } from 'node:crypto';
import {
  splitSecret,
  recoverSecret,
  sha256Bytes,
  gfAdd,
  gfMul,
  gfDiv,
  generateRecoveryShares,
  recoverKEKFromShares,
} from './out/shamir.js';

const hex = (b) => Buffer.from(b).toString('hex');

// Deterministic RNG seam: byte stream 0xA5, 0xA6, ... (mirrors kat-oracle.mjs).
const fixedRand = () => {
  let c = 0;
  return (n) => {
    const b = new Uint8Array(n);
    for (let i = 0; i < n; i++) b[i] = (0xa5 + c++) & 0xff;
    return b;
  };
};

// Known-answer vector: secret [01 02 03], t=3, n=5, fixed coefficient
// stream. Expected shares produced by kat-oracle.mjs — an INDEPENDENT
// implementation (peasant GF(256) multiply, node:crypto SHA-256).
// Checksum of the secret: sha256(010203)[0:4] = 039058c6.
const KAT_SECRET = new Uint8Array([0x01, 0x02, 0x03]);
const KAT_SHARES = [
  'PAE-SSS1-T3-N5-X01-020D00049347C5',
  'PAE-SSS1-T3-N5-X02-FEC1D4C85FEB41',
  'PAE-SSS1-T3-N5-X03-FDCED7CF5CF442',
  'PAE-SSS1-T3-N5-X04-2DC6DFB71C27E1',
  'PAE-SSS1-T3-N5-X05-2EC9DCB01F38E2',
];

describe('sha256Bytes', () => {
  it('matches NIST vectors', () => {
    const t = (s) => new TextEncoder().encode(s);
    assert.equal(hex(sha256Bytes(t(''))), 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    assert.equal(hex(sha256Bytes(t('abc'))), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    assert.equal(
      hex(sha256Bytes(t('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq'))),
      '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1'
    );
  });
  it('matches node:crypto on random inputs incl. multi-block', () => {
    for (const len of [0, 1, 55, 56, 57, 64, 1000]) {
      const d = randomBytes(len);
      assert.equal(hex(sha256Bytes(d)), createHash('sha256').update(d).digest('hex'), `len=${len}`);
    }
  });
});

describe('GF(256)', () => {
  it('AES reference vector {57}*{83}={c1}', () => {
    assert.equal(gfMul(0x57, 0x83), 0xc1);
  });
  it('exhaustive: div inverts mul over all 255x255 nonzero pairs', () => {
    for (let a = 1; a < 256; a++) {
      for (let b = 1; b < 256; b++) {
        assert.equal(gfDiv(gfMul(a, b), b), a, `a=${a} b=${b}`);
      }
    }
  });
  it('add is XOR', () => {
    assert.equal(gfAdd(0x53, 0xca), 0x99);
  });
});

describe('known-answer vector (independent oracle)', () => {
  it('splitSecret reproduces the oracle shares exactly', () => {
    assert.deepEqual(splitSecret(KAT_SECRET, 5, 3, fixedRand()), KAT_SHARES);
  });
  it('recoverSecret reconstructs from the oracle shares', () => {
    assert.deepEqual([...recoverSecret(KAT_SHARES)], [...KAT_SECRET]);
    assert.deepEqual([...recoverSecret([KAT_SHARES[0], KAT_SHARES[2], KAT_SHARES[4]])], [...KAT_SECRET]);
  });
  it('share format: prefix, zero-padded index, uppercase hex', () => {
    const s = KAT_SHARES[0];
    assert.match(s, /^PAE-SSS1-T3-N5-X01-[0-9A-F]+$/);
    assert.equal(s.split('-')[5].length, 14); // 7-byte payload -> 14 hex chars
  });
});

describe('roundtrips', () => {
  for (const len of [1, 2, 16, 32, 64]) {
    it(`split->recover, secret length ${len}`, () => {
      for (let r = 0; r < 5; r++) {
        const secret = randomBytes(len);
        const shares = splitSecret(secret, 5, 3);
        assert.equal(shares.length, 5);
        assert.deepEqual([...recoverSecret(shares)], [...secret]);
        assert.deepEqual([...recoverSecret(shares.slice(0, 3))], [...secret]);
        assert.deepEqual([...recoverSecret(shares.slice(2, 5))], [...secret]);
      }
    });
  }
  it('every 3-of-5 combination reconstructs', () => {
    const secret = randomBytes(32);
    const shares = splitSecret(secret, 5, 3);
    let combos = 0;
    for (let a = 0; a < 5; a++)
      for (let b = a + 1; b < 5; b++)
        for (let c = b + 1; c < 5; c++) {
          assert.deepEqual([...recoverSecret([shares[a], shares[b], shares[c]])], [...secret]);
          combos++;
        }
    assert.equal(combos, 10);
  });
  it('4 and 5 shares also work, order-independent', () => {
    const secret = randomBytes(32);
    const shares = splitSecret(secret, 5, 3);
    const rev = [...shares].reverse();
    assert.deepEqual([...recoverSecret(rev)], [...secret]);
    assert.deepEqual([...recoverSecret(shares.slice(0, 4))], [...secret]);
  });
  it('non-default thresholds: 2-of-2, 5-of-5, 7-of-10', () => {
    for (const [n, t] of [[2, 2], [5, 5], [10, 7]]) {
      const secret = randomBytes(24);
      const shares = splitSecret(secret, n, t);
      assert.deepEqual([...recoverSecret(shares.slice(0, t))], [...secret]);
    }
  });
});

describe('failure modes', () => {
  const secret = randomBytes(32);
  const shares = splitSecret(secret, 5, 3);

  it('2 of 5 FAILS loudly (below threshold)', () => {
    let pairs = 0;
    for (let a = 0; a < 5; a++)
      for (let b = a + 1; b < 5; b++) {
        assert.throws(() => recoverSecret([shares[a], shares[b]]), /at least 3 shares/);
        pairs++;
      }
    assert.equal(pairs, 10);
  });
  it('single share FAILS', () => {
    assert.throws(() => recoverSecret([shares[0]]), /at least 3 shares/);
  });
  it('tampered share (flipped hex digit) detected via checksum', () => {
    const bad = shares[2].slice(0, -1) + (shares[2].endsWith('0') ? '1' : '0');
    assert.notEqual(bad, shares[2]);
    assert.throws(
      () => recoverSecret([shares[0], shares[1], bad]),
      /integrity check failed/
    );
  });
  it('share from a different split mixed in is detected', () => {
    const other = splitSecret(secret, 5, 3); // same secret, fresh polynomials
    assert.notDeepEqual(other, shares);
    assert.throws(
      () => recoverSecret([shares[0], shares[1], other[2]]),
      /integrity check failed/
    );
  });
  it('tampered share index (duplicate X) rejected', () => {
    const dup = shares[1].replace('-X02-', '-X01-');
    assert.throws(() => recoverSecret([shares[0], dup, shares[2]]), /duplicate share index/);
  });
  it('malformed shares rejected', () => {
    for (const s of [
      '',
      'garbage',
      'PAE-SSS1-T3-N5-X01', // missing payload
      'PAE-SSS2-T3-N5-X01-AA', // wrong version
      'XXX-SSS1-T3-N5-X01-AA', // wrong issuer tag
      'PAE-SSS1-T1-N5-X01-AA', // t < 2
      'PAE-SSS1-T4-N3-X01-AA', // t > n
      'PAE-SSS1-T3-N5-X00-AA', // x = 0
      'PAE-SSS1-T3-N5-X01-ZZ', // non-hex
      'PAE-SSS1-T3-N5-X01-A', // odd hex length
    ]) {
      assert.throws(() => recoverSecret([s, shares[1], shares[2]]), /malformed|invalid|out of range|hex/, s);
    }
  });
  it('inconsistent share sets rejected (mixed T/N/length)', () => {
    const s7 = splitSecret(secret, 7, 4);
    assert.throws(() => recoverSecret([shares[0], shares[1], s7[2]]), /inconsistent/);
    const short = splitSecret(randomBytes(8), 5, 3);
    assert.throws(() => recoverSecret([shares[0], shares[1], short[2]]), /inconsistent/);
  });
  it('argument validation', () => {
    assert.throws(() => splitSecret(new Uint8Array(0)), /non-empty/);
    assert.throws(() => splitSecret(secret, 1, 1), /n must be/);
    assert.throws(() => splitSecret(secret, 5, 1), /t must be/);
    assert.throws(() => splitSecret(secret, 3, 4), /t must be/);
    assert.throws(() => recoverSecret([]), /at least one share/);
  });
  it('lowercase payload hex still parses (transcription tolerance)', () => {
    const lower = KAT_SHARES.slice(0, 3).map((s) => {
      const i = s.lastIndexOf('-');
      return s.slice(0, i + 1) + s.slice(i + 1).toLowerCase();
    });
    assert.deepEqual([...recoverSecret(lower)], [...KAT_SECRET]);
  });
});

describe('KEK integration surface', () => {
  it('generateRecoveryShares -> recoverKEKFromShares roundtrip', () => {
    const kek = randomBytes(32);
    const kekB64 = kek.toString('base64');
    const shares = generateRecoveryShares(kekB64);
    assert.equal(shares.length, 5);
    for (const [i, s] of shares.entries()) {
      assert.match(s, new RegExp(`^PAE-SSS1-T3-N5-X0${i + 1}-[0-9A-F]{72}$`));
    }
    assert.equal(recoverKEKFromShares(shares.slice(0, 3)), kekB64);
    assert.equal(recoverKEKFromShares(shares.slice(2, 5)), kekB64);
    // every 3-of-5 combo
    for (let a = 0; a < 5; a++)
      for (let b = a + 1; b < 5; b++)
        for (let c = b + 1; c < 5; c++)
          assert.equal(recoverKEKFromShares([shares[a], shares[b], shares[c]]), kekB64);
  });
  it('KEK must be exactly 32 bytes', () => {
    assert.throws(() => generateRecoveryShares(randomBytes(16).toString('base64')), /32 bytes/);
    assert.throws(() => generateRecoveryShares('!!!not-base64!!!'), /base64/);
    const shortShares = splitSecret(randomBytes(16), 5, 3);
    assert.throws(() => recoverKEKFromShares(shortShares.slice(0, 3)), /32-byte KEK/);
  });
  it('corrupted KEK share fails loudly', () => {
    const kekB64 = randomBytes(32).toString('base64');
    const shares = generateRecoveryShares(kekB64);
    const bad = shares[0].slice(0, -2) + 'FF';
    assert.throws(() => recoverKEKFromShares([bad, shares[1], shares[2]]), /integrity check failed/);
  });
});
