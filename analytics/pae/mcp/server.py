"""MCP server for PAE.

Builds an ``mcp.server.mcpserver.MCPServer`` (the ``mcp>=2.0`` successor of
v1's ``FastMCP``) with one tool per entry in :data:`pae.mcp.tools.TOOL_SPECS`.
Tools are thin wrappers — the implementations live in
:class:`pae.mcp.tools.PAETools` and are shared with the A2A surface.

Entry point: ``python -m pae.mcp`` serves over stdio.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from pae.mcp.tools import DISCLOSURE, TOOL_SPECS, PAETools
from pae.storage.db import PAEDatabase

SERVER_NAME = "pae"
SERVER_VERSION = "0.1.0"
DEFAULT_DB_PATH = "data/pae.db"


def create_mcp_server(
    db: PAEDatabase | None = None,
    engine_url: str | None = None,
) -> MCPServer:
    """Create the PAE MCP server, registering every tool in TOOL_SPECS.

    When no database is supplied, one is opened at ``PAE_DB_PATH``
    (default ``data/pae.db``) — the same convention as the FastAPI server —
    but NOT initialized: the caller owns ``initialize()`` (the stdio entry
    point does it; the tool manifest needs no database at all).
    """
    database = db
    if database is None:
        database = PAEDatabase(os.environ.get("PAE_DB_PATH", DEFAULT_DB_PATH))

    tools = PAETools(database, engine_url=engine_url)
    server = MCPServer(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        description=(
            "PAE (Personal Analytics Engine) — educational investment "
            "analytics for individuals: risk, factors, scenarios, decision "
            "journal. " + DISCLOSURE
        ),
    )
    for tool_name, description in TOOL_SPECS:
        server.add_tool(
            getattr(tools, tool_name),
            name=tool_name,
            description=description,
            structured_output=True,
        )
    return server


async def get_tool_manifest() -> list[dict[str, Any]]:
    """Machine-readable tool manifest, sourced from the MCP tool definitions.

    Single source of truth: the registered MCPServer tools (names,
    descriptions, JSON input schemas).
    """
    server = create_mcp_server()
    manifest: list[dict[str, Any]] = []
    for tool in await server.list_tools():
        manifest.append(
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
        )
    return manifest
