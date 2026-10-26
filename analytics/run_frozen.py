# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Entry point for the standalone (PyInstaller) analytics bundle.

The native PAE bundle ships the analytics service as a frozen
executable (see pae-analytics.spec and docs/PACKAGING.md) so the
target machine needs no Python installation. This module is the
frozen entry: it serves the same FastAPI app object as
``uvicorn pae.server:app``, on the loopback interface by default.

Environment:
    PAE_ANALYTICS_HOST  bind host (default 127.0.0.1)
    PAE_ANALYTICS_PORT  bind port (default 3002)
    PAE_DB_PATH         analytics SQLite path (set by the launcher)
    PAE_ENGINE_URL      engine base URL (set by the launcher)
"""

import os

import uvicorn

from pae.server import app


def main() -> None:
    host = os.environ.get("PAE_ANALYTICS_HOST", "127.0.0.1")
    port = int(os.environ.get("PAE_ANALYTICS_PORT", "3002"))
    # Passing the app object (not an import string) is what makes this
    # work frozen: there is no module path for uvicorn to re-import.
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
