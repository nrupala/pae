# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Authentication seam for PAE's agent surfaces — DEFERRED, no-op today.

Auth/identity for PAE (MCP, A2A, tool manifest, discovery endpoints) is
**deferred to the paid phase** ("portfolio decision tooling", phase-2).
This module reserves the hook point so credentials can be required later
**without touching route code**:

- :func:`api_key_or_none` is a FastAPI dependency. Every new agent route
  declares it (``_: None = Depends(api_key_or_none)``). Today it is a
  no-op passthrough that always returns ``None``: routes declare it,
  nothing is enforced, behavior is unchanged.
- When auth lands, this function will validate the request's credentials
  (API key / identity) and either return an identity object or raise
  HTTP 401/403. Callers then flip from "declared, unenforced" to
  "declared, enforced" by editing this one function.

No tokens, no keys, no stored secrets, no behavior change while deferred.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException


async def api_key_or_none() -> None:
    """Deferred auth hook: currently a no-op passthrough returning None.

    Declared as a dependency on all agent routes so the enforcement point
    exists before the paid phase needs it.
    """
    return None


async def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Enforcing auth dependency for the portfolio/holdings/analytics
    data routes (declared via ``dependencies=[Depends(require_api_key)]``).

    The data routes serve the user's financial data, so unlike the agent
    discovery seam above they do not wait for the paid phase: when the
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
