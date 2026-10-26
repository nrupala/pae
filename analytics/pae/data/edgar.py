# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""SEC EDGAR adapter -- US fundamentals and filings (Decision 6: ADOPTED).

EDGAR is the first source for US issuer filings: the filings themselves,
served by the regulator at data.sec.gov. The APIs need no key; automated
access follows the SEC's Privacy and Security Policy fair-access rules --
a declared User-Agent and at most 10 requests per second. This adapter
paces itself well under that ceiling and caches through DataCache with
provenance source='sec-edgar'.

What it serves: submissions history and XBRL company facts (the extracted
financial-statement data). It serves facts as filed; PAE computes nothing
on top of them here, and the non-advisory guardrail applies as everywhere:
facts, never recommendations.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from pae.data.cache import DataCache

logger = logging.getLogger(__name__)

EDGAR_BASE = "https://data.sec.gov"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
USER_AGENT = "PAE/0.2 (2351485 ALBERTA INC.; info@aimlds.org)"
REQUEST_TIMEOUT = 30
MIN_INTERVAL_SECONDS = 0.11  # SEC fair access: <= 10 requests/second.
TTL_FACTS = 86_400.0  # 24h -- filings change on filing events, not ticks.

_last_request_at = 0.0


class EdgarError(Exception):
    """Raised when an EDGAR fetch or parse fails."""


@dataclass
class FactPoint:
    """One XBRL fact as filed."""

    end: str
    value: float
    form: str = ""
    filed: str = ""


@dataclass
class CompanyFacts:
    """XBRL company facts for one issuer, with provenance."""

    cik: int
    entity_name: str
    facts: dict[str, list[FactPoint]] = field(default_factory=dict)
    source: str = "sec-edgar"


def _pace() -> None:
    """Enforce the fair-access request interval."""
    global _last_request_at
    now = time.monotonic()
    wait = MIN_INTERVAL_SECONDS - (now - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _get_json(url: str) -> Any:
    """GET a JSON document from EDGAR with the declared User-Agent."""
    _pace()
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        msg = f"EDGAR request failed for {url}: {e}"
        raise EdgarError(msg) from e


class EdgarAdapter:
    """Adapter for SEC EDGAR submissions and XBRL company facts."""

    def __init__(self, cache: DataCache | None = None) -> None:
        self._cache = cache or DataCache()

    def resolve_cik(self, ticker: str) -> int:
        """Resolve a ticker symbol to its CIK via the SEC's own mapping.

        Raises:
            EdgarError: If the ticker is unknown to EDGAR.
        """
        ticker = ticker.strip().upper()
        cache_key = "edgar:tickers"
        cached = self._cache.get(cache_key)
        if cached is not None:
            mapping = cached.value
        else:
            rows = _get_json(TICKERS_URL)
            mapping = {
                row["ticker"].upper(): int(row["cik_str"]) for row in rows.values()
            }
            self._cache.put(cache_key, mapping, source="sec-edgar", ttl=TTL_FACTS)
        try:
            return int(mapping[ticker])
        except KeyError:
            msg = f"Ticker '{ticker}' is not in EDGAR's company mapping."
            raise EdgarError(msg) from None

    def get_company_facts(
        self, cik: int, taxonomy: str = "us-gaap"
    ) -> CompanyFacts:
        """Fetch XBRL company facts for an issuer.

        Args:
            cik: Central Index Key (leading zeros are added here).
            taxonomy: XBRL taxonomy to extract ('us-gaap' by default).

        Returns:
            CompanyFacts with per-concept FactPoint lists (USD-unit facts
            where a unit choice exists), provenance source='sec-edgar'.
        """
        cache_key = f"edgar:facts:{cik}:{taxonomy}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return _facts_from_dict(cached.value)

        raw = _get_json(f"{EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik:010d}.json")
        facts: dict[str, list[FactPoint]] = {}
        for concept, payload in raw.get("facts", {}).get(taxonomy, {}).items():
            units = payload.get("units", {})
            series: list[dict[str, Any]] = units.get("USD") or next(
                iter(units.values()), []
            )
            points = [
                FactPoint(
                    end=str(item.get("end", "")),
                    value=float(item["val"]),
                    form=str(item.get("form", "")),
                    filed=str(item.get("filed", "")),
                )
                for item in series
                if "val" in item
            ]
            if points:
                facts[concept] = points
        result = CompanyFacts(
            cik=cik, entity_name=str(raw.get("entityName", "")), facts=facts
        )
        self._cache.put(
            cache_key,
            _facts_to_dict(result),
            source="sec-edgar",
            ttl=TTL_FACTS,
        )
        return result


def _facts_to_dict(facts: CompanyFacts) -> dict[str, Any]:
    return {
        "cik": facts.cik,
        "entity_name": facts.entity_name,
        "source": facts.source,
        "facts": {
            concept: [
                {"end": p.end, "value": p.value, "form": p.form, "filed": p.filed}
                for p in points
            ]
            for concept, points in facts.facts.items()
        },
    }


def _facts_from_dict(data: dict[str, Any]) -> CompanyFacts:
    return CompanyFacts(
        cik=int(data["cik"]),
        entity_name=str(data.get("entity_name", "")),
        source=str(data.get("source", "sec-edgar")),
        facts={
            concept: [
                FactPoint(
                    end=str(p["end"]),
                    value=float(p["value"]),
                    form=str(p.get("form", "")),
                    filed=str(p.get("filed", "")),
                )
                for p in points
            ]
            for concept, points in data.get("facts", {}).items()
        },
    )
