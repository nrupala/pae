# ui/vendor — vendored third-party JS (no runtime CDN dependencies)

Per the vendor-natively doctrine, browser dependencies ship with the app,
never from a CDN at runtime.

## hash-wasm/

- **File:** `hash-wasm.esm.js`
- **Package:** [hash-wasm](https://www.npmjs.com/package/hash-wasm) v4.12.0 (MIT, (c) Dani Biro)
- **Source:** `https://cdn.jsdelivr.net/npm/hash-wasm@4.12.0/dist/index.esm.js`
  (renamed on vendor; WASM is inlined as base64 — fully self-contained)
- **Used by:** `ui/src/crypto/vault-client.ts` for client-side Argon2id key
  derivation (the only KDF path; the server has no passphrase endpoint).
- **Provenance note:** the bundle's argon2 implementation hardcodes
  version `0x13` (19) — verified in the package's `lib/argon2.ts` source
  (`const version = 0x13`, PHC encoder emits `$v=19$`) — matching the
  engine's production KDF spec.

## Deploy

Copy this directory next to the compiled UI output so the relative import
in `dist/crypto/vault-client.js` resolves:

```sh
cp -r ui/vendor ui/dist/vendor   # -> dist/vendor/hash-wasm/hash-wasm.esm.js
```
