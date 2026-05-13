# PAE Function Reference

> Auto-generated documentation for every public function in the PAE codebase.
> Covers the Rust engine (crypto, risk, API handlers, versioning) and the Python analytics layer.
>
> **Conventions used in this document:**
> - *Valid range* describes the domain of accepted inputs.
> - *Edge cases* describes boundary conditions the function handles gracefully.
> - *Error conditions* describes inputs that cause the function to return an error or raise an exception.
> - *Dependencies* lists other PAE functions called internally.

---

## Table of Contents

### Rust Engine (`engine/src/`)

1. [Crypto Vault](#1-crypto-vault)
2. [Crypto API Handlers](#2-crypto-api-handlers)
3. [Portfolio API Handlers](#3-portfolio-api-handlers)
4. [Risk Metrics](#4-risk-metrics)
5. [Monte Carlo Simulation](#5-monte-carlo-simulation)
6. [Stress Testing](#6-stress-testing)
7. [Correlation Matrix](#7-correlation-matrix)
8. [Versioning Types](#8-versioning-types)
9. [Version Store](#9-version-store)
10. [Snapshot Engine](#10-snapshot-engine)
11. [Versioning API Handlers](#11-versioning-api-handlers)
12. [Health Check](#12-health-check)

### Python Analytics (`analytics/pae/`)

13. [Factor Decomposition](#13-factor-decomposition)
14. [Carry Analysis](#14-carry-analysis)
15. [PKE Ingestion](#15-pke-ingestion)
16. [PKE Retrieval](#16-pke-retrieval)
17. [Decision Journal](#17-decision-journal)

---

## 1. Crypto Vault

**File:** `engine/src/crypto/vault.rs`

Provides zero-knowledge cryptographic primitives for PAE's encryption layer. All data is encrypted client-side before storage. The server never sees plaintext.

### `CryptoError` (enum)

Error type for all cryptographic operations.

| Variant | Description |
|---------|-------------|
| `InvalidBase64 { context }` | Input is not valid base64. `context` identifies which field. |
| `InvalidKeyLength` | Decoded key is not 32 bytes. |
| `InvalidSalt(String)` | Salt string is not valid base64 for Argon2. |
| `DerivationFailed(String)` | Argon2 hashing failed. |
| `EncryptionFailed(String)` | AES-GCM encryption failed. |
| `DecryptionFailed` | AES-GCM decryption failed (wrong key or tampered data). |
| `InvalidNonceLength(usize)` | Nonce is not 12 bytes. |
| `InvalidUtf8` | Decrypted bytes are not valid UTF-8. |
| `EmptyPassphrase` | Passphrase is empty string. |
| `InvalidParams(String)` | Argon2 parameters are invalid. |

---

### `derive_key`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn derive_key(passphrase: &str, existing_salt: Option<&str>) -> Result<(String, String), CryptoError>` |
| **Purpose** | Derive a 256-bit key from a passphrase using Argon2id with 600K iterations. |
| **Parameters** | `passphrase`: non-empty string. `existing_salt`: optional base64-encoded salt for deterministic re-derivation. |
| **Valid ranges** | `passphrase`: any non-empty `&str`. `existing_salt`: valid base64 or `None`. |
| **Returns** | `Ok((key_hash_b64, salt_b64))` on success. |
| **Error conditions** | `EmptyPassphrase` if passphrase is `""`. `InvalidSalt` if salt is not valid base64. `DerivationFailed` if Argon2 hashing fails. `InvalidParams` if Argon2 config is invalid. |
| **Edge cases** | `None` salt generates a new random salt via `OsRng`. Same passphrase + same salt produces identical output (deterministic). |
| **Dependencies** | `argon2::Argon2`, `SaltString`, `OsRng` |

---

### `encrypt`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn encrypt(plaintext: &str, key_b64: &str) -> Result<(String, String), CryptoError>` |
| **Purpose** | Encrypt plaintext with AES-256-GCM. |
| **Parameters** | `plaintext`: any string. `key_b64`: base64-encoded 32-byte key. |
| **Valid ranges** | `key_b64` must decode to exactly 32 bytes. |
| **Returns** | `Ok((ciphertext_b64, nonce_b64))` on success. |
| **Error conditions** | `InvalidBase64` if key is not valid base64. `InvalidKeyLength` if decoded key is not 32 bytes. `EncryptionFailed` if AES-GCM encryption fails. |
| **Edge cases** | Empty plaintext is valid (encrypts zero-length payload). Nonce is generated randomly via `OsRng` (12 bytes). |
| **Dependencies** | `Aes256Gcm`, `OsRng`, `base64` |

---

### `decrypt`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn decrypt(ciphertext_b64: &str, nonce_b64: &str, key_b64: &str) -> Result<String, CryptoError>` |
| **Purpose** | Decrypt ciphertext with AES-256-GCM. |
| **Parameters** | `ciphertext_b64`: base64-encoded ciphertext. `nonce_b64`: base64-encoded 12-byte nonce. `key_b64`: base64-encoded 32-byte key. |
| **Valid ranges** | All inputs must be valid base64. Nonce must decode to 12 bytes. Key must decode to 32 bytes. |
| **Returns** | `Ok(plaintext_string)` on success. |
| **Error conditions** | `InvalidBase64` if any input is not valid base64. `InvalidKeyLength` if key is not 32 bytes. `InvalidNonceLength(n)` if nonce is not 12 bytes. `DecryptionFailed` if authentication fails. `InvalidUtf8` if decrypted bytes are not valid UTF-8. |
| **Edge cases** | Wrong key produces `DecryptionFailed` (not garbage output). Tampered ciphertext produces `DecryptionFailed`. |
| **Dependencies** | `Aes256Gcm`, `base64` |

---

## 2. Crypto API Handlers

**File:** `engine/src/api/crypto_api.rs`

HTTP endpoints for the crypto vault. All handlers return `Result` with proper HTTP status codes.

### `derive_key` (handler)

| Field | Value |
|-------|-------|
| **Signature** | `pub async fn derive_key(Json(req): Json<DeriveKeyRequest>) -> Result<Json<DeriveKeyResponse>, (StatusCode, Json<CryptoErrorResponse>)>` |
| **Route** | `POST /api/v1/crypto/derive-key` |
| **Purpose** | HTTP wrapper for `vault::derive_key`. |
| **Request body** | `{ "passphrase": string, "salt": string | null }` |
| **Response** | `200 { "key_hash": string, "salt": string }` |
| **Error responses** | `400` for empty passphrase or invalid salt. `422` for derivation failure. `500` for invalid Argon2 params. |
| **Dependencies** | `vault::derive_key` |

### `encrypt` (handler)

| Field | Value |
|-------|-------|
| **Signature** | `pub async fn encrypt(Json(req): Json<EncryptRequest>) -> Result<Json<EncryptResponse>, (StatusCode, Json<CryptoErrorResponse>)>` |
| **Route** | `POST /api/v1/crypto/encrypt` |
| **Purpose** | HTTP wrapper for `vault::encrypt`. |
| **Request body** | `{ "plaintext": string, "key_b64": string }` |
| **Response** | `200 { "ciphertext_b64": string, "nonce_b64": string }` |
| **Error responses** | `400` for invalid base64 or wrong key length. `422` for encryption failure. |
| **Dependencies** | `vault::encrypt` |

### `decrypt` (handler)

| Field | Value |
|-------|-------|
| **Signature** | `pub async fn decrypt(Json(req): Json<DecryptRequest>) -> Result<Json<DecryptResponse>, (StatusCode, Json<CryptoErrorResponse>)>` |
| **Route** | `POST /api/v1/crypto/decrypt` |
| **Purpose** | HTTP wrapper for `vault::decrypt`. |
| **Request body** | `{ "ciphertext_b64": string, "nonce_b64": string, "key_b64": string }` |
| **Response** | `200 { "plaintext": string }` |
| **Error responses** | `400` for invalid base64 or wrong nonce/key length. `422` for decryption failure. |
| **Dependencies** | `vault::decrypt` |

---

## 3. Portfolio API Handlers

**File:** `engine/src/api/portfolio.rs`

All portfolio endpoints share a common `validate_holdings()` function and a `sanitize_f64()` output guard.

### `PortfolioError` (enum)

| Variant | HTTP Status | Description |
|---------|-------------|-------------|
| `EmptyHoldings` | 400 | Holdings array is empty. |
| `NegativeWeight { symbol }` | 400 | A holding has a negative weight. |
| `NegativeMarketValue { symbol }` | 400 | A holding has a negative or NaN/Inf market value. |
| `EmptySymbol` | 400 | A holding has an empty symbol string. |
| `NoReturnsData { symbol }` | 400 | A holding has an empty returns array. |
| `NanOrInfReturn { symbol, index }` | 400 | A return value is NaN or Infinity. |
| `NanOrInfWeight { symbol }` | 400 | A weight is NaN or Infinity. |
| `InvalidSimulationCount` | 400 | `num_simulations` is 0 or > 1,000,000. |
| `InvalidTimeHorizon` | 400 | `time_horizon_months` is 0 or > 600. |
| `NegativeInitialValue` | 400 | `initial_value` is negative or NaN/Inf. |
| `ZeroInitialValue` | 400 | `initial_value` is exactly 0.0. |
| `InvalidWindowDays` | 400 | `window_days` is < 2 or > 10,000. |
| `ProcessingError(msg)` | 422 | A computation error occurred after validation. |

### `validate_holdings`

| Field | Value |
|-------|-------|
| **Signature** | `fn validate_holdings(holdings: &[Holding]) -> Result<(), PortfolioError>` |
| **Purpose** | Validate all holdings before any computation. |
| **Checks** | Non-empty array, non-empty symbols, non-negative weights, non-negative market values, at least one return per holding, no NaN/Infinity in numeric fields. |
| **Dependencies** | None (pure validation) |

### `sanitize_f64`

| Field | Value |
|-------|-------|
| **Signature** | `fn sanitize_f64(val: f64) -> f64` |
| **Purpose** | Replace NaN/Infinity with 0.0 to prevent JSON serialization issues. |

### `compute_risk`

| Field | Value |
|-------|-------|
| **Signature** | `pub async fn compute_risk(Json(input): Json<PortfolioInput>) -> Result<Json<RiskResponse>, ...>` |
| **Route** | `POST /api/v1/portfolio/risk` |
| **Purpose** | Compute VaR (95/99), CVaR, Sharpe, Sortino, volatility, and max drawdown. |
| **Request body** | `{ "holdings": [Holding], "benchmark": string | null }` |
| **Response** | `200 { var_95, var_99, cvar_95, max_drawdown, beta, sharpe, sortino, volatility }` |
| **Error responses** | `400` for invalid holdings. `422` if all holdings have zero market value. |
| **Dependencies** | `validate_holdings`, `metrics::portfolio_returns`, `metrics::value_at_risk`, `metrics::conditional_var`, `metrics::max_drawdown`, `metrics::sharpe_ratio`, `metrics::sortino_ratio`, `metrics::volatility`, `sanitize_f64` |

### `compute_metrics`

| Field | Value |
|-------|-------|
| **Signature** | `pub async fn compute_metrics(Json(input): Json<PortfolioInput>) -> Result<Json<MetricsResponse>, ...>` |
| **Route** | `POST /api/v1/portfolio/metrics` |
| **Purpose** | Compute total return, annualized return, volatility, Sharpe, Sortino, max drawdown, win rate, and Calmar ratio. |
| **Response** | `200 { total_return, annualized_return, volatility, sharpe, sortino, max_drawdown, win_rate, calmar }` |
| **Error responses** | `400` for invalid holdings. `422` if all holdings have zero market value. |
| **Dependencies** | `validate_holdings`, `metrics::*`, `sanitize_f64` |

### `stress_test`

| Field | Value |
|-------|-------|
| **Signature** | `pub async fn stress_test(Json(input): Json<StressTestInput>) -> Result<Json<StressTestResponse>, ...>` |
| **Route** | `POST /api/v1/portfolio/stress` |
| **Purpose** | Run scenario-based stress test. |
| **Request body** | `{ "holdings": [Holding], "scenario": string, "custom_shocks": [AssetShock] | null }` |
| **Response** | `200 { scenario, portfolio_impact_pct, position_impacts: [{ symbol, impact_pct, impact_value }] }` |
| **Error responses** | `400` for invalid holdings, empty scenario, or invalid custom shock values. |
| **Dependencies** | `validate_holdings`, `stress::run_stress_test` |

### `correlation_matrix`

| Field | Value |
|-------|-------|
| **Signature** | `pub async fn correlation_matrix(Json(input): Json<CorrelationInput>) -> Result<Json<CorrelationResponse>, ...>` |
| **Route** | `POST /api/v1/portfolio/correlation` |
| **Purpose** | Compute pairwise Pearson correlation matrix. |
| **Request body** | `{ "holdings": [Holding], "window_days": usize | null }` |
| **Response** | `200 { symbols: [string], matrix: [[f64]], window_days: usize }` |
| **Error responses** | `400` for invalid holdings or `window_days` outside [2, 10000]. |
| **Dependencies** | `validate_holdings`, `correlation::compute_matrix` |

### `monte_carlo`

| Field | Value |
|-------|-------|
| **Signature** | `pub async fn monte_carlo(Json(input): Json<MonteCarloInput>) -> Result<Json<MonteCarloResponse>, ...>` |
| **Route** | `POST /api/v1/portfolio/montecarlo` |
| **Purpose** | Run Monte Carlo simulation. |
| **Request body** | `{ "holdings": [Holding], "num_simulations": usize | null, "time_horizon_months": usize | null, "initial_value": f64 }` |
| **Valid ranges** | `initial_value` > 0. `num_simulations` in [1, 1000000]. `time_horizon_months` in [1, 600]. |
| **Response** | `200 { percentiles: { p5, p25, p50, p75, p95 }, num_simulations, time_horizon_months, probability_of_loss }` |
| **Error responses** | `400` for invalid holdings, non-positive initial value, or out-of-range simulation parameters. |
| **Dependencies** | `validate_holdings`, `montecarlo::run_simulation` |

---

## 4. Risk Metrics

**File:** `engine/src/risk/metrics.rs`

Pure computation functions. No I/O. All functions handle empty/single-element inputs gracefully.

### `portfolio_returns`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn portfolio_returns(holdings: &[Holding]) -> Vec<f64>` |
| **Purpose** | Compute market-value-weighted portfolio returns. |
| **Parameters** | `holdings`: slice of `Holding` structs with `market_value` and `returns`. |
| **Returns** | Vec of weighted returns, length = min of all holdings' return lengths. |
| **Edge cases** | Empty holdings -> `vec![]`. Zero total market value -> `vec![]`. Single holding -> that holding's returns weighted by 1.0. |
| **Dependencies** | None |

### `volatility`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn volatility(returns: &[f64]) -> f64` |
| **Purpose** | Sample standard deviation of returns (Bessel's correction). |
| **Edge cases** | < 2 returns -> 0.0. All identical returns -> 0.0. |

### `sharpe_ratio`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn sharpe_ratio(returns: &[f64], risk_free_annual: f64) -> f64` |
| **Purpose** | `(mean - rf_period) / volatility`. Assumes monthly returns. |
| **Edge cases** | < 2 returns -> 0.0. Zero volatility -> 0.0 (avoids division by zero). |
| **Dependencies** | `volatility` |

### `sortino_ratio`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn sortino_ratio(returns: &[f64], risk_free_annual: f64) -> f64` |
| **Purpose** | `(mean - rf_period) / downside_deviation`. |
| **Edge cases** | < 2 returns -> 0.0. No downside returns -> 0.0 (returns 0.0, not Infinity). Zero downside deviation -> 0.0. |

### `value_at_risk`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn value_at_risk(returns: &[f64], alpha: f64) -> f64` |
| **Purpose** | Historical VaR at confidence level `1 - alpha`. Reported as positive number. |
| **Parameters** | `alpha`: 0.05 for 95% VaR, 0.01 for 99% VaR. |
| **Edge cases** | Empty returns -> 0.0. NaN values in sort handled with `unwrap_or(Equal)`. |

### `conditional_var`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn conditional_var(returns: &[f64], alpha: f64) -> f64` |
| **Purpose** | Average loss beyond VaR threshold (Expected Shortfall). |
| **Edge cases** | Empty returns -> 0.0. Cutoff rounds to zero -> uses at least 1 observation. |

### `max_drawdown`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn max_drawdown(returns: &[f64]) -> f64` |
| **Purpose** | Largest peak-to-trough decline in cumulative returns. |
| **Edge cases** | Empty returns -> 0.0. Monotonically increasing -> 0.0. Guards against zero peak. |

### `total_return`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn total_return(returns: &[f64]) -> f64` |
| **Purpose** | Cumulative compounded return: `product(1 + r_i) - 1`. |
| **Edge cases** | Empty returns -> 0.0 (product of empty = 1.0, minus 1 = 0.0). |

### `annualized_return`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn annualized_return(returns: &[f64], periods_per_year: usize) -> f64` |
| **Purpose** | `(1 + total_return)^(1/years) - 1`. |
| **Edge cases** | Empty returns -> 0.0. `periods_per_year == 0` -> 0.0 (avoids division by zero). |
| **Dependencies** | `total_return` |

### `win_rate`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn win_rate(returns: &[f64]) -> f64` |
| **Purpose** | Fraction of positive return periods. |
| **Edge cases** | Empty returns -> 0.0. |

### `calmar_ratio`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn calmar_ratio(returns: &[f64], periods_per_year: usize) -> f64` |
| **Purpose** | `annualized_return / max_drawdown`. |
| **Edge cases** | Zero max drawdown -> 0.0 (avoids division by zero). |
| **Dependencies** | `annualized_return`, `max_drawdown` |

---

## 5. Monte Carlo Simulation

**File:** `engine/src/risk/montecarlo.rs`

### Constants

| Name | Value | Purpose |
|------|-------|---------|
| `MAX_SIMULATIONS` | 1,000,000 | Prevents resource exhaustion. |
| `MAX_HORIZON_MONTHS` | 600 | 50-year cap on projection horizon. |

### `run_simulation`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn run_simulation(input: &MonteCarloInput) -> MonteCarloResponse` |
| **Purpose** | Run geometric Brownian motion Monte Carlo simulation. |
| **Parameters** | `input.holdings`, `input.num_simulations` (default 10000), `input.time_horizon_months` (default 120), `input.initial_value`. |
| **Valid ranges** | `num_simulations`: clamped to [1, 1000000]. `time_horizon_months`: clamped to [1, 600]. `initial_value`: falls back to 1.0 if NaN/Inf/non-positive. |
| **Returns** | Percentile paths (p5/p25/p50/p75/p95), simulation count, horizon, probability of loss. |
| **Edge cases** | Empty holdings -> zero mean/std, paths stay near initial value. Single observation -> zero variance. Extreme returns clamped to [-0.99, 10.0] to prevent Infinity overflow. |
| **Dependencies** | `percentile`, `sample_standard_normal` |

### `percentile`

| Field | Value |
|-------|-------|
| **Signature** | `fn percentile(sorted: &[f64], p: f64) -> f64` |
| **Purpose** | Compute p-th percentile from a sorted slice. |
| **Edge cases** | Empty slice -> 0.0. Single element -> that element. |

### `sample_standard_normal`

| Field | Value |
|-------|-------|
| **Signature** | `fn sample_standard_normal(rng: &mut impl Rng) -> f64` |
| **Purpose** | Box-Muller transform for N(0,1) sampling. |
| **Edge cases** | Clamps u1 to `max(u1, 1e-15)` to avoid `ln(0) = -Infinity`. |

---

## 6. Stress Testing

**File:** `engine/src/risk/stress.rs`

### `get_scenario_shocks`

| Field | Value |
|-------|-------|
| **Signature** | `fn get_scenario_shocks(name: &str) -> Vec<(&'static str, f64)>` |
| **Purpose** | Return historical shock profiles by scenario name. |
| **Supported scenarios** | `gfc_2008`, `covid_2020`, `rate_shock_2022`, `dotcom_2000`, `stagflation_1970s`, `black_monday_1987`, `oil_shock_2020`. Unknown names -> moderate default profile. |

### `classify_asset`

| Field | Value |
|-------|-------|
| **Signature** | `fn classify_asset(_symbol: &str) -> &'static str` |
| **Purpose** | Classify a holding into an asset class. Stub: always returns `"equity"`. |

### `run_stress_test`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn run_stress_test(input: &StressTestInput) -> StressTestResponse` |
| **Purpose** | Apply scenario shocks to portfolio holdings. |
| **Edge cases** | Empty holdings -> zero impact. Zero total market value -> `portfolio_impact_pct = 0.0`. NaN/Infinity in `shock_pct` -> sanitized to 0.0. NaN/Infinity in `impact_value` -> sanitized to 0.0. Unknown asset class -> default -10% shock. |
| **Dependencies** | `get_scenario_shocks`, `classify_asset` |

---

## 7. Correlation Matrix

**File:** `engine/src/risk/correlation.rs`

### `compute_matrix`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn compute_matrix(input: &CorrelationInput) -> CorrelationResponse` |
| **Purpose** | Build NxN pairwise Pearson correlation matrix. |
| **Parameters** | `input.holdings`, `input.window_days` (default 90, min 2). |
| **Edge cases** | Empty holdings -> empty matrix. Single holding -> `[[1.0]]`. NaN/Infinity in returns -> 0.0 correlation for that pair. |
| **Dependencies** | `pearson_correlation`, `clamp_correlation` |

### `clamp_correlation`

| Field | Value |
|-------|-------|
| **Signature** | `fn clamp_correlation(corr: f64) -> f64` |
| **Purpose** | Clamp to [-1.0, 1.0]. NaN/Infinity -> 0.0. |

### `pearson_correlation`

| Field | Value |
|-------|-------|
| **Signature** | `fn pearson_correlation(x: &[f64], y: &[f64], window: usize) -> f64` |
| **Purpose** | Pearson r over the last `window` observations. |
| **Edge cases** | < 2 observations -> 0.0. Zero variance in either series -> 0.0. NaN/Infinity in either series -> 0.0. |

---

## 8. Versioning Types

**File:** `engine/src/versioning/types.rs`

### `VersionedRecord` (struct)

Content-addressed, append-only version record. Fields: `version_hash`, `entity_id`, `entity_type`, `version`, `author`, `created_at`, `content_encrypted`, `nonce`, `metadata`, `parent_hash`.

### `VersionedRecord::compute_hash`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn compute_hash(entity_id: &str, version: u64, content: &[u8]) -> String` |
| **Purpose** | SHA-256 content-addressed hash for tamper evidence. |
| **Returns** | Hex-encoded SHA-256 hash string. |

### `VersionedRecord::verify_integrity`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn verify_integrity(&self) -> bool` |
| **Purpose** | Verify `version_hash` matches recomputed hash of current fields. |

### `EntityType` (enum)

Variants: `Holdings`, `Position`, `DecisionEntry`, `CalibrationRecord`, `KnowledgeChunk`, `KnowledgeAnnotation`, `Configuration`, `StressTestConfig`, `MonteCarloConfig`, `CarrySnapshot`.

### `VersionAuthor` (enum)

Variants: `User`, `System`, `DataFeed(String)`.

---

## 9. Version Store

**File:** `engine/src/versioning/store.rs`

In-memory append-only version store with `RwLock`. Production target: SQLite with WAL.

### `VersionStoreError` (enum)

| Variant | Description |
|---------|-------------|
| `LockFailed` | Failed to acquire RwLock. |
| `NotFound(String)` | Entity ID not found. |
| `IntegrityFailed(String)` | Chain integrity check failed. |

### `VersionStore::new`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn new() -> Self` |
| **Purpose** | Create an empty in-memory version store. |

### `VersionStore::append`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn append(&self, entity_id, entity_type, content_encrypted, nonce, author, change_summary, tags) -> Result<String, VersionStoreError>` |
| **Purpose** | Append a new version. Returns version hash. |
| **Error conditions** | `LockFailed` if RwLock is poisoned. |

### `VersionStore::get_latest`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn get_latest(&self, entity_id: &str) -> Result<Option<VersionedRecord>, VersionStoreError>` |
| **Purpose** | Get the most recent version of an entity. |

### `VersionStore::get_by_hash`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn get_by_hash(&self, version_hash: &str) -> Result<Option<VersionedRecord>, VersionStoreError>` |
| **Purpose** | Look up a specific version by its content-addressed hash. |

### `VersionStore::query`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn query(&self, query: &VersionQuery) -> Result<Vec<VersionedRecord>, VersionStoreError>` |
| **Purpose** | Query version history with optional date range, limit, and latest-only filters. |

### `VersionStore::total_versions`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn total_versions(&self) -> Result<usize, VersionStoreError>` |
| **Purpose** | Count total versions across all entities. |

### `VersionStore::verify_chain`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn verify_chain(&self, entity_id: &str) -> Result<bool, VersionStoreError>` |
| **Purpose** | Verify integrity of the full version chain for an entity. Checks content hashes and parent linkage. |
| **Edge cases** | Non-existent entity -> `Ok(true)` (no chain to violate). |

---

## 10. Snapshot Engine

**File:** `engine/src/versioning/snapshot.rs`

Point-in-time portfolio state reconstruction. Currently stub implementations.

### `SnapshotEngine::snapshot_at`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn snapshot_at(&self, _query: &SnapshotQuery) -> Result<Vec<VersionedRecord>, VersionStoreError>` |
| **Purpose** | Reconstruct state of all entities at a specific timestamp. |
| **Status** | Stub: returns `Ok(vec![])`. Production: SQL query with GROUP BY + MAX(version). |

### `SnapshotEngine::diff`

| Field | Value |
|-------|-------|
| **Signature** | `pub fn diff(&self, from, to, _entity_types) -> Result<SnapshotDiff, VersionStoreError>` |
| **Purpose** | Compare two snapshots. Returns added, removed, and modified entities. |
| **Status** | Stub: returns empty diff. |

---

## 11. Versioning API Handlers

**File:** `engine/src/api/versioning_api.rs`

Stub HTTP handlers for the versioning system. All return placeholder responses.

| Handler | Route | Purpose |
|---------|-------|---------|
| `append_version` | `POST /api/v1/version` | Append a new version for an entity. |
| `get_history` | `POST /api/v1/version/history` | Get version history for an entity. |
| `get_snapshot` | `POST /api/v1/version/snapshot` | Get point-in-time snapshot. |
| `verify_integrity` | `GET /api/v1/version/integrity/:entity_id` | Verify chain integrity. |

---

## 12. Health Check

**File:** `engine/src/api/health.rs`

### `check`

| Field | Value |
|-------|-------|
| **Signature** | `pub async fn check() -> Json<HealthResponse>` |
| **Route** | `GET /health` |
| **Purpose** | Health/readiness probe. Returns engine status, version (from Cargo.toml), and engine name. |

---

## 13. Factor Decomposition

**File:** `analytics/pae/models/factor.py`

### `FactorError` (exception)

Raised when factor decomposition fails due to singular matrix or numerical instability.

### `_validate_returns`

| Field | Value |
|-------|-------|
| **Signature** | `def _validate_returns(portfolio_returns, factor_returns) -> None` |
| **Purpose** | Validate inputs before regression. |
| **Raises** | `ValueError` if: empty arrays, fewer than k+2 observations, mismatched lengths, NaN/Infinity values, no factors. |

### `decompose`

| Field | Value |
|-------|-------|
| **Signature** | `def decompose(portfolio_returns: NDArray, factor_returns: dict[str, NDArray]) -> FactorDecomposition` |
| **Purpose** | OLS regression of portfolio returns on Fama-French factors. Returns alpha, R-squared, factor exposures, and residual risk. |
| **Parameters** | `portfolio_returns`: 1D array. `factor_returns`: dict mapping factor name to 1D array (same length). |
| **Valid ranges** | Need >= k+2 observations. All values must be finite. |
| **Returns** | `FactorDecomposition(alpha, alpha_t_stat, r_squared, exposures, residual_risk_pct)` |
| **Error conditions** | `ValueError` from validation. `FactorError` if factor matrix is singular (collinear factors) or regression produces NaN/Infinity coefficients. |
| **Edge cases** | Near-singular matrices: caught via NaN check on betas. Negative covariance diagonal: guarded with `np.maximum(diag, 0.0)`. Zero portfolio variance: contribution_pct = 0.0. |
| **Dependencies** | `numpy.linalg.inv`, `_validate_returns` |

---

## 14. Carry Analysis

**File:** `analytics/pae/models/carry.py`

### `CarryError` (exception)

Raised when carry analysis encounters invalid inputs.

### `_validate_holdings`

| Field | Value |
|-------|-------|
| **Signature** | `def _validate_holdings(holdings: list[dict]) -> None` |
| **Raises** | `ValueError` if: empty list, missing symbol/market_value, negative market_value, NaN/Infinity in market_value or yield_pct. |

### `_validate_margin_params`

| Field | Value |
|-------|-------|
| **Signature** | `def _validate_margin_params(total_margin: float, margin_rate: float) -> None` |
| **Raises** | `ValueError` if: negative, NaN, or Infinity values. |

### `analyze_carry`

| Field | Value |
|-------|-------|
| **Signature** | `def analyze_carry(holdings, total_margin, margin_rate=0.058) -> PortfolioCarry` |
| **Purpose** | Compute income vs. margin cost for each position and the portfolio. |
| **Parameters** | `holdings`: list of dicts with `symbol`, `market_value`, optional `yield_pct`. `total_margin`: borrowed amount. `margin_rate`: annual rate (default 5.8%). |
| **Valid ranges** | All numeric values non-negative and finite. |
| **Returns** | `PortfolioCarry` with per-position and aggregate metrics. |
| **Error conditions** | `ValueError` from validation. |
| **Edge cases** | `total_long == 0` -> margin_share = 0. `total_nav <= 0` -> leverage = 0, margin_pct = 0. `total_margin_cost == 0` -> coverage = `inf` if income > 0, else 0.0. `yield_pct = None` -> treated as 0.0. |
| **Dependencies** | `_validate_holdings`, `_validate_margin_params` |

---

## 15. PKE Ingestion

**File:** `analytics/pae/pke/ingest.py`

### Constants

| Name | Value | Purpose |
|------|-------|---------|
| `MAX_FILE_SIZE_BYTES` | 10,485,760 (10 MB) | Prevents memory exhaustion on large files. |
| `MIN_CHUNK_WORDS` | 10 | Minimum word count to keep a chunk. |
| `THEMES` | 10 theme strings | Valid classification categories. |

### `parse_frontmatter`

| Field | Value |
|-------|-------|
| **Signature** | `def parse_frontmatter(text: str) -> tuple[dict, str]` |
| **Purpose** | Extract YAML frontmatter from `---` delimiters. |
| **Returns** | `(metadata_dict, body_text)`. No frontmatter -> `({}, original_text)`. |

### `chunk_text`

| Field | Value |
|-------|-------|
| **Signature** | `def chunk_text(text: str, max_tokens: int = 400) -> list[str]` |
| **Purpose** | Split text at paragraph boundaries, respecting max word count. |
| **Valid ranges** | `max_tokens >= 1`. |
| **Error conditions** | `ValueError` if `max_tokens < 1`. |
| **Edge cases** | Empty/whitespace text -> `[]`. Oversized paragraphs split at sentence boundaries. |

### `generate_chunk_id`

| Field | Value |
|-------|-------|
| **Signature** | `def generate_chunk_id(source: str, text: str) -> str` |
| **Purpose** | Deterministic SHA-256 chunk ID (16 hex chars). |

### `classify_themes`

| Field | Value |
|-------|-------|
| **Signature** | `def classify_themes(text: str) -> list[str]` |
| **Purpose** | Keyword-based theme classification (stub for zero-shot classifier). |
| **Returns** | List of matching themes, or `["general"]` if none match. |

### `ingest_markdown`

| Field | Value |
|-------|-------|
| **Signature** | `def ingest_markdown(file_path: Path) -> IngestionResult` |
| **Purpose** | Read a Markdown file, chunk it, classify themes, return structured result. |
| **Error handling** | `FileNotFoundError`, `PermissionError`, `UnicodeDecodeError`, `OSError`: all captured in `errors` list (not raised). Files > `MAX_FILE_SIZE_BYTES` rejected. Empty files reported as error. |
| **Dependencies** | `parse_frontmatter`, `chunk_text`, `generate_chunk_id`, `classify_themes` |

### `ingest_directory`

| Field | Value |
|-------|-------|
| **Signature** | `def ingest_directory(dir_path: Path) -> list[IngestionResult]` |
| **Purpose** | Recursively ingest all `*.md` files in a directory. |
| **Error conditions** | `ValueError` if `dir_path` does not exist or is not a directory. |
| **Edge cases** | Individual file errors captured per-result; do not abort other files. Directory glob errors logged and return empty list. |
| **Dependencies** | `ingest_markdown` |

---

## 16. PKE Retrieval

**File:** `analytics/pae/pke/retrieve.py`

### `retrieve_by_theme`

| Field | Value |
|-------|-------|
| **Signature** | `def retrieve_by_theme(theme: str, top_k: int = 5) -> list[RetrievalResult]` |
| **Purpose** | Retrieve top-k passages matching a theme. |
| **Status** | Stub: returns `[]`. Production: sqlite-vec vector similarity search. |

### `retrieve_by_context`

| Field | Value |
|-------|-------|
| **Signature** | `def retrieve_by_context(context_text: str, themes: list[str] | None = None, top_k: int = 5) -> list[RetrievalResult]` |
| **Purpose** | Retrieve passages relevant to an analytical context. |
| **Status** | Stub: returns `[]`. Production: semantic search. |

### `ANALYTICAL_CONTEXT_THEMES` (dict)

Maps analytical contexts (e.g. `"monte_carlo"`, `"stress_test"`) to relevant PKE themes for automatic knowledge surfacing.

---

## 17. Decision Journal

**File:** `analytics/pae/decision/journal.py`

### Constants

| Name | Value | Purpose |
|------|-------|---------|
| `CONFIDENCE_MIN` | 1 | Minimum valid confidence score. |
| `CONFIDENCE_MAX` | 10 | Maximum valid confidence score. |
| `_VALID_EMOTIONAL_STATES` | frozenset of 7 values | Valid emotional state strings. |

### `EmotionalState` (enum)

Values: `calm`, `anxious`, `excited`, `fearful`, `confident`, `uncertain`, `neutral`.

### `validate_entry`

| Field | Value |
|-------|-------|
| **Signature** | `def validate_entry(entry: DecisionEntry) -> list[str]` |
| **Purpose** | Validate a journal entry for data integrity. |
| **Checks** | Confidence in [1, 10] and is int. Emotional state is valid enum value. `max_acceptable_loss_pct` is non-negative and finite. Outcome values are finite or None. |
| **Returns** | List of error strings. Empty list = valid. |

### `compute_calibration`

| Field | Value |
|-------|-------|
| **Signature** | `def compute_calibration(entries: list[DecisionEntry]) -> list[CalibrationMetric]` |
| **Purpose** | Compare stated confidence against actual 90-day outcomes. Groups into buckets: high (8-10), medium (5-7), low (1-4). |
| **Parameters** | `entries`: list of `DecisionEntry`. Entries without `outcome_90d` are skipped. |
| **Returns** | List of 3 `CalibrationMetric` objects. Zero-decision buckets show 0.0% accuracy. |
| **Error conditions** | `TypeError` if `entries` is not a list. |
| **Edge cases** | Invalid confidence or non-finite outcomes silently skipped. Division by zero on empty buckets -> 0.0. |

---

## Appendix: Error Response Format

All API endpoints return errors in this format:

```json
{
  "error": "Human-readable error message",
  "code": "MACHINE_READABLE_ERROR_CODE"
}
```

### HTTP Status Code Mapping

| Status | Meaning | When |
|--------|---------|------|
| 400 | Bad Request | Input validation failures (empty holdings, negative values, NaN, invalid base64) |
| 422 | Unprocessable Entity | Processing failures (decryption failed, singular matrix, zero market value) |
| 500 | Internal Server Error | Unexpected internal errors (invalid Argon2 params) |

---

*Generated as part of PAE code audit. Every function, error type, edge case, and dependency documented.*
