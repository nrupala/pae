# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- **Phase 3C (part 1) — proof + hardening.**
  - *Known-answer test vectors:* the risk and analytics math is now pinned to values computed independently of the code — VaR/CVaR and Sharpe/volatility/drawdown on hand-computed series, Monte Carlo checked against its exact analytic terminal distribution within stated sampling-error tolerances, stress and bond duration/YTM textbook vectors (Rust), and Fama-French-style OLS recovery of known coefficients plus hand-computed carry cases (Python). No discrepancies found; the engine's VaR indexing convention (`floor(alpha·n)` into the ascending sort) is flagged in the tests as a product decision to confirm, not silently endorsed.
  - *Agent token (security):* the A2A/agent surfaces (analytics) and the engine's caller-supplied-key `/api/v1/crypto/encrypt` + `/decrypt` endpoints now require a per-install bearer token (`Authorization: Bearer`), generated on first start into an owner-only (`0600`) file in the PAE data dir. No token configured = those surfaces stay closed (there is no open fallback). Dashboard and data routes unchanged. See "The agent token" in `docs/SELF_HOST.md`.
  - *Journal encryption:* dashboard journal entries are now genuinely encrypted in the browser (vault unlock with the user's passphrase; AES-256-GCM via WebCrypto) before the engine stores them — the Phase 3B base64/zero-nonce stand-in is removed. Pre-3C entries still read and are labelled as stored unencrypted.
  - *Journal metadata (security follow-up, found on independent review):* the first 3C build still sent the entry title as the record's `change_summary`, which the versioning API stores as plaintext metadata — a plaintext copy of the title next to the ciphertext. New entries now send no `change_summary` (the title lives only inside the encrypted content); the list shows decrypted titles when the vault is unlocked and a neutral "Encrypted entry" label when locked. Entries the first 3C build already wrote keep their stored summary until rewritten. Tags and dates remain plaintext metadata by design, and the write form now says so where tags are typed.
  - *KDF iterations (security/correctness fix):* the engine advertised Argon2id with 600,000 iterations — a PBKDF2-scale pass count on an algorithm whose time cost is meant to be single-digit. At the advertised 64 MiB per pass, client-side derivation never completed in practice, leaving the browser vault create/unlock flow unusable. The advertised parameters are now the RFC 9106 first-recommended Argon2id profile (64 MiB, 3 iterations, 4 lanes), matching the memory and parallelism that were already set. Docs that cited the old count (README, ARCHITECTURE, THREAT_MODEL, FUNCTION_REFERENCE) are aligned.
  - *DEK-envelope replacement guard (security fix, found on independent review):* `POST /api/v1/crypto/dek-envelope` stored the vault's wrapped data key insert-or-replace, unauthenticated — one request could irreversibly overwrite the only wrapped copy of the DEK and permanently brick every encrypted record. The first write (vault creation, which the browser flow performs before any credential exists) stays open; once an envelope exists, replacing it requires the per-install agent token (`Authorization: Bearer`), and anything else is refused with `409 DEK_ENVELOPE_EXISTS`, the stored envelope untouched. Token-presenting replacement is the documented rotation path (docs/SELF_HOST.md, docs/THREAT_MODEL.md).
  - *Agent-token path parity (fix, found on independent review):* the analytics service expands a leading `~` in `PAE_AGENT_TOKEN_PATH` / `PAE_HOME` (Python `expanduser`), but the engine used the value literally — with a tilde in either variable the two processes resolved different token files and agent auth desynchronized between them. The engine now expands a leading `~` / `~/` against `$HOME` exactly as the analytics side does (`~user` remains unexpanded on both sides' per-install paths).
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
