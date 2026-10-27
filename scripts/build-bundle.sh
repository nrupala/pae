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
(cd "$OUT" && tar czf "$NAME.tar.gz" "$NAME")
rm -rf "$WORK"

echo ""
echo "Bundle ready:"
echo "  $STAGE"
echo "  $OUT/$NAME.tar.gz"
du -sh "$STAGE" "$OUT/$NAME.tar.gz" | sed 's/^/  /'
