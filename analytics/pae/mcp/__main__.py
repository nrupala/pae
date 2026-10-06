# Copyright (C) 2026 Nrupal Akolkar
# SPDX-License-Identifier: AGPL-3.0-or-later

"""PAE MCP server entry point: ``python -m pae.mcp`` (stdio transport)."""

from __future__ import annotations

import os

from pae.mcp.server import DEFAULT_DB_PATH, create_mcp_server
from pae.storage.db import PAEDatabase, Portfolio


def main() -> None:
    """Serve the PAE MCP server over stdio."""
    db = PAEDatabase(os.environ.get("PAE_DB_PATH", DEFAULT_DB_PATH))
    db.initialize()
    if not db.get_portfolios():
        db.insert_portfolio(Portfolio(name="Default"))
    server = create_mcp_server(db=db)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
