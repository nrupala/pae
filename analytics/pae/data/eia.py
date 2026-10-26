# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""EIA adapter -- US energy prices (Decision 6: ADOPTED for energy).

The U.S. Energy Information Administration is the first source for US
energy statistics; its published spot prices (WTI and Brent crude,
Henry Hub natural gas) are federal statistics, free through the Open
Data API v2 with a registered key. The fleet already holds an EIA key
(Secure Vault connector custom.eia, verified live 2026-10-14 and again
2026-10-26); PAE adopts the same key: a deployment supplies it as
PAE_EIA_API_KEY in the service environment (the vault value is
surrogate-only and is never copied into code, files, or logs).

Without the key the adapter refuses in the open -- there is no keyless
scrape path and no fallback to an aggregator.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from pae.data.cache import DataCache

logger = logging.getLogger(__name__)

EIA_BASE = "https://api.eia.gov/v2"
USER_AGENT = "PAE/0.2 (2351485 ALBERTA INC.; info@aimlds.org)"
REQUEST_TIMEOUT = 30
TTL_SERIES = 3_600.0

ATTRIBUTION = "Source: U.S. Energy Information Administration (EIA)"

# Published EIA spot-price series (first-source federal statistics).
ENERGY_SERIES: dict[str, str] = {
    "wti_crude": "PET.RWTC.D",        # Cushing WTI spot, $/bbl
    "brent_crude": "PET.RBRTE.D",     # Europe Brent spot, $/bbl
    "henry_hub_gas": "NG.RNGWHHD.D",  # Henry Hub spot, $/MMBtu
}


class EiaError(Exception):
    """Raised when an EIA fetch is impossible or fails."""


@dataclass
class EiaSeries:
    """One EIA series, oldest-first, with provenance."""

    series_id: str
    description: str = ""
    units: str = ""
    dates: list[str] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    source: str = "eia"
    attribution: str = ATTRIBUTION


def parse_series_payload(payload: dict[str, Any], series_id: str) -> EiaSeries:
    """Parse an EIA v2 seriesid response into an EiaSeries.

    Pure function of the payload (the live shape: response.data rows
    with period/value/units, newest first; normalized here to
    oldest-first).
    """
    response = payload.get("response", {})
    rows = response.get("data", [])
    dates: list[str] = []
    values: list[float] = []
    units = ""
    description = str(response.get("description", ""))
    for row in reversed(rows):
        raw_value = row.get("value")
        if raw_value in (None, ""):
            continue
        dates.append(str(row.get("period", "")))
        values.append(float(raw_value))
        units = str(row.get("units", units))
    return EiaSeries(
        series_id=series_id,
        description=description,
        units=units,
        dates=dates,
        values=values,
    )


def _get_json(url: str, series_id: str) -> Any:
    """GET JSON from EIA. Errors name the series, never the URL (key)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        msg = f"EIA request failed for series {series_id}: {e}"
        raise EiaError(msg) from e


class EiaAdapter:
    """Adapter for EIA Open Data API v2 series (energy prices)."""

    def __init__(
        self, cache: DataCache | None = None, api_key: str | None = None
    ) -> None:
        self._cache = cache or DataCache()
        self._api_key = (
            api_key or os.environ.get("PAE_EIA_API_KEY", "")
        ).strip()

    @property
    def armed(self) -> bool:
        """Whether a key is available to this process."""
        return bool(self._api_key)

    def get_series(self, series_id: str, length: int = 90) -> EiaSeries:
        """Fetch a series by EIA series id (e.g. 'PET.RWTC.D').

        Raises:
            EiaError: If no key is configured (in the open, with the
                arming instruction) or the fetch fails.
        """
        series_id = series_id.strip().upper()
        if not self._api_key:
            msg = (
                "EIA access is not armed: PAE_EIA_API_KEY is not set in "
                "this process's environment. The fleet's EIA key lives in "
                "the Secure Vault (connector custom.eia); a deployment "
                "supplies the same key through the service environment. "
                "There is no keyless or fallback path by design."
            )
            raise EiaError(msg)
        cache_key = f"eia:{series_id}:{length}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            data = cached.value
            return EiaSeries(
                series_id=data["series_id"],
                description=data.get("description", ""),
                units=data.get("units", ""),
                dates=list(data["dates"]),
                values=[float(v) for v in data["values"]],
            )
        query = urllib.parse.urlencode(
            {"api_key": self._api_key, "length": length}
        )
        payload = _get_json(
            f"{EIA_BASE}/seriesid/{series_id}?{query}", series_id
        )
        result = parse_series_payload(payload, series_id)
        self._cache.put(
            cache_key,
            {
                "series_id": result.series_id,
                "description": result.description,
                "units": result.units,
                "dates": result.dates,
                "values": result.values,
            },
            source="eia",
            ttl=TTL_SERIES,
        )
        return result

    def get_energy_price(self, key: str, length: int = 90) -> EiaSeries:
        """Fetch a named energy spot price ('wti_crude', 'brent_crude',
        'henry_hub_gas')."""
        try:
            series_id = ENERGY_SERIES[key.strip().lower()]
        except KeyError:
            msg = (
                f"Unknown energy series key '{key}'. Known: "
                f"{', '.join(sorted(ENERGY_SERIES))}"
            )
            raise EiaError(msg) from None
        return self.get_series(series_id, length=length)
