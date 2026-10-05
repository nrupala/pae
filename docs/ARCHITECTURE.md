# PAE Architecture

## Overview

PAE uses a three-language stratified architecture where each language handles what it does best. (The C numerical core ships in `engine/c/`, linked via Rust FFI.)

```
+------------------+     +-------------------+     +------------------+
|   UI (Browser)   | <-> |   Rust Engine     | <-> |  Python Analytics |
|  Vanilla TS      |     |   Axum API        |     |  Factor Models   |
|  Web Components  |     |   Risk Calcs      |     |  Optimization    |
|  Canvas/SVG      |     |   Crypto Vault    |     |  PKE / Decision  |
|  < 200KB         |     |   < 1ms latency   |     |  < 1s latency    |
+------------------+     +-------------------+     +------------------+
                                  |
                          +-------+-------+
                          | C Numerical   |
                          | BLAS/LAPACK   |
                          | BLAS/LAPACK   |
                          | QuantLib (FFI)|
                          +-------+-------+
```

## Layer Responsibilities

### Rust Engine (Hot Path)
- Risk calculations: VaR, CVaR, Sharpe, Sortino, volatility, drawdown
- Monte Carlo simulation: 1K-100K paths with Box-Muller sampling
- Correlation matrices: Pearson correlation over rolling windows
- Stress testing: Historical scenario application to holdings
- API serving: Axum REST endpoints, async, type-safe
- Cryptography: AES-256-GCM encryption/decryption, Argon2id key derivation
- Latency target: < 1ms per calculation

### Python Analytics (Research Layer)
- Factor models: Fama-French 5-Factor OLS decomposition
- Portfolio optimization: max-Sharpe, min-variance, and risk-parity mixes + efficient frontier (`analytics/pae/models/optimize.py`, long-only; `POST /api/v1/analytics/optimize`, `optimize_portfolio` tool, `#optimize` UI view)
- Performance attribution: Brinson-Hood-Beebower arithmetic attribution vs. benchmark by segment — allocation, selection, and interaction effects (`analytics/pae/models/brinson.py`; `POST /api/v1/analytics/attribution`, `brinson_attribution` tool)
- Carry analysis: Margin intelligence, income coverage ratios
- Personal Knowledge Engine: Document ingestion, chunking, theme classification, local embedding generation (fastembed `BAAI/bge-small-en-v1.5`, 384-dim ONNX, on-device), sqlite-vec vector index, retrieval (vector search with keyword fallback)
- Decision Intelligence: Journal, calibration, bias detection
- Latency target: < 1s per analysis

### C Numerical Core (Primitives)
`engine/c/` compiled via `engine/build.rs` (cc crate), linked against BLAS/LAPACK, exposed through safe Rust FFI (`engine/src/num_ffi.rs`).
- Matrix operations: covariance via `dgemm_`, Cholesky via `dpotrf_`, symmetric eigendecomposition via `dsyev_` (`pae_num.c`); the correlation matrix is computed through this path by default, with the pure-Rust implementation kept as fallback and test reference
- Bond pricing: discount-curve NPV, yield-to-maturity (bisection), Macaulay/modified duration, convexity (`pae_bonds.c`, served at `POST /api/v1/analytics/bond`) — standard bond mathematics following QuantLib's bond methodology. Linking the full QuantLib C++ library was probed and deliberately not taken (~436k LOC + Boost + hours-long builds for ~200 lines of needed math; license-compatible but disproportionate)
- Numerical optimization: Low-level solvers — roadmap (the Python optimizer in `analytics/pae/models/optimize.py` covers portfolio optimization today)
- Latency target: Sub-microsecond primitives

### Vanilla TypeScript UI (Presentation)
- Web Components: Shadow DOM encapsulation, no framework
- Canvas/SVG charts: Custom rendering, zero chart library dependency
- CSS Custom Properties: Dark/light theming, system preference detection
- Bundle target: < 200KB gzipped total

## Data Flow

```
User Input (holdings, parameters)
    |
    v
[Client-Side Encryption] -- AES-256-GCM with user's KEK
    |
    v
[Encrypted Storage] -- SQLite or PostgreSQL (ciphertext only)
    |
    v
[Client-Side Decryption] -- KEK derived from passphrase via Argon2id
    |
    v
[Rust Engine API] -- Risk calcs, Monte Carlo, correlation, stress
    |
    v
[Python Analytics] -- Factor models, optimization, PKE retrieval
    |
    v
[UI Rendering] -- Web Components, Canvas charts, data tables
```

## Zero-Knowledge Guarantee

> **Status: implemented.** Key derivation runs client-side in the browser (`ui/src/crypto/vault-client.ts`, Argon2id via vendored hash-wasm against `GET /api/v1/crypto/kdf-params`); the server exposes no passphrase-derivation endpoint (`POST /api/v1/crypto/derive-key` was removed 2026-10-05). Per-vault DEKs wrapped by the KEK (AES-256-GCM, versioned v2 envelope, `GET/POST /api/v1/crypto/dek-envelope`); Shamir 3-of-5 recovery is client-side (`ui/src/crypto/shamir.ts`). See `docs/THREAT_MODEL.md` for the full model.

The server never sees plaintext financial data. Encryption and decryption happen exclusively in the client. The server stores and transmits only ciphertext.

This is enforced architecturally:
- No server-side function accepts plaintext financial data
- All API endpoints operate on encrypted payloads or derived analytics
- Key material (KEK) exists only in client memory during active sessions — derived client-side, never transmitted (`ui/src/crypto/vault-client.ts`)
- Argon2id with 600K iterations derives the KEK from the user's passphrase
