#!/bin/sh
# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Build the PAE native release bundle for this OS/architecture.
#
#   scripts/build-bundle.sh [output-dir]
#
# Output (default: dist-bundle/):
#   pae-<version>-<os>-<arch>/     the bundle directory
#   pae-<version>-<os>-<arch>.tar.gz
#
# Bundle layout:
#   pae, pae.ps1, pae.cmd   launcher (start|stop|status|seed|logs)
#   install.sh, install.ps1 installers
#   bin/pae-engine          Rust engine (native binary)
#   bin/pae-seed            frozen demo-seed tool
#   analytics/              PyInstaller one-folder analytics service
#   ui/                     dashboard static files (served by the engine)
#   SELF_HOST.md            the self-host guide
#   VERSION
#
# Prerequisites (build machine only — the bundle itself needs
# nothing): cargo, node + npm, and a Python with PyInstaller in an
# environment installed from analytics/requirements-lock.txt.
# Point PAE_BUILD_PYTHON at that interpreter if it is not `python3`.
# See docs/PACKAGING.md.

set -eu

REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${1:-"$REPO/dist-bundle"}
PYTHON=${PAE_BUILD_PYTHON:-python3}

VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/engine/Cargo.toml" | head -1)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case $OS in
    mingw*|msys*|cygwin*) OS=windows ;;
esac
EXE=""
[ "$OS" = "windows" ] && EXE=".exe"

NAME="pae-$VERSION-$OS-$ARCH"
STAGE="$OUT/$NAME"
WORK="$OUT/.build-$NAME"

echo "Building $NAME"
echo "  repo:   $REPO"
echo "  stage:  $STAGE"
echo "  python: $PYTHON"

# --- Preflight ------------------------------------------------------------
# Fail in seconds, naming the fix, instead of dying ten minutes in at
# the PyInstaller freeze (the failure the Phase 3D buyer walk hit by
# running the bare script with the system python).
command -v cargo >/dev/null 2>&1 || {
    echo "error: cargo not found on PATH — install Rust (rustup) first." >&2
    exit 1
}
command -v npm >/dev/null 2>&1 || {
    echo "error: npm not found on PATH — install Node.js first." >&2
    exit 1
}
if ! PYTHONPATH="$REPO/analytics" "$PYTHON" -c \
        "import PyInstaller, pae.server" >/dev/null 2>&1; then
    echo "error: $PYTHON cannot build the analytics bundle:" >&2
    echo "PyInstaller or the locked analytics dependencies are missing." >&2
    echo "Create the build environment and point PAE_BUILD_PYTHON at it:" >&2
    echo "  python3.12 -m venv venv" >&2
    echo "  venv/bin/pip install -r analytics/requirements-lock.txt" >&2
    echo "  venv/bin/pip install -e analytics" >&2
    echo "  venv/bin/pip install pyinstaller   # build-time only" >&2
    echo "  PAE_BUILD_PYTHON=\$PWD/venv/bin/python scripts/build-bundle.sh" >&2
    exit 1
fi

rm -rf "$STAGE" "$WORK"
mkdir -p "$STAGE/bin" "$STAGE/ui" "$WORK"

# --- 1. Engine (Rust, native binary) -------------------------------------
echo "==> engine: cargo build --release --locked"
(cd "$REPO/engine" && cargo build --release --locked)
cp "$REPO/engine/target/release/pae-engine$EXE" "$STAGE/bin/pae-engine$EXE"

# --- 2. Dashboard (static files) ------------------------------------------
echo "==> ui: npm ci + tsc, assembling static files"
(cd "$REPO/ui" && npm ci && npm run clean && npm run build)
cp -r "$REPO/ui/dist/." "$STAGE/ui/"
cp "$REPO/ui/src/index.html" "$REPO/ui/src/manifest.json" "$REPO/ui/src/sw.js" "$STAGE/ui/"
cp -r "$REPO/ui/src/styles" "$STAGE/ui/styles"
cp -r "$REPO/ui/vendor" "$STAGE/ui/vendor"

# --- 3. Analytics (PyInstaller one-folder) --------------------------------
echo "==> analytics: PyInstaller (locked environment)"
(cd "$REPO/analytics" && PYTHONPATH="$REPO/analytics" "$PYTHON" -m PyInstaller \
    --noconfirm \
    --distpath "$WORK/pyi-dist" \
    --workpath "$WORK/pyi-work" \
    pae-analytics.spec)
mkdir -p "$STAGE/analytics"
cp -r "$WORK/pyi-dist/pae-analytics/." "$STAGE/analytics/"

# --- 4. Demo seed (PyInstaller one-file; the seed is stdlib-only) ---------
echo "==> seed: PyInstaller one-file"
"$PYTHON" -m PyInstaller --noconfirm --onefile \
    --name pae-seed \
    --distpath "$WORK/seed-dist" \
    --workpath "$WORK/seed-work" \
    --specpath "$WORK" \
    "$REPO/scripts/seed_demo.py"
cp "$WORK/seed-dist/pae-seed$EXE" "$STAGE/bin/pae-seed$EXE"

# --- 5. Launcher, installers, docs ----------------------------------------
cp "$REPO/scripts/launcher/pae" "$STAGE/pae"
cp "$REPO/scripts/launcher/pae.ps1" "$STAGE/pae.ps1"
cp "$REPO/scripts/launcher/pae.cmd" "$STAGE/pae.cmd"
cp "$REPO/scripts/install.sh" "$STAGE/install.sh"
cp "$REPO/scripts/install.ps1" "$STAGE/install.ps1"
cp "$REPO/docs/SELF_HOST.md" "$STAGE/SELF_HOST.md"
cp "$REPO/LICENSE" "$REPO/LICENSING.md" "$REPO/NOTICE.md" "$STAGE/"
echo "$VERSION" > "$STAGE/VERSION"

chmod +x "$STAGE/pae" "$STAGE/install.sh" \
         "$STAGE/bin/pae-engine$EXE" "$STAGE/bin/pae-seed$EXE" \
         "$STAGE/analytics/pae-analytics$EXE" 2>/dev/null || true

# --- 6. Archive -------------------------------------------------------------
# Portable ownership: root:root numeric ids, never the build machine's
# uid/gid — untarring as root (or any user) must be clean (Phase 3D W8;
# GNU tar flags, which is what the Linux/WSL build hosts run).
(cd "$OUT" && tar czf "$NAME.tar.gz" --owner=0 --group=0 --numeric-owner "$NAME")
rm -rf "$WORK"

echo ""
echo "Bundle ready:"
echo "  $STAGE"
echo "  $OUT/$NAME.tar.gz"
du -sh "$STAGE" "$OUT/$NAME.tar.gz" | sed 's/^/  /'
