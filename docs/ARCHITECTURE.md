# PAE Architecture

## Overview

PAE uses a three-language stratified architecture where each language handles what it does best. (The C numerical core is **Planned** — roadmap, not yet implemented.)

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
                          | (Planned)     |
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
- Portfolio optimization: Skfolio/CVXPY integration (100+ models) — **Planned** (declared as dependencies, not yet wired in)
- Performance attribution: Brinson-Fachler, currency attribution — **Planned** (not yet implemented)
- Carry analysis: Margin intelligence, income coverage ratios
- Personal Knowledge Engine: Document ingestion, embedding (**Planned** — chunk/theme pipeline exists, embedding generation not yet implemented), retrieval
- Decision Intelligence: Journal, calibration, bias detection
- Latency target: < 1s per analysis

### C Numerical Core (Primitives) — Planned
> **Roadmap, not yet implemented.** No C sources exist in the repo today; numerics are pure Rust (`ndarray`/`statrs`).
- Matrix operations: BLAS/LAPACK via Rust FFI
- Bond pricing: QuantLib yield curves, duration, convexity
- Numerical optimization: Low-level solvers
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

> **Status: target architecture (partially Planned).** The storage schema holds ciphertext today and the holdings API accepts only pre-encrypted blobs, so the engine never sees plaintext *on the storage path*. But client-side key derivation and encryption are **not yet implemented** — the only in-repo key-derivation path is server-side (`POST /api/v1/crypto/derive-key` takes the raw passphrase), and the DEK/KEK hierarchy and Shamir recovery below are roadmap items. See `docs/THREAT_MODEL.md` and the 2026-10-05 threat-model audit notes before making public security claims.

The server never sees plaintext financial data. Encryption and decryption happen exclusively in the client. The server stores and transmits only ciphertext.

This is enforced architecturally:
- No server-side function accepts plaintext financial data
- All API endpoints operate on encrypted payloads or derived analytics
- Key material (KEK) exists only in client memory during active sessions — **Planned** (no client-side key handling exists yet)
- Argon2id with 600K iterations derives the KEK from the user's passphrase
