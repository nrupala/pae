# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Authentication for PAE's HTTP surfaces.

Two independent mechanisms live here:

1. **Agent token** (Phase 3C, F7 fix) — :func:`require_agent_token`
   guards the agent surfaces (A2A agent card + message endpoint,
   discovery, tool manifest). PAE is local-first, but "local" is not
   an identity: any process — or any website the user's browser
   visits, subject to CORS — could previously invoke agent tools,
   including writes (journal entries, holdings changes). The agent
   surfaces now require a per-install bearer token:

   - Resolution order: the ``PAE_AGENT_TOKEN`` environment variable
     wins; otherwise a token file — ``PAE_AGENT_TOKEN_PATH`` if set,
     else ``$PAE_HOME/data/agent-token``, else ``~/.pae/agent-token``.
   - The file is created on first use (analytics startup, or the
     first guarded call) with owner-only permissions (0600) and a
     256-bit random value; the engine resolves the SAME file, so one
     token per install serves both processes.
   - Requests must present ``Authorization: Bearer <token>``
     (constant-time comparison; 401 otherwise). There is no
     "unset means open" mode for the agent surfaces: if no token is
     configured, one is generated — the surfaces are never
     unauthenticated.

   The MCP server itself is stdio-only (``python -m pae.mcp``); it
   has no HTTP transport, so there is no MCP-over-HTTP path to guard.
   stdio callers are already local processes running as the user.

2. **Data-route API key** — :func:`require_api_key` guards the
   portfolio/holdings/analytics data routes with the operator-set
   ``PAE_API_KEY`` (unchanged; opt-in because the dashboard's
   same-origin calls predate it — set it before exposing analytics
   beyond loopback).
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException

AGENT_TOKEN_ENV = "PAE_AGENT_TOKEN"
AGENT_TOKEN_PATH_ENV = "PAE_AGENT_TOKEN_PATH"


def agent_token_path() -> Path:
    """Where the per-install agent token file lives (see module docstring)."""
    override = os.environ.get(AGENT_TOKEN_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    pae_home = os.environ.get("PAE_HOME", "").strip()
    if pae_home:
        return Path(pae_home).expanduser() / "data" / "agent-token"
    return Path.home() / ".pae" / "agent-token"


def get_or_create_agent_token() -> str:
    """Resolve the per-install agent token, generating the file if needed.

    Generation is race-safe (O_EXCL): two processes starting together
    converge on the single token written by whichever created the
    file first.
    """
    env_token = os.environ.get(AGENT_TOKEN_ENV, "").strip()
    if env_token:
        return env_token

    path = agent_token_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        return existing

    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process created it between our read and open.
        return path.read_text(encoding="utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")
    return token


async def require_agent_token(
    authorization: str | None = Header(default=None),
) -> None:
    """Enforcing auth dependency for the agent surfaces (A2A card +
    message endpoint, discovery, tool manifest).

    Requires ``Authorization: Bearer <agent token>`` — see the module
    docstring for how the token is resolved and where it lives.
    """
    expected = get_or_create_agent_token()
    provided = ""
    if authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer ") :].strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="A valid agent token is required (Authorization: Bearer <token>).",
        )
    return None


async def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Enforcing auth dependency for the portfolio/holdings/analytics
    data routes (declared via ``dependencies=[Depends(require_api_key)]``).

    The data routes serve the user's financial data: when the
    ``PAE_API_KEY`` environment variable is set, every data-route call
    must present it as ``Authorization: Bearer <key>`` or
    ``X-API-Key: <key>`` (constant-time comparison; 401 otherwise). When
    ``PAE_API_KEY`` is not set, the dependency passes — PAE is a
    local-first personal tool and the operator has not enabled remote
    access protection; set the variable before exposing the server
    beyond localhost.
    """
    expected = os.environ.get("PAE_API_KEY", "")
    if not expected:
        return None
    provided = x_api_key or ""
    if not provided and authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer ") :].strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="A valid API key is required.")
    return None
