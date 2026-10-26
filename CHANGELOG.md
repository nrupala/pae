# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
