# Codetopo Profile — PAE (nrupala/pae)

**What this is.** The agent-facing map of this repository, generated with
Codetopo 0.9.1 (local engine) — topology, trust boundaries, money
surface — with the graph snapshot beside it (`snapshot.json`,
`stats.json`) as the measured evidence.

- **Repo:** nrupala/pae (public)
- **Commit profiled:** `92f8a09ffd4d00a4d4133616c2ec82813ec90dac` (main)
- **Engine:** codetopo 0.9.1 + full-language program (engine built from codetopo main `d71509d1` — all sixteen program extractors merged) · index run 2026-11-19
- **Graph:** 133 files walked · 0 failed · 4,813 nodes · 15,888 edges · audit chain valid (3 entries, head `5b03f1a1…952e`)
- **Nodes by kind:** external 2,698 · function 861 · variable 401 · method 341 · class 182 · file 133 · module 131 · interface 40 · doc 22 · package 4
- **Edges by kind:** calls 6,584 · reads 5,426 · contains 2,111 · defines 1,075 · imports 428 · writes 259 · inherits 4 · implements 1

## Coverage honesty

**Re-profiled 2026-11-19** with the full-language engine (Codetopo extractor program, 2026-11-18 ruling): every language this repo is written in is now extracted at symbol level. First profile: 2026-11-18 (Codetopo 0.9.1).

Extracted at symbol level: Python (54 `.py`), Rust (25 `.rs`) and TypeScript (18 `.ts`) as before, and now the C residual named in the first profile — the C sources and headers are in the graph (C extractor, program PR #155). The engine now covers every language of the full-language program. Not parsed: CSS/SVG assets and TOML configuration (data, not code). Shell scripts, if any, are extracted by the Shell extractor. Symbol coverage of the codebase is complete.
## Topology

PAE — the Personal Analytics Engine (OpenSSF project description):
institutional-grade financial analytics as a personal engine.

- `engine/` — the analytics engine (Python + Rust core): the graph's
  hub, where computations live.
- `knowledge/` — the knowledge layer the engine reasons over.
- `platforms/` — platform integrations/surfaces.
- `analytics/` — analytics jobs and outputs plumbing.
- `ui/` + `site/` — the TypeScript user surface and the public site.
- `tools/` + `scripts/` — operator tooling; `Makefile` at the root is
  the build entry; `llms.txt` is the agent-facing site summary.

## Trust boundaries

- **Personal-data boundary:** PAE works over the owner's own financial
  data — that data is the sensitive asset. It lives outside this repo
  (local stores, by path reference); no personal financial records,
  account numbers or credentials appear in this repo or this profile.
- **Computation integrity:** the Rust core exists so the numbers are
  deterministic and checkable; UI/analytics layers display, they do
  not recompute differently (read `engine/` before believing any
  number a surface shows).
- **External calls:** platform integrations (`platforms/`) are the
  only egress; provider keys are environment-held, by name only.
- **Blast radius:** `engine/` concentrates the call edges — an engine
  change moves every figure every surface displays.

## Money surface

**No payment surface found in code** (grep for stripe/paddle/checkout
over `.py`/`.ts`/`.rs`: no payment integration). PAE analyses money;
it does not take payments, hold billing credentials, or place orders.
Licence terms live in `LICENSING.md` / `TERMS.md` as documents, not
code paths.

## How an agent should approach

1. Read `README.md`, `LICENSING.md`, then this profile.
2. Never copy personal financial data into the repo, a profile, or a
   report — aggregates and shapes only, and only when asked.
3. Query the graph before touching `engine/`.
4. Regenerate this profile at every release.

## Regeneration

`docs/codetopo-profile/refresh.sh` (in this pack) re-fetches the repo
at a named ref, re-runs the index, and rewrites `snapshot.json`,
`stats.json` and `verify.txt`.

## Standing-condition lines for this change

- Documentation at par: this pack is the documentation change, same PR;
  no behaviour changed.
- Release gate: NOT RUN for this profile PR · Guardian Mesh approval:
  NOT RUN. Merging is not "shipped"; the pack enters the gate queue in
  the Stage-1 evidence pack.
- OpenSSF Best Practices: project 16401 registered (checked
  2026-11-18); this additive docs change does not degrade any
  criterion.
