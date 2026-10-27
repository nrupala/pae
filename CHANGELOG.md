# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- **Phase 3D — buyer-walk fixes.** An independent walk of the 3C bundle (install → seed → use, as a stranger) returned DO NOT CERTIFY with three blockers; this batch closes them plus the friction findings behind them.
  - *W1 (blocker) — the seeded demo was unreachable:* analytics auto-creates an empty "Default" portfolio and every dashboard component pinned `portfolios[0]`, so the seeded Demo Portfolio could not be opened in the UI at all. The app shell now owns ONE shared portfolio selection (`ui/src/portfolio-selection.ts`): a visible selector in the sidebar, defaulting to the first portfolio WITH holdings (fallback: the first portfolio), persisted and read by dashboard, risk, factors, scenarios, optimize, import, holdings, and carry. Switching remounts the current view on the new selection.
  - *W3 (blocker) — stress contradicted itself:* position impacts printed as "-0.20%" beside "-$1,000 on $5,000", and cash took a −20% shock in "2008". Three stacked causes on one path: (1) the dashboard's scenario ids (`2008`/`2020`/`rate_shock`) matched no engine table key and silently fell into a flat default profile — they are now aliases for `gfc_2008`/`covid_2020`/`rate_shock_2022`, resolved in one place; (2) the engine's asset classifier was a stub returning "equity" for everything, the scenario tables had no "cash" entry, and the analytics→engine holdings builder dropped the `asset_class` the store keeps — holdings now carry their class end to end, classification uses it (documented "equity" fallback only when absent), and cash is an explicit 0.0 shock in every profile; (3) `impact_pct` was a raw fraction printed as a percent — both impact fields are now true percents (impact ÷ base × 100) across the engine, the analytics path, the MCP tool, and the docs, pinned by hand-computed known-answer vectors on both sides (per-class GFC: cash 0.0%, equity −50.0%, bonds +5.0%, portfolio −18.0%).
  - *W2 (blocker, copy) — the dropped claim removed:* the "zero-knowledge, zero-trust" positioning the owner ruled out still fronted the README, the product site (title/meta/social/lede/privacy section/footer), `llms.txt`, the discovery document, and the API description. All now state the true split: local-first and user-sovereign; vault records encrypted in the browser; the analytics working store an unencrypted local file — with the site's privacy section rewritten as "what is encrypted, and what is not". The Knowledge view also labels a cleartext knowledge base in-product (see W4). README's feature list corrected (W10): correlation is an engine API surface, not a dashboard view; journal calibration is library-only and not surfaced; the PKE line states the encrypted-only-with-a-key truth; the IBKR import claim narrowed to the header layout the parser detects.
  - *W5 — same-origin dashboard:* 9 of 11 components hardcoded `http://localhost:3002` (the journal, `localhost:3001`), breaking most views on any non-default analytics port. All components now call same-origin relative paths. On the engine, the analytics data plane (`/api/v1/portfolios`, `/api/v1/holdings`, `/api/v1/import/*`, `/api/v1/knowledge/*`) joins the proxy set — and the engine's own ciphertext (vault) store moved from those paths to `/api/v1/vault/*` to make room (handlers and semantics unchanged; no shipped consumer used the old paths). **API note:** clients of the engine's ciphertext holdings/portfolios/import endpoints must use the `/api/v1/vault/` prefix from this release.
  - *W4 — knowledge base path and empty state:* the PKE store ignored `PAE_HOME` (it lived outside the documented backup tree), its endpoints 500'd under a fresh PAE_HOME (missing parent directory), and a missing database was an error instead of the empty state. Resolution is now `PAE_PKE_PATH` → `$PAE_HOME/data/pke.db` → the historic XDG default; the store creates its parent directory on open; the endpoints return `{"total": 0, "chunks": []}` when no base exists (a read creates nothing); the chunks response carries `cleartext_chunks`, and the Knowledge view shows a plain "stored unencrypted in this configuration" disclosure when any chunk is unencrypted. SELF_HOST's data map and backup advice now match reality (with a migration note for pre-3D installs).
  - *W7 — demo data worth evaluating:* the seed's 6 monthly returns and unset yields made Factors refuse, risk metrics degenerate, and Carry show $0. The seed now generates a deterministic 36-month synthetic series per holding (distinct drift/volatility) and sets yields on every position; its fictional labelling and self-verification are unchanged. Exposed by the richer dataset and fixed here: the optimizer's risk-parity iteration could oscillate without converging (near-zero-variance asset with negative cross-covariance — the demo's cash) and silently returned a 100%-in-one-asset corner; it now detects non-convergence and returns labelled inverse-volatility weights with the substitution stated in the result notes. Also fixed: the Optimize view's risk-free-rate default — the input is per-period while the holdings series are monthly, so the old 0.02 default (≈27%/yr equivalent) pushed maximum-Sharpe into its documented minimum-variance fallback on the demo; the default is now 0, the neutral per-period value, and the field is labelled per-period in the view.
  - *W6/W8/W9 — build and docs honesty:* the README Quick Start now describes the working bundle-build path (venv from `analytics/requirements-lock.txt` + `PAE_BUILD_PYTHON`, as in PACKAGING.md — the bare `scripts/build-bundle.sh` it previously printed fails); `build-bundle.sh` preflights its toolchain (cargo, npm, and a Python that can import PyInstaller + the analytics app) and exits early naming the fix; the release tarball is created with portable root ownership (`--owner=0 --group=0 --numeric-owner`) so untar-as-root is clean. SELF_HOST gains the installer's `--prefix`/`--bin-dir` usage, a CSV import format section documenting the columns the parser actually accepts (with the IBKR Flex note in its header-detection form), and one worked A2A example message with its reply envelope.
  - *Scheduled convergence (named debt):* the scenario catalogue lives in two places — the engine's table (7 scenarios, canonical names + aliases) and the dashboard's picker (a hardcoded subset of 3, legacy ids). The ids now resolve identically everywhere, but the picker should be served the engine's catalogue instead of hardcoding its subset. Likewise the engine's ciphertext vault plane (`/api/v1/vault/*`) and the analytics data plane expose overlapping portfolio/holding concepts under two contracts; they stay separate deliberately (ciphertext vault records vs plaintext working store) and any future unification is a product decision, not a refactor.
- **Phase 3C (part 1) — proof + hardening.**
  - *Known-answer test vectors:* the risk and analytics math is now pinned to values computed independently of the code — VaR/CVaR and Sharpe/volatility/drawdown on hand-computed series, Monte Carlo checked against its exact analytic terminal distribution within stated sampling-error tolerances, stress and bond duration/YTM textbook vectors (Rust), and Fama-French-style OLS recovery of known coefficients plus hand-computed carry cases (Python). No discrepancies found; the engine's VaR indexing convention (`floor(alpha·n)` into the ascending sort) is flagged in the tests as a product decision to confirm, not silently endorsed.
  - *Agent token (security):* the A2A/agent surfaces (analytics) and the engine's caller-supplied-key `/api/v1/crypto/encrypt` + `/decrypt` endpoints now require a per-install bearer token (`Authorization: Bearer`), generated on first start into an owner-only (`0600`) file in the PAE data dir. No token configured = those surfaces stay closed (there is no open fallback). Dashboard and data routes unchanged. See "The agent token" in `docs/SELF_HOST.md`.
  - *Journal encryption:* dashboard journal entries are now genuinely encrypted in the browser (vault unlock with the user's passphrase; AES-256-GCM via WebCrypto) before the engine stores them — the Phase 3B base64/zero-nonce stand-in is removed. Pre-3C entries still read and are labelled as stored unencrypted.
  - *Journal metadata (security follow-up, found on independent review):* the first 3C build still sent the entry title as the record's `change_summary`, which the versioning API stores as plaintext metadata — a plaintext copy of the title next to the ciphertext. New entries now send no `change_summary` (the title lives only inside the encrypted content); the list shows decrypted titles when the vault is unlocked and a neutral "Encrypted entry" label when locked. Entries the first 3C build already wrote keep their stored summary until rewritten. Tags and dates remain plaintext metadata by design, and the write form now says so where tags are typed.
  - *KDF iterations (security/correctness fix):* the engine advertised Argon2id with 600,000 iterations — a PBKDF2-scale pass count on an algorithm whose time cost is meant to be single-digit. At the advertised 64 MiB per pass, client-side derivation never completed in practice, leaving the browser vault create/unlock flow unusable. The advertised parameters are now the RFC 9106 first-recommended Argon2id profile (64 MiB, 3 iterations, 4 lanes), matching the memory and parallelism that were already set. Docs that cited the old count (README, ARCHITECTURE, THREAT_MODEL, FUNCTION_REFERENCE) are aligned.
  - *Vault creation 204 fix:* the client's POST helper parsed every successful response as JSON, but storing the DEK envelope returns `204 No Content` — so `createVault` threw on the empty body in the browser even though the envelope had been stored. The helper now returns `null` for bodyless responses (callers that require a body fail loudly instead), keeping the error path for non-2xx unchanged.
  - *DEK-envelope replacement guard (security fix, found on independent review):* `POST /api/v1/crypto/dek-envelope` stored the vault's wrapped data key insert-or-replace, unauthenticated — one request could irreversibly overwrite the only wrapped copy of the DEK and permanently brick every encrypted record. The first write (vault creation, which the browser flow performs before any credential exists) stays open; once an envelope exists, replacing it requires the per-install agent token (`Authorization: Bearer`), and anything else is refused with `409 DEK_ENVELOPE_EXISTS`, the stored envelope untouched. Token-presenting replacement is the documented rotation path (docs/SELF_HOST.md, docs/THREAT_MODEL.md).
  - *Agent-token path parity (fix, found on independent review):* the analytics service expands a leading `~` in `PAE_AGENT_TOKEN_PATH` / `PAE_HOME` (Python `expanduser`), but the engine used the value literally — with a tilde in either variable the two processes resolved different token files and agent auth desynchronized between them. The engine now expands a leading `~` / `~/` against `$HOME` exactly as the analytics side does for those cases (Python additionally resolves `~user` through the account database; the engine deliberately does not).
- **Phase 3B — version store persistence + finished dashboard.**
  - *Fixed:* the engine's append-only version store was in-memory only, so versioned records (including the decision journal) vanished on engine restart. It is now backed by a `version_records` table in the engine's SQLite database: every append is written through and the full history (numbering, metadata, parent-hash chain) is reloaded on startup. `docs/SELF_HOST.md` updated — the in-memory limitation it disclosed is gone.
  - *Dashboard:* every nav item is now a real view over a real backend. New: **Decision Journal** (list/read/write engine `decision_entry` records), **Knowledge Base** (list + keyword search), **Holdings** (read-only table), **Carry Analysis** (wired to the existing carry endpoint). Removed from the nav, named rather than left as "Coming soon" promises: Correlation (no endpoint exposes per-holding return series to the dashboard), Bias Detection (no backend logic), Calibration (library-only, over the separate analytics-side journal), Tax Analyzer (no backend).
  - *Analytics:* the PKE knowledge base previously had no HTTP surface at all (library only, no MCP tool). Added read-only `GET /api/v1/knowledge/chunks` and `GET /api/v1/knowledge/search`; encrypted bases list metadata with text locked, and search reports `locked` instead of erroring. Ingest remains a library operation — there is deliberately no write endpoint.
- **Native bundle packaging (owner ruling, 2026-10-26): no Docker, anywhere.** PAE now ships as a native bundle per OS/arch — the Rust engine binary (which also serves the dashboard itself via `PAE_UI_DIR`, with an SPA fallback, and proxies `/api/v1/analytics/*` + `/api/v1/dashboard/*` to the analytics service so the product is a single origin), the analytics service as a PyInstaller one-folder bundle built from the pinned `analytics/requirements-lock.txt`, the static UI, a `pae` launcher (start|stop|status|seed|logs|serve), and installers for Linux/macOS/Windows (`scripts/build-bundle.sh`, `docs/PACKAGING.md`, `docs/SELF_HOST.md`). The engine now binds loopback by default (`PAE_ENGINE_HOST` overrides). All container packaging from the Phase 3A draft (Dockerfiles, compose, nginx, entrypoint rebase) and the legacy `infra/docker/` stack are removed.
- **Fleet keys adopted (owner direction, 2026-10-26)**: PAE uses the API keys the fleet already holds. FRED serves through the keyed API when `PAE_FRED_API_KEY` is set (the fleet's existing FRED key arms PAE unchanged; keyless CSV remains the no-key path; the key never appears in logs, errors, or results). New EIA adapter (`pae.data.eia`) serves energy spot prices (WTI, Brent, Henry Hub) with the fleet's EIA key (`PAE_EIA_API_KEY`, Secure Vault custom.eia) -- commodities moves GAP -> CONDITIONAL (energy adopted; licensed benchmarks still wait). EDGAR needs and has no key (keyless by design). Gap exception renamed `DataProviderGapError`.

### Added — Decision 6 data provider (first source, free use verified)
- `pae/data/registry.py`: the ruled source registry per data class.
- SEC EDGAR adapter (`fundamentals_us`, adopted): submissions/XBRL company facts, fair-access paced, provenance `sec-edgar`.
- Bank of Canada Valet adapter (`rates_fx_cad`, adopted): rates/FX with Bank attribution and the Terms s.1.3 free-availability notice constant.
- FRED allowlist: only Federal-Reserve-owned series are served; third-party-owned series (BLS CPI/unemployment, BEA GDP, S&P 500, Cboe VIX, ICE/BofA) are refused with the owner named.
- Yahoo-class data (prices, quotes, commodities, search) is development-only behind `PAE_DEV_DATA=1`; product mode raises the Decision 6 gap. User-supplied series via import remain the product path for prices.
- `docs/DATA_SOURCES.md`: the terms-evidence register behind these rulings.

## [0.2.0] - 2026-10-26

Phase 2 — "make it true": the Phase 1 audit's findings closed at code level.

### Added
- **Snapshot engine implemented** (`engine/src/versioning/snapshot.rs`): point-in-time
  snapshots over the append-only version store, plus snapshot diff (added / removed /
  modified). New endpoint `POST /api/v1/version/diff`; `POST /api/v1/version/snapshot`
  now returns real state instead of an empty list.
- `LICENSING.md` + `LICENSES/` — the layered licence map (owner ruling 2026-10-26):
  engine/analytics core/dashboard AGPL-3.0-or-later, common/MCP-exposed surface
  Apache-2.0, commercial licensing available on both tracks. Root `LICENSE` is now
  the map.
- `scripts/check-guardrail.sh` + CI step: fails the build if an advice-implying label
  ("optimal" / "recommended") appears in a user-facing surface.
- `site/privacy.html` — privacy page for the product site (ciphertext-only storage,
  no collection in the self-hosted edition).
- `docs/BENCHMARKS.md` — measured engine timings (method + environment stated).

### Changed
- **Copyright holder aligned to 2351485 ALBERTA INC.** across `LICENSE`, `NOTICE.md`,
  and all first-party source headers; manifests carry precise SPDX expressions
  (`AGPL-3.0-or-later`; MCP surface files carry `Apache-2.0`).
- Version bump: 0.1.1 → 0.2.0 (engine, analytics, UI).
- `docs/ARCHITECTURE.md`: Rust engine declared the system of record for holdings,
  portfolios and imports; Python `storage/` and the duplicate `data/csv_import.py`
  marked legacy.
- Product site: recovery copy corrected (Shamir 3-of-5 and the DEK envelope exist);
  footer now carries the company ownership line and drops the referral code.
- README: import claims corrected — file-based imports only (CSV incl. IBKR Flex
  export files), no live brokerage connections; multi-currency scoped to per-currency
  tracking (no FX conversion engine); latency claim replaced with the benchmarks doc.

### Removed
- Guardrail-breaching wording: "optimal mixes" labels in the optimizer UI, chart
  titles, and MCP tool descriptions (CONTRIBUTING rule 5).
- Threat model's Plaid/broker-token section — no broker integrations exist; the
  section now records non-applicability and the conditions for re-adding it.
- `TERMS.md` contact corrected to info@aimlds.org (was a different company's address).


### Added
- `NOTICE.md` — project attribution ("Owned by Nrupal Akolkar · Built with Milo
  (Town AI Assistant)") and license reference (AGPL-3.0).
- Portfolio certification: `CHANGELOG.md` created (this file; version history starts
  here — no past entries fabricated); PR-flow discipline added to `CONTRIBUTING.md`
  (draft PR → tests green → owner merges; no direct pushes to `main`; every PR adds
  its entry under Unreleased; semver bumps; releases tagged `vX.Y.Z`).
- AGPL-3.0 SPDX license headers on all first-party source files (Rust, C, Python,
  TypeScript, JavaScript); vendored third-party code under `ui/vendor/` untouched.

### Changed
- Version bump (chore): `analytics/pyproject.toml`, `engine/Cargo.toml`,
  `ui/package.json` — 0.1.0 → 0.1.1.
