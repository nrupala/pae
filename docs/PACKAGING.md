<!-- Copyright (C) 2026 2351485 ALBERTA INC. -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

# Packaging PAE — the native bundle

PAE ships as a **native bundle per OS/architecture**. There is no
container packaging anywhere in this repository: the target machine
needs no Docker, no Python, no Node, and no Rust — just the bundle.

## Layout

`scripts/build-bundle.sh` produces `dist-bundle/pae-<version>-<os>-<arch>/`
(plus a `.tar.gz` of it):

```
pae-<version>-<os>-<arch>/
  pae                  launcher (POSIX sh): start|stop|restart|status|seed|logs|serve|version
  pae.ps1, pae.cmd     launcher (Windows)
  install.sh           installer (Linux/macOS)
  install.ps1          installer (Windows)
  bin/
    pae-engine         Rust engine, a single native binary
    pae-seed           frozen demo-seed tool (scripts/seed_demo.py)
  analytics/
    pae-analytics      PyInstaller one-folder executable (+ _internal/)
  ui/                  dashboard static files, served by the engine
  SELF_HOST.md         the self-host user guide
  LICENSE, LICENSING.md, NOTICE.md
  VERSION
```

## How each piece is built

| Piece | Build | Why |
|-------|-------|-----|
| engine | `cargo build --release --locked` | One static-ish native binary; also serves the dashboard (`PAE_UI_DIR`) and proxies `/api/v1/analytics/*` + `/api/v1/dashboard/*` to analytics, so the installed product is a single origin with no separate web server. |
| UI | `npm ci` + `tsc`, then assembly | `dist/` JS output plus the static assets that live beside the sources (`index.html`, `manifest.json`, `sw.js`, `styles/`, `vendor/` — the vendor tree must sit next to the compiled output; see `ui/src/crypto/vault-client.ts`). No build-time rewriting of the compiled JS: the bundle is served same-origin by the engine instead. |
| analytics | PyInstaller, **one-folder** mode (`analytics/pae-analytics.spec`) | Carries its own Python interpreter and dependencies; one-folder (not one-file) so startup is fast and the layout is inspectable. Entry point is `analytics/run_frozen.py`, which serves the same `pae.server:app` object via uvicorn on loopback. |
| seed | PyInstaller, one-file | `scripts/seed_demo.py` is stdlib-only; freezing it means `pae seed` works on machines with no Python at all. |

## Reproducibility: the pinned dependency set

`analytics/requirements-lock.txt` is a `pip freeze` of the reference
environment that passes the analytics test suite. The PyInstaller
build must run inside an environment installed from exactly those
pins, so the frozen service carries the same dependency versions the
tests ran against. To recreate it:

```bash
python3.12 -m venv venv
venv/bin/pip install -r analytics/requirements-lock.txt
venv/bin/pip install -e analytics
venv/bin/pip install pyinstaller   # build-time only
```

Deliberate exclusions from the frozen bundle (see the spec):
`fastembed` / `onnxruntime` are absent from the locked environment and
`pae.pke` imports fastembed lazily (semantic search only), so the
frozen service matches the tested environment exactly. `sqlite-vec`'s
native extension is collected wholesale — the PKE store needs it at
runtime.

The Rust build is lockfile-pinned (`Cargo.lock`, `--locked`) and the
UI build is lockfile-pinned (`package-lock.json`, `npm ci`).

One runtime dependency to know about: the engine binary links the
system BLAS/LAPACK for its C numerical core (`build.rs`), so on Linux
it needs the system `libblas.so.3` (present on any mainstream distro;
`ldd bin/pae-engine` shows exactly what a given build resolved). The
frozen analytics carries everything it needs inside `_internal/`.

## Building

Prerequisites on the *build* machine: cargo, node + npm, and the
locked Python environment above.

```bash
PAE_BUILD_PYTHON=/path/to/venv/bin/python scripts/build-bundle.sh
# or: make bundle
```

The script builds the engine, the UI, the frozen analytics, and the
frozen seed; assembles the bundle directory; and writes the tarball.
A bundle is only valid for the OS/architecture it was built on —
build one per target.

## Running the bundle you built

```bash
./dist-bundle/pae-*/install.sh     # or install.ps1 on Windows
pae start
pae seed                           # fictional demo data
# open http://localhost:3001
```

`make demo` runs the whole flow against a scratch prefix and data
directory under `dist-bundle/` so a build can be smoke-tested without
touching a real install.
