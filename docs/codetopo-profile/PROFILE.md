# Codetopo Profile — PAE (nrupala/pae)

**What this is.** The agent-facing map of this repository, generated with
Codetopo 0.9.1 (local engine) — topology, trust boundaries, money
surface — with the graph snapshot beside it (`snapshot.json`,
`stats.json`) as the measured evidence.

- **Repo:** nrupala/pae (public)
- **Commit profiled:** `d73cb8109322b19be12e198b9124c12f4e4b28c8` (main)
- **Engine:** codetopo 0.9.1 · index run 2026-11-18
- **Graph:** 124 files walked · 4,715 nodes · 15,590 edges · audit chain valid
- **Nodes by kind:** function 842 · method 341 · class 182 · interface 40 · variable 374 · module 124 · file 124 · doc 21 · external 2,663 · package 4
- **Edges by kind:** calls 6,504 · reads 5,341 · contains 2,048 · defines 1,028 · imports 422 · writes 242 · implements 1 · inherits 4

## Coverage honesty

Extracted at symbol level: Python (54 `.py`), Rust (25 `.rs`) and
TypeScript (18 `.ts`) — the engine's three implementation languages —
plus Markdown as Doc nodes. Not parsed: C sources (~19 KB by GitHub's
count), CSS/SVG assets, TOML/shell. Symbol coverage is complete for
the three extracted languages; the small C residual is named here as
the gap (a C extractor is a Stage-3 program item).

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
