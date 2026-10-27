# PAE - Personal Analytics Engine

**Local-first, user-sovereign financial analytics — everything runs on your machine, under your control.**

PAE is an open-source tool that aggregates portfolio data, runs risk analytics, and surfaces decision intelligence on your own computer. There is no PAE cloud and no account: both processes run on loopback and your data lives in files under your data directory. Vault records (the decision journal and other versioned records) are encrypted in your browser before the local engine stores them, so for those the engine holds ciphertext only. The analytics working store — the holdings you import for analysis — is a local database file, unencrypted, like any local app's data file. The Security Model below states exactly what is encrypted and what is not.

> **PAE is an educational analytics tool, not a financial advisor.**
> This tool provides data, calculations, and educational content only. The user is responsible for all investment decisions.

## What PAE Does

- **Portfolio Aggregation**: Import holdings from CSV files (including IBKR Flex Query CSV exports whose headers match the IBKR layout) or manual entry. PAE has no live brokerage connections — imports are files you provide. Holdings can be recorded in multiple currencies (CAD/USD/INR); values are tracked per currency (no FX conversion engine yet).
- **Risk Engine**: VaR, CVaR, Sharpe, Sortino, volatility, drawdown, beta -- calculated in Rust (see docs/BENCHMARKS.md for measured timings).
- **Factor Decomposition**: Fama-French 5-Factor model via OLS. See exactly what drives your returns.
- **Monte Carlo Simulation**: 1K-100K path simulation with geometric Brownian motion. Stress test allocations.
- **Correlation Analysis**: Pairwise correlation matrices over holdings' return series, computed by the engine's REST API. Not yet surfaced as a dashboard view.
- **Stress Testing**: Apply historical scenarios (2008 GFC, 2020 COVID, rate shocks) to your current portfolio, with per-asset-class shocks.
- **Margin Intelligence**: Carry analysis for leveraged portfolios. Income coverage ratios, net carry, position-level carry attribution.
- **Decision Journal**: Log rationale, confidence, and emotional state before trades; entries are encrypted in your browser. (Calibration scoring exists in the analytics library and is not surfaced in the dashboard.)
- **Personal Knowledge Engine (PKE)**: Ingest your own research (Buffett letters, coursework, book notes) into a locally-indexed knowledge base on your own machine. Chunk text is encrypted at rest when notes are ingested with a data key through the PKE library; otherwise notes are stored unencrypted, and the dashboard's Knowledge view says so plainly when that is your configuration.

## What PAE Does Not Do

- **No investment advice.** Ever. The tool calculates; you decide.
- **No recommendations.** No "buy X" or "sell Y" outputs.
- **No personalized suggestions.** All parameters are user-specified.
- **No data exfiltration.** Passphrases never leave the client — key derivation runs client-side in the browser (`ui/src/crypto/vault-client.ts`, Argon2id via vendored hash-wasm); the server exposes no passphrase-derivation endpoint. The engine's vault store holds ciphertext only; its `/encrypt` and `/decrypt` endpoints are caller-supplied-key oracles for local-first deployments (see Security Model).

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

- **AES-256-GCM** encryption for vault records (decision journal and other versioned records), encrypted in the browser before the engine stores them
- **The analytics store is a local file, unencrypted**: imported holdings live in a SQLite database under your PAE data directory, readable by anyone who can read your files — the posture of any local app's data file. The knowledge base is the same unless its notes were ingested with a data key through the PKE library; the dashboard labels an unencrypted knowledge base plainly. Full-disk encryption is yours to enable.
- **Argon2id** key derivation (3 iterations, 64MB memory, 4 threads - RFC 9106 first-recommended profile)
- Per-vault Data Encryption Keys (DEKs) wrapped with user's Key Encryption Key (KEK) — random 32-byte DEK generated client-side, wrapped with the KEK via AES-256-GCM (WebCrypto); versioned v2 envelope stored opaquely server-side (`ui/src/crypto/vault-client.ts`, `GET/POST /api/v1/crypto/dek-envelope`); v1 vaults (direct-KEK) remain readable. Per-record DEKs remain a documented extension via the envelope's `kid` field.
- KEK derived client-side from passphrase — **never transmitted** (`ui/src/crypto/vault-client.ts`; `POST /api/v1/crypto/derive-key` was removed 2026-10-05 — no server-side derivation path exists)
- Optional **Shamir's Secret Sharing** (3-of-5) for key recovery — implemented client-side (`ui/src/crypto/shamir.ts`); the KEK never leaves the browser
- A reader of the engine's vault store gets ciphertext only; the local-first posture trusts your machine for the analytics files, and the threat model says so

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for the full threat model.

## Quick Start

```bash
# Clone
git clone https://github.com/nrupala/pae.git
cd pae

# Native self-host bundle — no Docker, no runtimes to install
# (see docs/SELF_HOST.md for the user guide, docs/PACKAGING.md
# for how the bundle is built):
scripts/build-bundle.sh
./dist-bundle/pae-*/install.sh   # install.ps1 on Windows
pae start

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
  scripts/         Native bundle build, installers, launcher, demo seed
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
- Designed with reference to FINRA 2214, CSA guidance, and SEC no-action letter precedents for analytical tools (design reference, not a compliance certification)

## License

Layered, per [LICENSING.md](LICENSING.md): the engine, analytics core and dashboard are [AGPL-3.0-or-later](LICENSES/AGPL-3.0.txt); the common/MCP-exposed surface is [Apache-2.0](LICENSES/Apache-2.0.txt); commercial licensing is available on both tracks.

## Author

PAE is a product of 2351485 ALBERTA INC. Created by Nrupal Akolkar.

Architecture and initial scaffolding by Milo (Town AI Assistant).
