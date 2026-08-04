# PLAN — Wire the Decision Journal end-to-end

**Status: PLAN — awaiting Nrupal's approval. No build until this is approved and merged.**
Slice owner: engine opens branch + PR; Nrupal merges. Phased delivery: this is the PLAN step; BUILD follows only on go.

Date: 2026-08-04 · Repo: `nrupala/pae` · Area: `analytics/` (Python) · Master spec: PAE v2 §9.1, §12, §15 (Layer 3), §20.8.

---

## 1. Why this slice

`analytics/pae/decision/journal.py` is **already written and orphaned.** It defines `DecisionEntry`, `validate_entry()`, `compute_calibration()`, and `CalibrationMetric` — but:

- it is **not persisted** (no table in `storage/db.py`, no DAO methods), and
- it is **not reachable** (no endpoint in `server.py`).

So the module is dead code the UI cannot use. This slice turns it into a working feature by wiring persistence + API around the existing pure logic. It is the smallest useful step into **Layer 3 — Decision Intelligence** (v2 §15), the spec's single biggest differentiator, and it advances entirely **box-free and data-free** (no OCI box, no market feed, no options — none of the box-blocked or data-gated surface).

**Leverage:** we reuse `journal.py` as-is; the work is the SQLite table, the DAO, the endpoints, and tests. High output per unit of build.

---

## 2. Scope

**In scope**
1. A `journal_entries` table + DAO methods in `storage/db.py`, mirroring the existing `Holding` pattern (dataclass ⇄ row, `_transaction()`, `ValidationError`, JSON-string columns for list fields).
2. Journal endpoints in `server.py`: create, get, list, record-outcome, delete, and **calibration** (reusing `compute_calibration()` unchanged).
3. One additive model tweak: an optional `portfolio_id` on `DecisionEntry` so a decision can be scoped to a portfolio (default `""` → backward-compatible; enables per-portfolio calibration).
4. Unit + API tests.

**Out of scope (named so they are not silently assumed)**
- The bias detector, pre-mortem, second-order, regret-minimization, behavioral-mining modules (v2 §9.1/§9.3) — separate later slices.
- Any UI/Web-Component work (`ui/`) — this slice stops at the API; the UI slice is a follow-up.
- Client-side encryption of the free-text content (see §5, ZK note) — staged, not built here.
- The Rust engine — untouched.

---

## 3. Design

### 3.1 Storage — `storage/db.py`

Add a `DecisionRecord` persistence mapping for `journal.py`'s `DecisionEntry`. (Keep `DecisionEntry` as the domain object in `decision/journal.py`; the DAO reads/writes it directly, mapping `entry_id`/`timestamp` to the row's `id`/`created_at`-style columns — same as `Holding`.)

New table (created in `initialize()` via `CREATE TABLE IF NOT EXISTS`, purely additive; bump `SCHEMA_VERSION` 1 → 2):

```sql
CREATE TABLE IF NOT EXISTS journal_entries (
    entry_id             TEXT PRIMARY KEY,
    portfolio_id         TEXT NOT NULL DEFAULT '',        -- optional scope; '' = unscoped
    timestamp            TEXT NOT NULL,                   -- ISO 8601 UTC
    action               TEXT NOT NULL DEFAULT '',
    symbols_affected     TEXT NOT NULL DEFAULT '[]',      -- JSON array (like returns_json)
    rationale            TEXT NOT NULL DEFAULT '',
    alternatives         TEXT NOT NULL DEFAULT '[]',      -- JSON array
    thesis               TEXT NOT NULL DEFAULT '',
    confidence           INTEGER NOT NULL,                -- 1..10, REQUIRED (no default → §20.3)
    time_horizon         TEXT NOT NULL DEFAULT '',
    what_could_go_wrong  TEXT NOT NULL DEFAULT '',
    max_acceptable_loss_pct REAL NOT NULL DEFAULT 0.0,
    emotional_state      TEXT NOT NULL DEFAULT 'neutral',
    market_context       TEXT NOT NULL DEFAULT '',
    trigger              TEXT NOT NULL DEFAULT '',
    outcome_30d          REAL,                            -- NULL until measured
    outcome_90d          REAL,
    outcome_180d         REAL,
    outcome_notes        TEXT NOT NULL DEFAULT '',
    was_thesis_correct   INTEGER,                         -- NULL / 0 / 1
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_journal_portfolio ON journal_entries(portfolio_id);
CREATE INDEX IF NOT EXISTS idx_journal_confidence ON journal_entries(confidence);
```

Note: `portfolio_id` FK allows `''` (unscoped) — the FK only constrains non-empty values that must exist; to keep SQLite FK semantics simple we store `''` and skip the FK check for empty via app-level rule, OR make the column nullable. Build detail: use nullable `portfolio_id TEXT` with `NULL` = unscoped (cleaner FK semantics). Finalize in build.

New DAO methods (same shape as the Holding CRUD):
- `insert_decision(entry: DecisionEntry) -> DecisionEntry` — runs `validate_entry()` first; raises `ValidationError` on any message.
- `get_decisions(portfolio_id: str | None = None, limit: int | None = None) -> list[DecisionEntry]`
- `get_decision_by_id(entry_id: str) -> DecisionEntry` — `NotFoundError` if missing.
- `update_decision_outcome(entry_id, *, outcome_30d=None, outcome_90d=None, outcome_180d=None, outcome_notes=None, was_thesis_correct=None) -> DecisionEntry` — patch semantics; only provided fields change.
- `delete_decision(entry_id: str) -> None`
- List fields (`symbols_affected`, `alternatives_considered`) JSON-encoded on write / decoded on read, exactly like `returns_json`.

