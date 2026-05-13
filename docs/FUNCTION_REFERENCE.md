# PAE Function Reference

> Complete API and function documentation for the Personal Analytics Engine.
> Generated from source code audit. Every public function, struct, enum, and API endpoint is documented.

---

## Table of Contents

- [Rust Engine](#rust-engine)
  - [API Layer](#api-layer)
    - [Health Check](#health-check)
    - [Portfolio Risk](#portfolio-risk)
    - [Portfolio Metrics](#portfolio-metrics)
    - [Stress Testing](#stress-testing)
    - [Correlation Matrix](#correlation-matrix)
    - [Monte Carlo Simulation](#monte-carlo-simulation)
    - [Crypto: Key Derivation](#crypto-key-derivation)
    - [Crypto: Encrypt](#crypto-encrypt)
    - [Crypto: Decrypt](#crypto-decrypt)
    - [Versioning: Append](#versioning-append)
    - [Versioning: History](#versioning-history)
    - [Versioning: Snapshot](#versioning-snapshot)
    - [Versioning: Integrity](#versioning-integrity)
  - [Risk Module](#risk-module)
    - [portfolio_returns](#portfolio_returns)
    - [volatility](#volatility)
    - [sharpe_ratio](#sharpe_ratio)
    - [sortino_ratio](#sortino_ratio)
    - [value_at_risk](#value_at_risk)
    - [conditional_var](#conditional_var)
    - [max_drawdown](#max_drawdown)
    - [total_return](#total_return)
    - [annualized_return](#annualized_return)
    - [win_rate](#win_rate)
    - [calmar_ratio](#calmar_ratio)
    - [pearson_correlation](#pearson_correlation)
    - [compute_matrix](#compute_matrix)
    - [run_stress_test](#run_stress_test)
    - [get_scenario_shocks](#get_scenario_shocks)
    - [classify_asset](#classify_asset)
    - [run_simulation (Monte Carlo)](#run_simulation-monte-carlo)
    - [percentile](#percentile)
    - [sample_standard_normal](#sample_standard_normal)
  - [Crypto Module](#crypto-module)
    - [derive_key](#derive_key)
    - [encrypt](#encrypt)
    - [decrypt](#decrypt)
    - [CryptoError](#cryptoerror)
  - [Versioning Module](#versioning-module)
    - [VersionedRecord](#versionedrecord)
    - [VersionStore](#versionstore)
    - [SnapshotEngine](#snapshotengine)
  - [Data Types (Rust)](#data-types-rust)
- [Python Analytics](#python-analytics)
  - [Factor Decomposition](#factor-decomposition)
    - [decompose](#decompose)
    - [FactorExposure](#factorexposure)
    - [FactorDecomposition](#factordecomposition)
  - [Carry Analysis](#carry-analysis)
    - [analyze_carry](#analyze_carry)
    - [PositionCarry](#positioncarry)
    - [PortfolioCarry](#portfoliocarry)
  - [Decision Journal](#decision-journal)
    - [DecisionEntry](#decisionentry)
    - [EmotionalState](#emotionalstate)
    - [compute_calibration](#compute_calibration)
    - [CalibrationMetric](#calibrationmetric)
  - [PKE Ingestion](#pke-ingestion)
    - [ingest_markdown](#ingest_markdown)
    - [ingest_directory](#ingest_directory)
    - [chunk_text](#chunk_text)
    - [parse_frontmatter](#parse_frontmatter)
    - [generate_chunk_id](#generate_chunk_id)
    - [classify_themes](#classify_themes)
    - [KnowledgeChunk](#knowledgechunk)
    - [IngestionResult](#ingestionresult)
  - [PKE Retrieval](#pke-retrieval)
    - [retrieve_by_theme](#retrieve_by_theme)
    - [retrieve_by_context](#retrieve_by_context)
    - [RetrievalResult](#retrievalresult)
- [TypeScript UI](#typescript-ui)
  - [PaeApp](#paeapp)
  - [PaeDashboard](#paedashboard)
  - [PaeChart](#paechart)
  - [PaeDisclaimer](#paedisclaimer)

---

## Rust Engine

### API Layer

All API endpoints accept JSON payloads and return JSON responses.
Error responses use the format `{ "error": "message", "code": "ERROR_CODE" }` with appropriate HTTP status codes.

---

#### Health Check

| Property | Value |
|---|---|
| **Endpoint** | `GET /health` |
| **File** | `engine/src/api/health.rs` |
| **Handler** | `check()` |

**Response:**

```json
{
  "status": "ok",
  "version": "0.1.0",
  "engine": "pae-engine"
}
```

**Error Conditions:** None (always returns 200).

---

#### Portfolio Risk

| Property | Value |
|---|---|
| **Endpoint** | `POST /api/v1/portfolio/risk` |
| **File** | `engine/src/api/portfolio.rs` |
| **Handler** | `compute_risk(Json<PortfolioInput>)` |

**Request Body (`PortfolioInput`):**

| Field | Type | Required | Valid Range | Description |
|---|---|---|---|---|
| `holdings` | `Holding[]` | Yes | >= 1 element | Portfolio holdings |
| `benchmark` | `string?` | No | -- | Benchmark symbol (future use) |

**Holding:**

| Field | Type | Required | Valid Range | Description |
|---|---|---|---|---|
| `symbol` | `string` | Yes | Non-empty | Ticker symbol |
| `weight` | `f64` | Yes | -- | Portfolio weight |
| `returns` | `f64[]` | Yes | >= 1, all finite | Historical period returns |
| `yield_pct` | `f64?` | No | -- | Annual yield percentage |
| `cost_basis` | `f64?` | No | -- | Cost basis |
| `market_value` | `f64` | Yes | >= 0, finite | Current market value |

**Response (`RiskResponse`):**

| Field | Type | Description |
|---|---|---|
| `var_95` | `f64` | 95% Value at Risk (positive = loss) |
| `var_99` | `f64` | 99% Value at Risk (positive = loss) |
| `cvar_95` | `f64` | 95% Conditional VaR (Expected Shortfall) |
| `max_drawdown` | `f64` | Maximum peak-to-trough drawdown [0, 1] |
| `beta` | `f64?` | Portfolio beta (null without benchmark) |
| `sharpe` | `f64` | Annualized Sharpe ratio |
| `sortino` | `f64` | Annualized Sortino ratio |
| `volatility` | `f64` | Annualized volatility |

**Error Responses:**

| HTTP Status | Code | Condition |
|---|---|---|
| 400 | `EMPTY_HOLDINGS` | Holdings array is empty |
| 400 | `EMPTY_SYMBOL` | A holding has an empty symbol |
| 400 | `NEGATIVE_MARKET_VALUE` | A holding has negative market value |
| 400 | `INVALID_MARKET_VALUE` | A holding has NaN or Infinity market value |
| 400 | `EMPTY_RETURNS` | A holding has no return data |
| 400 | `INVALID_RETURN` | A return value is NaN or Infinity |
| 400 | `ZERO_TOTAL_VALUE` | Total portfolio value is zero or negative |

**Dependencies:** `metrics::portfolio_returns`, `metrics::value_at_risk`, `metrics::conditional_var`, `metrics::max_drawdown`, `metrics::sharpe_ratio`, `metrics::sortino_ratio`, `metrics::volatility`.

---

#### Portfolio Metrics

| Property | Value |
|---|---|
| **Endpoint** | `POST /api/v1/portfolio/metrics` |
| **File** | `engine/src/api/portfolio.rs` |
| **Handler** | `compute_metrics(Json<PortfolioInput>)` |

**Request:** Same as Portfolio Risk (`PortfolioInput`).

**Response (`MetricsResponse`):**

| Field | Type | Description |
|---|---|---|
| `total_return` | `f64` | Cumulative return over all periods |
| `annualized_return` | `f64` | Annualized return (assuming 12 periods/year) |
| `volatility` | `f64` | Annualized volatility |
| `sharpe` | `f64` | Annualized Sharpe ratio |
| `sortino` | `f64` | Annualized Sortino ratio |
| `max_drawdown` | `f64` | Maximum drawdown [0, 1] |
| `win_rate` | `f64` | Fraction of positive return periods [0, 1] |
| `calmar` | `f64` | Calmar ratio (annualized return / max drawdown) |

**Error Responses:** Same as Portfolio Risk.

**Dependencies:** `metrics::portfolio_returns`, `metrics::total_return`, `metrics::annualized_return`, `metrics::volatility`, `metrics::sharpe_ratio`, `metrics::sortino_ratio`, `metrics::max_drawdown`, `metrics::win_rate`, `metrics::calmar_ratio`.

---

#### Stress Testing

| Property | Value |
|---|---|
| **Endpoint** | `POST /api/v1/portfolio/stress` |
| **File** | `engine/src/api/portfolio.rs` |
| **Handler** | `stress_test(Json<StressTestInput>)` |

**Request (`StressTestInput`):**

| Field | Type | Required | Description |
|---|---|---|---|
| `holdings` | `Holding[]` | Yes | Portfolio holdings |
| `scenario` | `string` | Yes | Scenario name or "custom" |
| `custom_shocks` | `AssetShock[]?` | No | User-defined shocks |

**AssetShock:**

| Field | Type | Description |
|---|---|---|
| `asset_class` | `string` | Asset class name (e.g., "equity") |
| `shock_pct` | `f64` | Shock as decimal (e.g., -0.30 for -30%) |

**Named Scenarios:** `gfc_2008`, `covid_2020`, `rate_shock_2022`, `dotcom_2000`, `stagflation_1970s`, `black_monday_1987`, `oil_shock_2020`. Unknown names use a moderate default.

**Response (`StressTestResponse`):**

| Field | Type | Description |
|---|---|---|
| `scenario` | `string` | Scenario name |
| `portfolio_impact_pct` | `f64` | Weighted portfolio impact |
| `position_impacts` | `PositionImpact[]` | Per-position impacts |

**Error Responses:** Standard holding validation errors, plus:

| HTTP Status | Code | Condition |
|---|---|---|
| 400 | `MISSING_SCENARIO` | Empty scenario and no custom_shocks |
| 400 | `INVALID_SHOCK` | Non-finite shock value |

**Dependencies:** `stress::run_stress_test`.

---

#### Correlation Matrix

| Property | Value |
|---|---|
| **Endpoint** | `POST /api/v1/portfolio/correlation` |
| **File** | `engine/src/api/portfolio.rs` |
| **Handler** | `correlation_matrix(Json<CorrelationInput>)` |

**Request (`CorrelationInput`):**

| Field | Type | Required | Valid Range | Description |
|---|---|---|---|---|
| `holdings` | `Holding[]` | Yes | >= 2 | Holdings with return series |
| `window_days` | `usize?` | No | >= 1, default 90 | Rolling window |

**Response (`CorrelationResponse`):**

| Field | Type | Description |
|---|---|---|
| `symbols` | `string[]` | Ordered symbol list |
| `matrix` | `f64[][]` | NxN correlation matrix [-1, 1] |
| `window_days` | `usize` | Window used |

**Error Responses:** Standard validation, plus:

| HTTP Status | Code | Condition |
|---|---|---|
| 400 | `INSUFFICIENT_HOLDINGS` | Fewer than 2 holdings |
| 400 | `INVALID_WINDOW` | window_days is 0 |

**Dependencies:** `correlation::compute_matrix`.

---

#### Monte Carlo Simulation

| Property | Value |
|---|---|
| **Endpoint** | `POST /api/v1/portfolio/montecarlo` |
| **File** | `engine/src/api/portfolio.rs` |
| **Handler** | `monte_carlo(Json<MonteCarloInput>)` |

**Request (`MonteCarloInput`):**

| Field | Type | Required | Valid Range | Description |
|---|---|---|---|---|
| `holdings` | `Holding[]` | Yes | >= 1 | Holdings with returns |
| `num_simulations` | `usize?` | No | 1 to 1,000,000 (default: 10,000) | Number of paths |
| `time_horizon_months` | `usize?` | No | 1 to 600 (default: 120) | Horizon in months |
| `initial_value` | `f64` | Yes | > 0, finite | Starting value |

**Response (`MonteCarloResponse`):**

| Field | Type | Description |
|---|---|---|
| `percentiles.p5` | `f64[]` | 5th percentile path (horizon+1 points) |
| `percentiles.p25` | `f64[]` | 25th percentile path |
| `percentiles.p50` | `f64[]` | Median path |
| `percentiles.p75` | `f64[]` | 75th percentile path |
| `percentiles.p95` | `f64[]` | 95th percentile path |
| `num_simulations` | `usize` | Simulations run |
| `time_horizon_months` | `usize` | Horizon used |
| `probability_of_loss` | `f64` | P(final < initial) [0, 1] |

**Error Responses:** Standard validation, plus:

| HTTP Status | Code | Condition |
|---|---|---|
| 400 | `INVALID_INITIAL_VALUE` | Non-positive or non-finite initial_value |
| 400 | `INVALID_NUM_SIMULATIONS` | Outside [1, 1,000,000] |
| 400 | `INVALID_HORIZON` | Outside [1, 600] |

**Dependencies:** `montecarlo::run_simulation`.

---

#### Crypto: Key Derivation

| Property | Value |
|---|---|
| **Endpoint** | `POST /api/v1/crypto/derive-key` |
| **File** | `engine/src/api/crypto_api.rs` |
| **Handler** | `derive_key(Json<DeriveKeyRequest>)` |

**Request:**

| Field | Type | Required | Description |
|---|---|---|---|
| `passphrase` | `string` | Yes | User passphrase (must not be empty) |
| `salt` | `string?` | No | Base64 salt for deterministic derivation |

**Response:**

| Field | Type | Description |
|---|---|---|
| `key_hash` | `string` | Argon2id hash string |
| `salt` | `string` | Salt used (base64) |

**Error Responses:**

| HTTP Status | Code | Condition |
|---|---|---|
| 400 | `INVALID_INPUT` | Empty passphrase or invalid salt |
| 500 | `CRYPTO_ERROR` | Hashing failure |

**Dependencies:** `vault::derive_key`.

---

#### Crypto: Encrypt

| Property | Value |
|---|---|
| **Endpoint** | `POST /api/v1/crypto/encrypt` |
| **File** | `engine/src/api/crypto_api.rs` |
| **Handler** | `encrypt(Json<EncryptRequest>)` |

**Request:**

| Field | Type | Required | Description |
|---|---|---|---|
| `plaintext` | `string` | Yes | Text to encrypt (must not be empty) |
| `key_b64` | `string` | Yes | Base64-encoded 32-byte AES key |

**Response:**

| Field | Type | Description |
|---|---|---|
| `ciphertext_b64` | `string` | Base64 ciphertext |
| `nonce_b64` | `string` | Base64 12-byte nonce |

**Error Responses:**

| HTTP Status | Code | Condition |
|---|---|---|
| 400 | `INVALID_INPUT` | Empty plaintext, invalid base64, wrong key length |
| 500 | `CRYPTO_ERROR` | Encryption failure |

**Dependencies:** `vault::encrypt`.

---

#### Crypto: Decrypt

| Property | Value |
|---|---|
| **Endpoint** | `POST /api/v1/crypto/decrypt` |
| **File** | `engine/src/api/crypto_api.rs` |
| **Handler** | `decrypt(Json<DecryptRequest>)` |

**Request:**

| Field | Type | Required | Description |
|---|---|---|---|
| `ciphertext_b64` | `string` | Yes | Base64 ciphertext |
| `nonce_b64` | `string` | Yes | Base64 12-byte nonce |
| `key_b64` | `string` | Yes | Base64 32-byte AES key |

**Response:**

| Field | Type | Description |
|---|---|---|
| `plaintext` | `string` | Decrypted text |

**Error Responses:**

| HTTP Status | Code | Condition |
|---|---|---|
| 400 | `INVALID_INPUT` | Invalid base64, wrong key/nonce length |
| 422 | `DECRYPTION_FAILED` | Wrong key, tampered ciphertext, etc. |

**Dependencies:** `vault::decrypt`.

---

#### Versioning: Append

| Property | Value |
|---|---|
| **Endpoint** | `POST /api/v1/version` |
| **File** | `engine/src/api/versioning_api.rs` |
| **Handler** | `append_version(State<Arc<VersionStore>>, Json<AppendVersionRequest>)` |

**Request:**

| Field | Type | Required | Description |
|---|---|---|---|
| `entity_id` | `string` | Yes | Stable entity identifier |
| `entity_type` | `string` | Yes | One of: `holdings`, `position`, `decision_entry`, `calibration_record`, `knowledge_chunk`, `knowledge_annotation`, `configuration`, `stress_test_config`, `monte_carlo_config`, `carry_snapshot` |
| `content_encrypted_b64` | `string` | Yes | Base64 encrypted content |
| `nonce_b64` | `string` | Yes | Base64 encryption nonce |
| `change_summary` | `string?` | No | Human-readable change description |
| `tags` | `string[]` | Yes | Metadata tags |

**Response:**

| Field | Type | Description |
|---|---|---|
| `version_hash` | `string` | Content-addressed SHA-256 hash |
| `version` | `u64` | Monotonic version number |

**Error Responses:**

| HTTP Status | Code | Condition |
|---|---|---|
| 400 | `EMPTY_ENTITY_ID` | Empty entity_id |
| 400 | `EMPTY_CONTENT` | Empty content_encrypted_b64 |
| 400 | `EMPTY_NONCE` | Empty nonce_b64 |
| 400 | `INVALID_ENTITY_TYPE` | Unrecognized entity type string |
| 400 | `INVALID_BASE64` | Malformed base64 |
| 500 | `STORE_ERROR` | Lock failure or internal error |

**Dependencies:** `VersionStore::append`, `VersionStore::get_latest`.

---

#### Versioning: History

| Property | Value |
|---|---|
| **Endpoint** | `POST /api/v1/version/history` |
| **File** | `engine/src/api/versioning_api.rs` |
| **Handler** | `get_history(State<Arc<VersionStore>>, Json<GetHistoryRequest>)` |

**Request:**

| Field | Type | Required | Description |
|---|---|---|---|
| `entity_id` | `string` | Yes | Entity to query |
| `limit` | `usize?` | No | Max versions to return |
| `latest_only` | `bool?` | No | Return only the latest version |

**Response:**

| Field | Type | Description |
|---|---|---|
| `entity_id` | `string` | Queried entity |
| `total_versions` | `usize` | Count of returned versions |
| `versions` | `VersionRecord[]` | Version history |

---

#### Versioning: Snapshot

| Property | Value |
|---|---|
| **Endpoint** | `POST /api/v1/version/snapshot` |
| **File** | `engine/src/api/versioning_api.rs` |
| **Handler** | `get_snapshot(Json<SnapshotRequest>)` |

**Status:** Stub. Returns empty entities array. Will be wired to `SnapshotEngine` when SQLite backend is ready.

---

#### Versioning: Integrity

| Property | Value |
|---|---|
| **Endpoint** | `GET /api/v1/version/integrity/{entity_id}` |
| **File** | `engine/src/api/versioning_api.rs` |
| **Handler** | `verify_integrity(State<Arc<VersionStore>>, Path<String>)` |

**Response:**

| Field | Type | Description |
|---|---|---|
| `entity_id` | `string` | Entity checked |
| `chain_valid` | `bool` | Whether all hashes and parent links are valid |
| `total_versions` | `usize` | Number of versions in the chain |

---

### Risk Module

#### portfolio_returns

| Property | Value |
|---|---|
| **File** | `engine/src/risk/metrics.rs` |
| **Signature** | `pub fn portfolio_returns(holdings: &[Holding]) -> Vec<f64>` |

**Purpose:** Compute weighted portfolio returns from holdings. Weights are derived from `market_value` relative to total portfolio value.

**Parameters:**

| Param | Type | Description |
|---|---|---|
| `holdings` | `&[Holding]` | Slice of portfolio holdings |

**Returns:** `Vec<f64>` - Weighted portfolio returns. Empty if holdings are empty, total value is zero, or no return data exists.

**Edge Cases:**
- Empty holdings: returns `[]`
- Zero total value: returns `[]`
- Mismatched return lengths: uses minimum length across all holdings
- Uses `.get(i)` with fallback to 0.0 instead of direct indexing

**Dependencies:** None.

---

#### volatility

| Property | Value |
|---|---|
| **File** | `engine/src/risk/metrics.rs` |
| **Signature** | `pub fn volatility(returns: &[f64]) -> f64` |

**Purpose:** Compute sample standard deviation of returns (annualized volatility).

**Parameters:**

| Param | Type | Valid Range | Description |
|---|---|---|---|
| `returns` | `&[f64]` | >= 2 elements for meaningful result | Period returns |

**Returns:** `f64` - Standard deviation. Returns 0.0 if fewer than 2 returns or non-finite result.

**Edge Cases:**
- Empty/single return: 0.0
- All identical returns: 0.0
- Non-finite result: clamped to 0.0

---

#### sharpe_ratio

| Property | Value |
|---|---|
| **File** | `engine/src/risk/metrics.rs` |
| **Signature** | `pub fn sharpe_ratio(returns: &[f64], risk_free_annual: f64) -> f64` |

**Purpose:** Compute annualized Sharpe ratio = (mean - risk_free_per_period) / volatility.

**Parameters:**

| Param | Type | Description |
|---|---|---|
| `returns` | `&[f64]` | Period returns |
| `risk_free_annual` | `f64` | Annual risk-free rate (e.g., 0.045 for 4.5%) |

**Returns:** `f64` - Sharpe ratio. Returns 0.0 if <2 returns, zero volatility, or non-finite.

**Assumptions:** Returns are monthly (divides risk-free by 12).

**Dependencies:** `volatility`.

---

#### sortino_ratio

| Property | Value |
|---|---|
| **File** | `engine/src/risk/metrics.rs` |
| **Signature** | `pub fn sortino_ratio(returns: &[f64], risk_free_annual: f64) -> f64` |

**Purpose:** Compute Sortino ratio, penalizing only downside deviation.

**Returns:** `f64` - Sortino ratio. Returns `f64::INFINITY` if no downside returns exist, 0.0 if <2 returns.

**Dependencies:** None (self-contained calculation).

---

#### value_at_risk

| Property | Value |
|---|---|
| **File** | `engine/src/risk/metrics.rs` |
| **Signature** | `pub fn value_at_risk(returns: &[f64], alpha: f64) -> f64` |

**Purpose:** Historical Value at Risk at a given confidence level.

**Parameters:**

| Param | Type | Valid Range | Description |
|---|---|---|---|
| `returns` | `&[f64]` | -- | Period returns |
| `alpha` | `f64` | 0.001 to 0.999 (clamped) | Tail probability (0.05 = 95% VaR) |

**Returns:** `f64` - VaR as a positive number (magnitude of loss). Returns 0.0 if empty or all non-finite.

**Edge Cases:**
- Filters out NaN/Infinity values before sorting
- Alpha is clamped to [0.001, 0.999]
- Uses `partial_cmp` with `Ordering::Equal` fallback for NaN safety

---

#### conditional_var

| Property | Value |
|---|---|
| **File** | `engine/src/risk/metrics.rs` |
| **Signature** | `pub fn conditional_var(returns: &[f64], alpha: f64) -> f64` |

**Purpose:** Conditional VaR (Expected Shortfall) - average loss beyond the VaR threshold.

**Returns:** `f64` - CVaR as positive number. Returns 0.0 if empty, non-finite, or empty tail.

---

#### max_drawdown

| Property | Value |
|---|---|
| **File** | `engine/src/risk/metrics.rs` |
| **Signature** | `pub fn max_drawdown(returns: &[f64]) -> f64` |

**Purpose:** Maximum peak-to-trough drawdown from a cumulative return series.

**Returns:** `f64` in [0, 1]. Returns 0.0 if empty or non-finite.

**Edge Cases:**
- Non-finite cumulative values: replaced with previous value
- Zero peak: avoids division by zero

---

#### total_return

| Property | Value |
|---|---|
| **File** | `engine/src/risk/metrics.rs` |
| **Signature** | `pub fn total_return(returns: &[f64]) -> f64` |

**Purpose:** Cumulative return: product of (1 + r_i) - 1.

**Returns:** `f64`. Returns 0.0 if empty or non-finite.

---

#### annualized_return

| Property | Value |
|---|---|
| **File** | `engine/src/risk/metrics.rs` |
| **Signature** | `pub fn annualized_return(returns: &[f64], periods_per_year: usize) -> f64` |

**Purpose:** Annualize a cumulative return given periods per year.

**Parameters:**

| Param | Type | Valid Range | Description |
|---|---|---|---|
| `returns` | `&[f64]` | -- | Period returns |
| `periods_per_year` | `usize` | > 0 | Periods in a year (e.g., 12 for monthly) |

**Returns:** `f64`. Returns 0.0 if empty or periods_per_year is 0. Returns -1.0 if total loss exceeds 100%.

**Dependencies:** `total_return`.

---

#### win_rate

| Property | Value |
|---|---|
| **File** | `engine/src/risk/metrics.rs` |
| **Signature** | `pub fn win_rate(returns: &[f64]) -> f64` |

**Purpose:** Fraction of periods with positive returns.

**Returns:** `f64` in [0, 1]. Returns 0.0 if empty.

---

#### calmar_ratio

| Property | Value |
|---|---|
| **File** | `engine/src/risk/metrics.rs` |
| **Signature** | `pub fn calmar_ratio(returns: &[f64], periods_per_year: usize) -> f64` |

**Purpose:** Calmar ratio = annualized_return / max_drawdown.

**Returns:** `f64`. Returns 0.0 if drawdown is zero or non-finite result.

**Dependencies:** `annualized_return`, `max_drawdown`.

---

#### pearson_correlation

| Property | Value |
|---|---|
| **File** | `engine/src/risk/correlation.rs` |
| **Signature** | `fn pearson_correlation(x: &[f64], y: &[f64], window: usize) -> f64` |
| **Visibility** | Private (crate-internal) |

**Purpose:** Compute Pearson correlation coefficient over trailing `window` observations.

**Returns:** `f64` in [-1, 1]. Returns 0.0 if <2 valid pairs, zero variance, or non-finite denominator.

**Edge Cases:**
- NaN/Infinity pairs are filtered out
- Result is clamped to [-1, 1] to handle floating-point drift

---

#### compute_matrix

| Property | Value |
|---|---|
| **File** | `engine/src/risk/correlation.rs` |
| **Signature** | `pub fn compute_matrix(input: &CorrelationInput) -> CorrelationResponse` |

**Purpose:** Compute pairwise correlation matrix for all holdings.

**Returns:** `CorrelationResponse` with NxN symmetric matrix, diagonal = 1.0.

**Dependencies:** `pearson_correlation`.

---

#### run_stress_test

| Property | Value |
|---|---|
| **File** | `engine/src/risk/stress.rs` |
| **Signature** | `pub fn run_stress_test(input: &StressTestInput) -> StressTestResponse` |

**Purpose:** Apply scenario shocks to portfolio positions and compute weighted impact.

**Edge Cases:**
- Zero total value: portfolio_impact_pct = 0.0
- Non-finite market values: treated as 0.0
- Non-finite custom shocks: treated as 0.0
- Unknown scenario: moderate default shocks applied

**Dependencies:** `get_scenario_shocks`, `classify_asset`.

---

#### get_scenario_shocks

| Property | Value |
|---|---|
| **File** | `engine/src/risk/stress.rs` |
| **Signature** | `fn get_scenario_shocks(name: &str) -> Vec<(&'static str, f64)>` |
| **Visibility** | Private |

**Purpose:** Return asset-class shock profiles for named historical scenarios.

**Supported Scenarios:** `gfc_2008`, `covid_2020`, `rate_shock_2022`, `dotcom_2000`, `stagflation_1970s`, `black_monday_1987`, `oil_shock_2020`.

---

#### classify_asset

| Property | Value |
|---|---|
| **File** | `engine/src/risk/stress.rs` |
| **Signature** | `fn classify_asset(_symbol: &str) -> &'static str` |
| **Visibility** | Private |

**Purpose:** Classify a holding into a broad asset class. **Stub**: always returns `"equity"`.

---

#### run_simulation (Monte Carlo)

| Property | Value |
|---|---|
| **File** | `engine/src/risk/montecarlo.rs` |
| **Signature** | `pub fn run_simulation(input: &MonteCarloInput) -> MonteCarloResponse` |

**Purpose:** Run Monte Carlo simulation using geometric Brownian motion parameterized from historical returns.

**Edge Cases:**
- Empty holdings: zero mean/stddev produces flat paths
- Per-period returns capped at [-50%, +50%] to prevent overflow
- Non-finite simulation values: clamped to 0.0
- Simulation count clamped to [1, 1,000,000]
- Horizon clamped to [1, 600]

**Dependencies:** `percentile`, `sample_standard_normal`.

---

#### percentile

| Property | Value |
|---|---|
| **File** | `engine/src/risk/montecarlo.rs` |
| **Signature** | `fn percentile(sorted: &[f64], p: f64) -> f64` |
| **Visibility** | Private |

**Purpose:** Compute a percentile value from a pre-sorted slice.

**Returns:** 0.0 if empty. `p` is clamped to [0, 1].

---

#### sample_standard_normal

| Property | Value |
|---|---|
| **File** | `engine/src/risk/montecarlo.rs` |
| **Signature** | `fn sample_standard_normal(rng: &mut impl Rng) -> f64` |
| **Visibility** | Private |

**Purpose:** Generate a standard normal random variate using the Box-Muller transform.

**Returns:** N(0,1) sample. Non-finite results return 0.0.

---

### Crypto Module

#### derive_key

| Property | Value |
|---|---|
| **File** | `engine/src/crypto/vault.rs` |
| **Signature** | `pub fn derive_key(passphrase: &str, existing_salt: Option<&str>) -> Result<(String, String), CryptoError>` |

**Purpose:** Derive a 256-bit key from a passphrase using Argon2id with 600K iterations.

**Returns:** `Ok((key_hash, salt))` on success.

**Errors:**
- `CryptoError::EmptyPassphrase` if passphrase is empty
- `CryptoError::InvalidSalt` if salt base64 is malformed
- `CryptoError::InvalidParams` if Argon2 parameters are invalid
- `CryptoError::HashingFailed` if the hashing operation fails

---

#### encrypt

| Property | Value |
|---|---|
| **File** | `engine/src/crypto/vault.rs` |
| **Signature** | `pub fn encrypt(plaintext: &str, key_b64: &str) -> Result<(String, String), CryptoError>` |

**Purpose:** Encrypt plaintext with AES-256-GCM. Generates a random 12-byte nonce.

**Returns:** `Ok((ciphertext_b64, nonce_b64))` on success.

**Errors:**
- `CryptoError::EmptyPlaintext`
- `CryptoError::InvalidBase64` if key is not valid base64
- `CryptoError::InvalidKeyLength` if key is not 32 bytes
- `CryptoError::EncryptionFailed`

---

#### decrypt

| Property | Value |
|---|---|
| **File** | `engine/src/crypto/vault.rs` |
| **Signature** | `pub fn decrypt(ciphertext_b64: &str, nonce_b64: &str, key_b64: &str) -> Result<String, CryptoError>` |

**Purpose:** Decrypt AES-256-GCM ciphertext.

**Returns:** `Ok(plaintext)` on success.

**Errors:**
- `CryptoError::InvalidBase64` for any malformed input
- `CryptoError::InvalidKeyLength` if key is not 32 bytes
- `CryptoError::InvalidNonceLength` if nonce is not 12 bytes
- `CryptoError::DecryptionFailed` (wrong key, tampered data)
- `CryptoError::InvalidUtf8` if decrypted bytes are not UTF-8

---

#### CryptoError

| Property | Value |
|---|---|
| **File** | `engine/src/crypto/vault.rs` |
| **Type** | `enum` (derives `Debug`, `thiserror::Error`) |

**Variants:**

| Variant | Description |
|---|---|
| `InvalidSalt(String)` | Salt base64 is malformed |
| `InvalidParams(String)` | Argon2 parameters are invalid |
| `HashingFailed(String)` | Password hashing failed |
| `InvalidBase64(String)` | Base64 decode failure |
| `InvalidKeyLength { expected, actual }` | Key is not 32 bytes |
| `InvalidNonceLength { expected, actual }` | Nonce is not 12 bytes |
| `EncryptionFailed(String)` | AES-GCM encryption error |
| `DecryptionFailed(String)` | AES-GCM decryption error |
| `InvalidUtf8` | Decrypted content is not valid UTF-8 |
| `EmptyPassphrase` | Passphrase is empty |
| `EmptyPlaintext` | Plaintext is empty |

---

### Versioning Module

#### VersionedRecord

| Property | Value |
|---|---|
| **File** | `engine/src/versioning/types.rs` |
| **Type** | `struct` (derives `Debug, Clone, Serialize, Deserialize`) |

**Fields:**

| Field | Type | Description |
|---|---|---|
| `version_hash` | `String` | SHA-256 content-addressed hash |
| `entity_id` | `String` | Stable entity identifier |
| `entity_type` | `EntityType` | Type enum |
| `version` | `u64` | Monotonic version number |
| `author` | `VersionAuthor` | Who created this version |
| `created_at` | `DateTime<Utc>` | Creation timestamp |
| `content_encrypted` | `Vec<u8>` | Encrypted content bytes |
| `nonce` | `Vec<u8>` | AES-GCM nonce |
| `metadata` | `VersionMetadata` | Plaintext metadata |
| `parent_hash` | `Option<String>` | Previous version's hash |

**Methods:**

| Method | Signature | Description |
|---|---|---|
| `compute_hash` | `fn(entity_id: &str, version: u64, content: &[u8]) -> String` | Compute content-addressed hash |
| `verify_integrity` | `fn(&self) -> bool` | Verify hash matches content |

---

#### VersionStore

| Property | Value |
|---|---|
| **File** | `engine/src/versioning/store.rs` |
| **Type** | `struct` (in-memory, `Arc<RwLock<HashMap>>`) |

**Methods:**

| Method | Signature | Returns | Description |
|---|---|---|---|
| `new` | `fn() -> Self` | `VersionStore` | Create empty store |
| `append` | `fn(&self, entity_id, entity_type, content, nonce, author, summary, tags)` | `Result<String, VersionStoreError>` | Append a new version |
| `get_latest` | `fn(&self, entity_id: &str)` | `Result<Option<VersionedRecord>, ...>` | Get latest version |
| `get_by_hash` | `fn(&self, version_hash: &str)` | `Result<Option<VersionedRecord>, ...>` | Find by hash |
| `query` | `fn(&self, query: &VersionQuery)` | `Result<Vec<VersionedRecord>, ...>` | Query history with filters |
| `total_versions` | `fn(&self)` | `Result<usize, ...>` | Count all versions |
| `verify_chain` | `fn(&self, entity_id: &str)` | `Result<bool, ...>` | Verify hash chain integrity |

**Error Type: `VersionStoreError`**

| Variant | Description |
|---|---|
| `LockFailed` | RwLock poisoned |
| `NotFound(String)` | Entity not found |
| `IntegrityFailed(String)` | Chain integrity violated |

---

#### SnapshotEngine

| Property | Value |
|---|---|
| **File** | `engine/src/versioning/snapshot.rs` |
| **Type** | `struct<'a>` (borrows `&'a VersionStore`) |

**Methods:**

| Method | Signature | Status | Description |
|---|---|---|---|
| `new` | `fn(store: &'a VersionStore) -> Self` | Implemented | Constructor |
| `snapshot_at` | `fn(&self, query: &SnapshotQuery)` | **Stub** | Point-in-time portfolio state |
| `diff` | `fn(&self, from, to, entity_types)` | **Stub** | Compare two timestamps |

---

### Data Types (Rust)

#### EntityType (enum)

```
Holdings, Position, DecisionEntry, CalibrationRecord,
KnowledgeChunk, KnowledgeAnnotation, Configuration,
StressTestConfig, MonteCarloConfig, CarrySnapshot
```

#### VersionAuthor (enum)

```
User, System, DataFeed(String)
```

#### VersionMetadata (struct)

| Field | Type |
|---|---|
| `change_summary` | `Option<String>` |
| `content_size_bytes` | `u64` |
| `tags` | `Vec<String>` |

---

## Python Analytics

### Factor Decomposition

#### decompose

| Property | Value |
|---|---|
| **File** | `analytics/pae/models/factor.py` |
| **Signature** | `def decompose(portfolio_returns: NDArray, factor_returns: dict[str, NDArray]) -> FactorDecomposition` |

**Purpose:** Run OLS regression of portfolio returns on factor returns (Fama-French style).

**Parameters:**

| Param | Type | Valid Range | Description |
|---|---|---|---|
| `portfolio_returns` | `NDArray[float64]` | 1-D, no NaN/Inf, len >= factors+2 | Period returns |
| `factor_returns` | `dict[str, NDArray]` | Same length as portfolio, no NaN/Inf | Factor return series |

**Returns:** `FactorDecomposition` with alpha, R-squared, and per-factor exposures.

**Raises:**
- `ValueError` if inputs are empty, non-1D, contain non-finite values, have mismatched lengths, or insufficient observations
- `numpy.linalg.LinAlgError` if factor matrix is singular (collinear factors)

**Dependencies:** `numpy.linalg.inv`.

---

#### FactorExposure

| Field | Type | Description |
|---|---|---|
| `factor_name` | `str` | Factor identifier |
| `beta` | `float` | Regression coefficient |
| `t_stat` | `float` | T-statistic |
| `contribution_pct` | `float` | Variance contribution (%) |

#### FactorDecomposition

| Field | Type | Description |
|---|---|---|
| `alpha` | `float` | Intercept (excess return) |
| `alpha_t_stat` | `float` | Alpha T-statistic |
| `r_squared` | `float` | R-squared [0, 1] |
| `exposures` | `list[FactorExposure]` | Per-factor results |
| `residual_risk_pct` | `float` | Unexplained variance (%) |

---

### Carry Analysis

#### analyze_carry

| Property | Value |
|---|---|
| **File** | `analytics/pae/models/carry.py` |
| **Signature** | `def analyze_carry(holdings: list[dict], total_margin: float, margin_rate: float = 0.058) -> PortfolioCarry` |

**Purpose:** Compute carry analysis for a leveraged portfolio -- income vs. margin cost.

**Parameters:**

| Param | Type | Valid Range | Description |
|---|---|---|---|
| `holdings` | `list[dict]` | Non-empty, each needs `symbol` and `market_value` >= 0 | Portfolio holdings |
| `total_margin` | `float` | >= 0, finite | Total margin debt |
| `margin_rate` | `float` | >= 0, finite, default 0.058 | Annual margin rate |

**Returns:** `PortfolioCarry` with position-level and aggregate carry metrics.

**Raises:** `ValueError` if holdings is empty, margin is negative, rate is negative, holdings are missing required fields, or values are non-finite.

---

#### PositionCarry

| Field | Type | Description |
|---|---|---|
| `symbol` | `str` | Ticker |
| `market_value` | `float` | Position value |
| `yield_pct` | `float` | Annual yield % |
| `annual_income` | `float` | MV * yield |
| `margin_allocated` | `float` | Proportional margin |
| `margin_rate` | `float` | Rate used |
| `annual_margin_cost` | `float` | Margin cost |
| `net_carry` | `float` | Income - margin cost |
| `carry_spread` | `float` | Yield - margin rate |

#### PortfolioCarry

| Field | Type | Description |
|---|---|---|
| `total_nav` | `float` | Net asset value |
| `total_long_value` | `float` | Sum of market values |
| `total_margin` | `float` | Total margin |
| `leverage_ratio` | `float` | Long / NAV |
| `total_annual_income` | `float` | Total income |
| `total_annual_margin_cost` | `float` | Total margin cost |
| `net_carry` | `float` | Net carry |
| `income_coverage_ratio` | `float` | Income / margin cost |
| `margin_as_pct_of_nav` | `float` | Margin % of NAV |
| `positions` | `list[PositionCarry]` | Per-position detail |

---

### Decision Journal

#### DecisionEntry

| Property | Value |
|---|---|
| **File** | `analytics/pae/decision/journal.py` |
| **Type** | `@dataclass` |

A single decision journal entry with auto-validation via `__post_init__`:
- `confidence` clamped to [1, 10]
- `max_acceptable_loss_pct` converted to absolute value if negative
- Invalid `emotional_state` defaults to `"neutral"`

See file docstring for full field listing (19 fields).

#### EmotionalState

Enum with values: `CALM`, `ANXIOUS`, `EXCITED`, `FEARFUL`, `CONFIDENT`, `UNCERTAIN`, `NEUTRAL`.

#### compute_calibration

| Property | Value |
|---|---|
| **File** | `analytics/pae/decision/journal.py` |
| **Signature** | `def compute_calibration(entries: list[DecisionEntry]) -> list[CalibrationMetric]` |

**Purpose:** Compute confidence calibration -- do high-confidence decisions perform better?

**Parameters:**

| Param | Type | Description |
|---|---|---|
| `entries` | `list[DecisionEntry]` | Journal entries (only those with outcome_90d are used) |

**Returns:** List of 3 `CalibrationMetric` objects (buckets 8-10, 5-7, 1-4). Empty list if entries is empty.

#### CalibrationMetric

| Field | Type | Description |
|---|---|---|
| `confidence_bucket` | `str` | Bucket label ("8-10", "5-7", "1-4") |
| `total_decisions` | `int` | Decisions in bucket |
| `positive_outcomes` | `int` | Positive 90d outcomes |
| `accuracy_pct` | `float` | Hit rate (%) |

---

### PKE Ingestion

#### ingest_markdown

| Property | Value |
|---|---|
| **File** | `analytics/pae/pke/ingest.py` |
| **Signature** | `def ingest_markdown(file_path: Path) -> IngestionResult` |

**Purpose:** Ingest a Markdown file: extract frontmatter, chunk text, classify themes, generate IDs.

**Edge Cases:**
- File does not exist: returns error result
- File is empty: returns error result
- Encoding error: returns error result
- Chunks <10 words: discarded

**Dependencies:** `parse_frontmatter`, `chunk_text`, `classify_themes`, `generate_chunk_id`.

---

#### ingest_directory

| Property | Value |
|---|---|
| **File** | `analytics/pae/pke/ingest.py` |
| **Signature** | `def ingest_directory(dir_path: Path) -> list[IngestionResult]` |

**Purpose:** Recursively ingest all `*.md` files in a directory.

**Raises:** `ValueError` if path does not exist or is not a directory.

---

#### chunk_text

| Property | Value |
|---|---|
| **File** | `analytics/pae/pke/ingest.py` |
| **Signature** | `def chunk_text(text: str, max_tokens: int = 400) -> list[str]` |

**Purpose:** Split text into semantic chunks at paragraph boundaries, respecting `max_tokens` word limit.

**Raises:** `ValueError` if `max_tokens < 10`.

**Returns:** Empty list if text is empty/blank.

---

#### parse_frontmatter

| Property | Value |
|---|---|
| **File** | `analytics/pae/pke/ingest.py` |
| **Signature** | `def parse_frontmatter(text: str) -> tuple[dict, str]` |

**Purpose:** Extract YAML frontmatter between `---` delimiters.

**Returns:** `(metadata_dict, body_text)`. Empty dict if no frontmatter found.

---

#### generate_chunk_id

| Property | Value |
|---|---|
| **File** | `analytics/pae/pke/ingest.py` |
| **Signature** | `def generate_chunk_id(source: str, text: str) -> str` |

**Purpose:** Deterministic 16-char hex hash from source + first 200 chars of text.

---

#### classify_themes

| Property | Value |
|---|---|
| **File** | `analytics/pae/pke/ingest.py` |
| **Signature** | `def classify_themes(text: str) -> list[str]` |

**Purpose:** Keyword-based theme classification. Stub for future zero-shot classifier.

**Returns:** List of matching themes, or `["general"]` if none match.

---

#### KnowledgeChunk

| Field | Type | Description |
|---|---|---|
| `chunk_id` | `str` | Deterministic hash ID |
| `source` | `str` | Source document |
| `author` | `str` | Author |
| `date` | `str` | Date string |
| `themes` | `list[str]` | Classifications |
| `text` | `str` | Passage content |
| `embedding` | `list[float]` | Vector (empty until populated) |

#### IngestionResult

| Field | Type | Description |
|---|---|---|
| `source_file` | `str` | File path |
| `chunks_created` | `int` | Chunks produced |
| `themes_detected` | `list[str]` | Unique themes |
| `errors` | `list[str]` | Error messages |
| `chunks` | `list[KnowledgeChunk]` | Chunk objects |

---

### PKE Retrieval

#### retrieve_by_theme

| Property | Value |
|---|---|
| **File** | `analytics/pae/pke/retrieve.py` |
| **Signature** | `def retrieve_by_theme(theme: str, top_k: int = 5) -> list[RetrievalResult]` |

**Status:** Stub (returns empty list). Will use sqlite-vec.

**Raises:** `ValueError` if theme is empty or top_k < 1.

---

#### retrieve_by_context

| Property | Value |
|---|---|
| **File** | `analytics/pae/pke/retrieve.py` |
| **Signature** | `def retrieve_by_context(context_text: str, themes: list[str] | None = None, top_k: int = 5) -> list[RetrievalResult]` |

**Status:** Stub (returns empty list). Will use semantic search.

**Raises:** `ValueError` if context_text is empty or top_k < 1.

---

#### RetrievalResult

| Field | Type | Description |
|---|---|---|
| `chunk_id` | `str` | Chunk identifier |
| `source` | `str` | Source document |
| `author` | `str` | Author |
| `text` | `str` | Passage text |
| `themes` | `list[str]` | Theme classifications |
| `relevance_score` | `float` | Similarity [0, 1] |

---

## TypeScript UI

### PaeApp

| Property | Value |
|---|---|
| **File** | `ui/src/components/pae-app.ts` |
| **Element** | `<pae-app>` |
| **Shadow DOM** | Open |

**Purpose:** Root application shell. Manages layout (header, sidebar, main), theme toggle (light/dark persisted to localStorage), and hash-based navigation.

**Methods:**

| Method | Visibility | Description |
|---|---|---|
| `initTheme()` | private | Load theme from localStorage; fallback to dark |
| `toggleTheme()` | private | Switch theme and persist; handles localStorage errors |
| `setupRouting()` | private | Register `hashchange` listener |
| `handleRoute(hash)` | private | Update active nav item styling |
| `render()` | private | Build shadow DOM HTML |

**Lifecycle:**
- `connectedCallback`: init theme, render, setup routing
- `disconnectedCallback`: remove hashchange listener

---

### PaeDashboard

| Property | Value |
|---|---|
| **File** | `ui/src/components/pae-dashboard.ts` |
| **Element** | `<pae-dashboard>` |
| **Shadow DOM** | Open |

**Purpose:** Portfolio overview with metric cards (NAV, return, Sharpe, drawdown), holdings table, allocation/performance charts, and carry analysis section.

**Methods:**

| Method | Visibility | Description |
|---|---|---|
| `setMetric(id, value)` | private | Update a metric card's display |
| `formatCurrency(value)` | private | Format as USD; '--' for non-finite |
| `formatPercent(value)` | private | Format as %; '--' for non-finite |
| `formatNumber(value, decimals)` | private | Fixed-decimal format; '--' for non-finite |
| `fetchApi<T>(endpoint, body)` | private | POST to engine API with 15s timeout |
| `render()` | private | Build shadow DOM HTML |

**Error Handling:** `fetchApi` catches network errors, abort (timeout), and non-200 responses. Returns `null` on any failure. Logs errors to console.

---

### PaeChart

| Property | Value |
|---|---|
| **File** | `ui/src/components/pae-chart.ts` |
| **Element** | `<pae-chart>` |
| **Shadow DOM** | Open |
| **Observed Attributes** | `type`, `width`, `height` |

**Purpose:** Canvas-based chart rendering. Supports line and donut/pie charts with zero external dependencies.

**Methods:**

| Method | Visibility | Description |
|---|---|---|
| `getNumericAttr(name, default, min, max)` | private | Parse numeric attribute with clamping |
| `drawLine(data: ChartData)` | **public** | Render line chart. Skips non-finite values (gap). |
| `drawPie(data: PieSlice[])` | **public** | Render donut chart. Filters non-positive slices. |
| `render()` | private | Create canvas element |

**Exported Types:**

| Type | Description |
|---|---|
| `ChartData` | `{ labels: string[], datasets: ChartDataset[] }` |
| `ChartDataset` | `{ label: string, data: number[], color: string }` |
| `PieSlice` | `{ label: string, value: number, color: string }` |
| `ChartType` | `'line' \| 'bar' \| 'pie'` |

**Edge Cases:**
- Empty datasets: no-op
- All NaN/Infinity values: no-op
- Zero total in pie: no-op
- Width/height attributes: clamped to [50, 4000]

---

### PaeDisclaimer

| Property | Value |
|---|---|
| **File** | `ui/src/components/pae-disclaimer.ts` |
| **Element** | `<pae-disclaimer>` |
| **Shadow DOM** | Open |

**Purpose:** Fixed-position regulatory disclaimer bar. Cannot be permanently dismissed (architectural requirement). Rendered on every page.

**Accessibility:** Uses `role="contentinfo"` and `aria-label="Legal disclaimer"`.

---

## Audit Summary

### Issues Found and Fixed

#### Rust Engine (27 fixes)

| Category | Count | Examples |
|---|---|---|
| Panic-prone `.expect()` / `.unwrap()` | 11 | `vault.rs` had 8 `.expect()` calls; `metrics.rs` had `partial_cmp().unwrap()` |
| Missing input validation | 6 | Empty holdings, negative market values, non-finite returns |
| Missing API error responses | 4 | All API handlers returned bare JSON without error handling |
| Missing NaN/Infinity guards | 3 | Sort comparisons, cumulative return overflow |
| Missing graceful shutdown | 1 | Server had no SIGINT/SIGTERM handler |
| Versioning stubs not wired | 2 | API stubs not connected to VersionStore |

#### Python Analytics (18 fixes)

| Category | Count | Examples |
|---|---|---|
| Missing input validation | 8 | Empty arrays, non-finite values, missing dict keys |
| Missing docstrings | 4 | `PositionCarry`, `PortfolioCarry`, `CalibrationMetric`, `RetrievalResult` |
| Missing type annotations | 2 | `Optional` types on return values |
| Missing exception handling | 2 | `OSError` and `UnicodeDecodeError` in file I/O |
| Division by zero risks | 2 | Zero total_margin_cost, zero portfolio_var |

#### TypeScript UI (12 fixes)

| Category | Count | Examples |
|---|---|---|
| Missing null/NaN guards | 4 | Non-finite values in charts, formatters |
| Missing API error handling | 2 | `fetchApi` with timeout, error responses |
| Missing accessibility | 3 | `role`, `aria-label` on charts, nav, disclaimer |
| Missing cleanup | 2 | `disconnectedCallback`, `removeEventListener` |
| Missing type exports | 1 | `PieSlice`, `ChartDataset` types |
