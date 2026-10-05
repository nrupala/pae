# PAE - Personal Analytics Engine

**Zero-knowledge, zero-trust, user-sovereign financial analytics platform.**

PAE is an open-source tool that aggregates portfolio data, runs risk analytics, and surfaces decision intelligence -- all without ever seeing your financial data. The server stores only ciphertext. You hold the keys.

> **PAE is an educational analytics tool, not a financial advisor.**
> This tool provides data, calculations, and educational content only. The user is responsible for all investment decisions.

## What PAE Does

- **Portfolio Aggregation**: Import holdings from CSV, brokerage APIs (IBKR Flex Query, Plaid), or manual entry. Multi-currency (CAD/USD/INR).
- **Risk Engine**: VaR, CVaR, Sharpe, Sortino, volatility, drawdown, beta -- calculated in Rust for sub-millisecond latency.
- **Factor Decomposition**: Fama-French 5-Factor model via OLS. See exactly what drives your returns.
- **Monte Carlo Simulation**: 1K-100K path simulation with geometric Brownian motion. Stress test allocations.
- **Correlation Analysis**: Rolling correlation matrices across holdings. Spot hidden concentration risk.
- **Stress Testing**: Apply historical scenarios (2008 GFC, 2020 COVID, rate shocks) to your current portfolio.
- **Margin Intelligence**: Carry analysis for leveraged portfolios. Income coverage ratios, net carry, position-level carry attribution.
- **Decision Journal**: Log rationale, confidence, and emotional state before trades. Track calibration over time.
- **Personal Knowledge Engine (PKE)**: Ingest your own research (Buffett letters, coursework, book notes) into a private, encrypted, locally-indexed knowledge base.

## What PAE Does Not Do

- **No investment advice.** Ever. The tool calculates; you decide.
- **No recommendations.** No "buy X" or "sell Y" outputs.
- **No personalized suggestions.** All parameters are user-specified.
- **No data exfiltration.** Passphrases never leave the client — key derivation runs client-side in the browser (`ui/src/crypto/vault-client.ts`, Argon2id via vendored hash-wasm); the server exposes no passphrase-derivation endpoint. The engine stores ciphertext only; its `/encrypt` and `/decrypt` endpoints are caller-supplied-key oracles for local-first deployments (see Security Model).

## Architecture

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
                          | Bond analytics|
                          +-------+-------+
```

Three languages, each doing what it does best:
- **Rust** (hot path): Risk calculations, Monte Carlo, crypto vault, API server
- **Python** (research layer): Factor models, portfolio optimization (max-Sharpe, min-variance, and risk-parity mixes + efficient frontier), PKE, decision intelligence
- **C** (primitives): BLAS/LAPACK matrix ops and bond analytics — `engine/c/pae_num.c` (covariance via `dgemm_`, Cholesky via `dpotrf_`, symmetric eigendecomposition via `dsyev_`, exposed through safe Rust FFI in `engine/src/num_ffi.rs`; the correlation matrix is computed through this path by default) and `engine/c/pae_bonds.c` (discount-curve NPV, yield-to-maturity, duration, convexity — standard bond mathematics following QuantLib's bond methodology; served at `POST /api/v1/analytics/bond`). Linking the full QuantLib C++ library was probed and deliberately not taken: ~436k LOC / ~2,400 files plus a Boost dependency and hours-long builds, for analytics the engine needs in ~200 lines of standard math (probe: QuantLib is modified-BSD, license-compatible with AGPL-3.0, but disproportionate here).
- **Vanilla TypeScript** (presentation): Web Components, Canvas/SVG charts, < 200KB total

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full details.

## Security Model

- **AES-256-GCM** encryption for all stored data
- **Argon2id** key derivation (600K iterations, 64MB memory, 4 threads)
- Per-vault Data Encryption Keys (DEKs) wrapped with user's Key Encryption Key (KEK) — random 32-byte DEK generated client-side, wrapped with the KEK via AES-256-GCM (WebCrypto); versioned v2 envelope stored opaquely server-side (`ui/src/crypto/vault-client.ts`, `GET/POST /api/v1/crypto/dek-envelope`); v1 vaults (direct-KEK) remain readable. Per-record DEKs remain a documented extension via the envelope's `kid` field.
- KEK derived client-side from passphrase — **never transmitted** (`ui/src/crypto/vault-client.ts`; `POST /api/v1/crypto/derive-key` was removed 2026-10-05 — no server-side derivation path exists)
- Optional **Shamir's Secret Sharing** (3-of-5) for key recovery — implemented client-side (`ui/src/crypto/shamir.ts`); the KEK never leaves the browser
- Server compromise yields ciphertext only

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for the full threat model.

## Quick Start

```bash
# Clone
git clone https://github.com/nrupala/pae.git
cd pae

# Docker (recommended)
docker compose -f infra/docker/docker-compose.yml up

# Or build individually:

# Rust engine
cd engine && cargo build --release && cargo test

# Python analytics
cd analytics && pip install -e ".[dev]" && pytest tests/ -v

# UI
cd ui && npx tsc
```

## Project Structure

```
pae/
  .editorconfig, .gitignore, LICENSE, Makefile, README.md, CONTRIBUTING.md
  engine/          Rust risk engine (Cargo.toml, Axum API, crypto vault, risk modules)
  analytics/       Python analytics (factor models, carry analysis, PKE, decision journal)
  ui/              Vanilla TypeScript dashboard (Web Components, CSS tokens, Canvas charts)
  infra/           Docker, docker-compose, CI/CD
  docs/            Architecture, threat model, methodology
  knowledge/       PKE templates and content directory (content/ is gitignored)
```

## Development

```bash
# Full build + test
make build
make test

# Lint
make lint

# Clean
make clean
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development standards and the non-advisory guardrail checklist.

## Regulatory Position

PAE operates within educational/analytical safe harbor:
- **Tool calculates, user decides** -- the core architectural principle
- Non-dismissible disclaimer on every page
- No default parameters that imply advice
- No output labeled "optimal" or "recommended"
- All inputs start blank or neutral
- Compliant with FINRA 2214, CSA guidance, and SEC no-action letter precedents for analytical tools

## License

[AGPL-3.0](LICENSE)

## Author

Nrupal Akolkar ([nrupalakolkar@gmail.com](mailto:nrupalakolkar@gmail.com))

Architecture and initial scaffolding by Milo (Town AI Assistant).
