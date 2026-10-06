# Copyright (C) 2026 Nrupal Akolkar
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


async def api_key_or_none() -> None:
    """Deferred auth hook: currently a no-op passthrough returning None.

    Declared as a dependency on all agent routes so the enforcement point
    exists before the paid phase needs it.
    """
    return None
