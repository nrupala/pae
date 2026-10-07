# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
