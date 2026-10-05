"""PAE agent tool layer (MCP + A2A).

Single implementation of every PAE agent tool. The MCP server
(:mod:`pae.mcp.server`) registers these tools on an MCPServer; the A2A
message endpoint (``pae.server``) dispatches to the same tools by name via
:func:`run_agent_tool`. No math is duplicated here — tools are thin wrappers
over ``pae.models``, ``pae.decision``, ``pae.storage``, and the Rust engine.

Note: this codebase targets ``mcp>=2.0`` (verified against 2.3.0), where the
v1 ``FastMCP`` class is ``mcp.server.mcpserver.MCPServer``.
"""

from pae.mcp.server import create_mcp_server, get_tool_manifest
from pae.mcp.tools import (
    DISCLOSURE,
    TOOL_NAMES,
    TOOL_SPECS,
    EngineUnreachableError,
    PAEToolError,
    PAETools,
    run_agent_tool,
)

__all__ = [
    "DISCLOSURE",
    "TOOL_NAMES",
    "TOOL_SPECS",
    "EngineUnreachableError",
    "PAEToolError",
    "PAETools",
    "create_mcp_server",
    "get_tool_manifest",
    "run_agent_tool",
]
