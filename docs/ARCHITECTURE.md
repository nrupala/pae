# PAE Architecture

## Overview

PAE uses a three-language stratified architecture where each language handles what it does best.

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
                          | QuantLib (FFI)|
                          +---------------+
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
- Portfolio optimization: Skfolio/CVXPY integration (100+ models)
- Performance attribution: Brinson-Fachler, currency attribution
- Carry analysis: Margin intelligence, income coverage ratios
- Personal Knowledge Engine: Document ingestion, embedding, retrieval
- Decision Intelligence: Journal, calibration, bias detection
- Latency target: < 1s per analysis

### C Numerical Core (Primitives)
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

The server never sees plaintext financial data. Encryption and decryption happen exclusively in the client. The server stores and transmits only ciphertext.

This is enforced architecturally:
- No server-side function accepts plaintext financial data
- All API endpoints operate on encrypted payloads or derived analytics
- Key material (KEK) exists only in client memory during active sessions
- Argon2id with 600K iterations derives the KEK from the user's passphrase