### 3.2 API — `server.py`

Add a `# --- Decision Journal ---` section (pure Python-native, no Rust engine, mirrors the `carry` endpoint style). Pydantic request models with **no advisory defaults**; `confidence` is required.

| Method + path | Purpose |
|---|---|
| `POST /api/v1/decisions` | Create an entry. Body = decision fields (`confidence` required). Returns `{entry_id}`. |
| `GET /api/v1/decisions` | List entries (`portfolio_id`, `limit` query). Returns metadata + content. |
| `GET /api/v1/decisions/{entry_id}` | Fetch one. |
| `PUT /api/v1/decisions/{entry_id}/outcome` | Record 30/90/180-day outcomes, `was_thesis_correct`, `outcome_notes`. |
| `DELETE /api/v1/decisions/{entry_id}` | Delete one. |
| `GET /api/v1/decisions/calibration` | Return `compute_calibration()` over completed entries (`portfolio_id` query optional). |

`compute_calibration()` is used **unchanged** — the endpoint just fetches `get_decisions(...)` and passes them in.

### 3.3 Guardrail compliance (v2 §20)

- **§1 no recommendation engine / §7 observe-not-prescribe:** the journal stores the user's OWN rationale/confidence/outcomes; `compute_calibration()` is pure observation ("at confidence 8+, your 90-day accuracy is X%"). No endpoint emits buy/sell/allocate or an "optimal" label.
- **§8 opt-in:** endpoints are inert until the user posts an entry; nothing auto-journals.
- **§3 no advisory defaults:** `confidence` is required (no silent midpoint); other fields default to neutral/empty only.

---

## 4. Test plan

- `tests/test_journal_db.py` — insert → get → list(filter by portfolio) → update-outcome (patch) → delete round-trip; `validate_entry` rejection paths (bad confidence, bad emotional_state, non-finite outcome); calibration over persisted entries matches `compute_calibration()` on the same in-memory list.
- `tests/test_server_decisions.py` — FastAPI `TestClient`: create → get → list → outcome → calibration → delete happy path; 404 on missing id; 400 on invalid confidence. Uses a temp SQLite file (or `:memory:`) via the existing lifespan/`get_db()` seam.

(If `httpx`/`TestClient` isn't already a dev dep in `analytics/pyproject.toml`, add it under dev deps — flagged as a build sub-task.)

---

## 5. Constraints & notes

- **Zero-knowledge (v2 §4, clause 3):** calibration only ever needs **numeric metadata** (`confidence`, `outcome_90d`) — never the free-text content. That means the sensitive fields (`rationale`, `thesis`, `alternatives`, `what_could_go_wrong`, `market_context`, `outcome_notes`) can later become a **client-encrypted ciphertext blob** without breaking calibration. This slice stores them as plaintext columns to match the CURRENT holdings implementation; a `# ZK-TODO` marks the content fields as the future encrypted-blob boundary. **No new plaintext-exposure guarantee is claimed beyond what holdings already do today.**
- **No Docker; vanilla stack:** pure stdlib `sqlite3` + existing FastAPI; no new heavy deps (only `httpx` for tests, if missing).
- **Additive / non-breaking:** new table + new endpoints only; existing tables, endpoints, and the Rust proxy are untouched. Existing DBs upgrade by `CREATE TABLE IF NOT EXISTS` on next `initialize()`.

## 6. Acceptance criteria (verify-before-done)

1. `ruff` clean on changed files; type-check clean (repo's configured checker).
2. `pytest` green — existing suite + the two new test files.
3. Offline smoke (sandbox, no box, no network): start the app against a temp DB, `POST` two decisions with confidence 9 and 3, `PUT` their 90-day outcomes, `GET /calibration` → returns the three buckets with correct counts.
4. Screenshot/paste of the smoke output in the BUILD PR.

## 7. File-by-file change list (for the BUILD PR)

- `analytics/pae/storage/db.py` — `journal_entries` DDL in `initialize()`; `SCHEMA_VERSION` → 2; 5 DAO methods; JSON encode/decode for the two list fields.
- `analytics/pae/decision/journal.py` — add optional `portfolio_id: str = ""` to `DecisionEntry` (additive); no logic change to `validate_entry`/`compute_calibration`.
- `analytics/pae/server.py` — Pydantic request models + 6 endpoints in a new Decision Journal section.
- `analytics/tests/test_journal_db.py` — new.
- `analytics/tests/test_server_decisions.py` — new.
- `analytics/pyproject.toml` — add `httpx` dev dep if absent.
- `README.md` / `CHANGELOG` — note the journal is now wired end-to-end (if the repo keeps a changelog; confirm in build).

## 8. Risks

- **FK on empty `portfolio_id`** — resolved by making the column nullable (`NULL` = unscoped) rather than `''`. Decided at build.
- **TestClient dep** — may need `httpx` added; low risk, standard.
- **Schema-version drift** — new table only; no destructive migration. `schema_version` row bumped to 2 via `INSERT OR REPLACE`.

---

**Next action on approval:** create `feat/decision-journal-wiring` off `main`, implement the file-by-file list, run the gate + offline smoke in the sandbox, and open the BUILD PR for your merge. Nothing builds until you say go.
