# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Bank of Canada Valet adapter -- CAD rates and FX (Decision 6: ADOPTED).

The Bank of Canada is the first source for the policy rate, CORRA,
Government of Canada yields, and its daily FX reference rates. Valet is
the Bank's own web service. The Bank's Terms of Use (s.1) permit free use,
copying, distribution and transmission with attribution and accuracy
diligence; s.1.3 adds a duty PAE carries as a constant below: wherever
Bank content surfaces through a PAID service, purchasers must be told
before sale that the content came from the Bank's website and is
available there free of charge. Third-party-marked content sits outside
the Bank's grant and is never pulled. Request limits are honoured.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from pae.data.cache import DataCache

logger = logging.getLogger(__name__)

VALET_BASE = "https://www.bankofcanada.ca/valet"
USER_AGENT = "PAE/0.2 (2351485 ALBERTA INC.; info@aimlds.org)"
REQUEST_TIMEOUT = 30
TTL_SERIES = 3_600.0  # 1h -- Bank series publish daily at most.

ATTRIBUTION = "Source: Bank of Canada"
FREE_AVAILABILITY_NOTICE = (
    "Bank of Canada content shown here was obtained from the Bank of "
    "Canada's website and is available there free of charge "
    "(bankofcanada.ca)."
)


class BocError(Exception):
    """Raised when a Valet fetch or parse fails."""


@dataclass
class BocSeries:
    """One Bank of Canada series, with provenance."""

    series_name: str
    dates: list[str] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    source: str = "bank-of-canada"
    attribution: str = ATTRIBUTION


def _get_json(url: str) -> Any:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        msg = f"Bank of Canada request failed for {url}: {e}"
        raise BocError(msg) from e


class BocAdapter:
    """Adapter for Bank of Canada Valet series (rates, yields, FX)."""

    def __init__(self, cache: DataCache | None = None) -> None:
        self._cache = cache or DataCache()

    def get_series(self, series_name: str, recent: int | None = None) -> BocSeries:
        """Fetch observations for one Valet series.

        Args:
            series_name: Valet series name (e.g. 'V39079' policy rate,
                'FXUSDCAD' daily USD/CAD reference rate).
            recent: If set, only the most recent N observations.

        Returns:
            BocSeries with dates and values, provenance 'bank-of-canada'.
        """
        series_name = series_name.strip().upper()
        cache_key = f"boc:{series_name}:{recent or 'all'}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            data = cached.value
            return BocSeries(
                series_name=data["series_name"],
                dates=list(data["dates"]),
                values=[float(v) for v in data["values"]],
            )

        query = f"?recent={recent}" if recent else ""
        raw = _get_json(f"{VALET_BASE}/observations/{series_name}/json{query}")
        dates: list[str] = []
        values: list[float] = []
        for obs in raw.get("observations", []):
            cell = obs.get(series_name, {})
            raw_value = cell.get("v")
            if raw_value in (None, ""):
                continue
            dates.append(str(obs.get("d", "")))
            values.append(float(raw_value))
        result = BocSeries(series_name=series_name, dates=dates, values=values)
        self._cache.put(
            cache_key,
            {
                "series_name": result.series_name,
                "dates": result.dates,
                "values": result.values,
            },
            source="bank-of-canada",
            ttl=TTL_SERIES,
        )
        return result

    def get_fx_rate(self, pair: str = "USDCAD") -> BocSeries:
        """Fetch a daily FX reference rate series (e.g. 'USDCAD')."""
        return self.get_series(f"FX{pair.strip().upper()}")
