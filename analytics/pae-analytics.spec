# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# PyInstaller spec for the standalone analytics service (one-folder).
#
# Build (from this directory, inside the locked environment — see
# requirements-lock.txt and docs/PACKAGING.md):
#
#   python -m PyInstaller --noconfirm pae-analytics.spec
#
# Output: dist/pae-analytics/pae-analytics[.exe] + _internal/.
# scripts/build-bundle.sh runs this and assembles the release bundle.
#
# Notes on the dependency set:
# - sqlite-vec ships a native loadable extension inside the package;
#   collect it wholesale or the PKE store fails at runtime.
# - fastembed / onnxruntime are deliberately NOT bundled: they are
#   absent from the locked environment (requirements-lock.txt) and
#   pae.pke imports fastembed lazily, only for semantic search. The
#   frozen service matches the tested environment exactly.

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

for pkg in ("sqlite_vec",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# uvicorn picks its protocol/loop implementations dynamically.
hiddenimports += [
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.loops.asyncio",
    "uvicorn.lifespan.on",
]

a = Analysis(
    ["run_frozen.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["fastembed", "onnxruntime", "tkinter", "matplotlib"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pae-analytics",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="pae-analytics",
)
