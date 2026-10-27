# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fama-French factor data and FRED macro indicators.

Downloads factor returns (MKT-RF, SMB, HML, RMW, CMA) from Kenneth French's
Data Library and risk-free rates / macro indicators from FRED.

All data is cached locally. No queries leave the user's machine after
the initial download.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from pae.data.cache import TTL_FACTORS, TTL_MACRO, DataCache

logger = logging.getLogger(__name__)

# Kenneth French Data Library URLs
FRENCH_BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
FRENCH_FF3_FILE = "F-F_Research_Data_Factors_CSV.zip"
FRENCH_FF5_FILE = "F-F_Research_Data_5_Factors_2x3_CSV.zip"

# FRED API (no key required for basic series via CSV download)
FRED_BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# Common FRED series -- ALLOWLISTED under Decision 6 (2026-10-26).
#
# FRED's own Terms of Use state that series owned by third parties carry
# their owners' copyright restrictions, and that non-personal use of such
# a series requires the OWNER's permission, which FRED cannot grant.
# PAE therefore serves only series owned by the Federal Reserve itself.
# Ownership below names the publishing Federal Reserve source (the
# statistical release the series belongs to).
FRED_SERIES_OWNERS: dict[str, str] = {
    "DGS3MO": "Board of Governors, H.15",
    "DGS2": "Board of Governors, H.15",
    "DGS10": "Board of Governors, H.15",
    "DGS30": "Board of Governors, H.15",
    "FEDFUNDS": "Board of Governors, H.15",
    "DFEDTARU": "Board of Governors (FOMC target range)",
    "DFEDTARL": "Board of Governors (FOMC target range)",
    "T10YIE": "Federal Reserve Bank of St. Louis (calculated)",
    "DTWEXBGS": "Board of Governors, H.10",
    "DEXUSAL": "Board of Governors, H.10",
    "DEXCAUS": "Board of Governors, H.10",
    "DEXUSEU": "Board of Governors, H.10",
    "DEXJPUS": "Board of Governors, H.10",
    "DEXUSUK": "Board of Governors, H.10",
    "WALCL": "Board of Governors, H.4.1",
}

# Series PAE previously listed that are EXCLUDED by the same rule, with
# their owners, so a refusal is explained rather than silent. Extending
# coverage to any of these requires the owner's permission or its own
# terms verification, recorded in the data-source register.
FRED_EXCLUDED_SERIES: dict[str, str] = {
    "CPIAUCSL": "U.S. Bureau of Labor Statistics",
    "UNRATE": "U.S. Bureau of Labor Statistics",
    "A191RL1Q225SBEA": "U.S. Bureau of Economic Analysis",
    "SP500": "S&P Dow Jones Indices",
    "VIXCLS": "Cboe",
    "BAAFFM": "ICE / BofA index data",
}

# Notice carried wherever FRED-sourced values surface (FRED Terms of Use
# requirement for API applications; PAE carries it for CSV access too).
FRED_ATTRIBUTION = (
    "This product uses FRED data from the Federal Reserve Bank of "
    "St. Louis but is not endorsed or certified by the Federal Reserve "
    "Bank of St. Louis."
)

# Named shortcuts to allowlisted series only.
FRED_SERIES: dict[str, str] = {
    "risk_free_rate": "DGS3MO",       # 3-Month Treasury
    "fed_funds_rate": "FEDFUNDS",      # Federal Funds Rate
    "yield_10y": "DGS10",             # 10-Year Treasury
    "yield_2y": "DGS2",               # 2-Year Treasury
    "breakeven_inflation_10y": "T10YIE",  # 10Y breakeven inflation
    "usd_index": "DTWEXBGS",          # Broad trade-weighted USD
}

# Request timeout (seconds)
REQUEST_TIMEOUT = 30

# Maximum download size (10 MB)
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024


@dataclass
class FactorData:
    """Factor return series.

    Attributes:
        name: Factor name (e.g. 'MKT-RF', 'SMB', 'HML').
        dates: List of date strings (YYYY-MM format for monthly).
        returns: List of factor returns (as decimals, e.g. 0.02 for 2%).
        description: Human-readable description of the factor.
    """

    name: str
    dates: list[str] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    description: str = ""


@dataclass
class MacroSeries:
    """A macroeconomic time series from FRED.

    Attributes:
        series_id: FRED series identifier.
        name: Human-readable name.
        dates: List of date strings (YYYY-MM-DD).
        values: List of numeric values.
        units: Unit description (e.g. 'Percent', 'Index').
    """

    series_id: str
    name: str
    dates: list[str] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    units: str = ""


class FactorDataError(Exception):
    """Raised when factor or macro data fetching fails."""


_API_KEY_PARAM = re.compile(r"(api_key=)[^&\s\"']+")


def _redact_url_and_key(text: str, url: str) -> str:
    """Strip a request URL — and any api_key in it — from error text.

    Some urllib / http.client exception strings embed the failing URL.
    For the keyed FRED endpoint the URL carries the api_key, so an
    unsanitized ``f"...: {e}"`` would put a credential into the raised
    error and, from there, into logs and reports (release-gate F-04).
    The EIA adapter avoids the same trap by never formatting its URL
    into errors; downloads here get the same guarantee by redaction.
    """
    if url:
        text = text.replace(url, "<url>")
    return _API_KEY_PARAM.sub(r"\1<redacted>", text)


def _safe_download(url: str, description: str) -> bytes:
    """Download content from a URL with size limit and timeout.

    Args:
        url: URL to download.
        description: Human-readable description for error messages.

    Returns:
        Downloaded bytes.

    Raises:
        FactorDataError: If download fails or exceeds size limit.
            The message names the description (which names the series)
            and a sanitized error — never the URL, which for keyed
            endpoints carries a credential. The original exception is
            deliberately NOT chained (``from None``): traceback logging
            prints chained exceptions whole, and their text can embed
            the request URL.
    """
    try:
        req = Request(url, headers={"User-Agent": "PAE/0.1"})
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data: bytes = resp.read(MAX_DOWNLOAD_BYTES + 1)
            if len(data) > MAX_DOWNLOAD_BYTES:
                msg = f"{description}: download exceeds {MAX_DOWNLOAD_BYTES} bytes"
                raise FactorDataError(msg)
            return data
    except URLError as e:
        detail = _redact_url_and_key(str(e), url)
        msg = f"Failed to download {description}: {detail}"
        raise FactorDataError(msg) from None
    except OSError as e:
        detail = _redact_url_and_key(str(e), url)
        msg = f"Network error downloading {description}: {detail}"
        raise FactorDataError(msg) from None


FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _fetch_fred_api(
    series_id: str, start_date: str, api_key: str
) -> tuple[list[str], list[float]]:
    """Fetch observations from the keyed FRED API.

    The key is placed only in the request URL; error messages name the
    series, never the URL, so the key cannot leak into logs or reports.
    """
    query = urllib.parse.urlencode(
        {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start_date,
        }
    )
    raw = _safe_download(f"{FRED_API_BASE}?{query}", f"FRED API series {series_id}")
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        msg = f"Unparseable response from the FRED API for series {series_id}"
        raise FactorDataError(msg) from e
    dates: list[str] = []
    values: list[float] = []
    for obs in payload.get("observations", []):
        val_str = str(obs.get("value", "")).strip()
        if val_str in ("", "."):
            continue
        try:
            values.append(float(val_str))
            dates.append(str(obs.get("date", "")))
        except ValueError:
            continue
    return dates, values


class FactorAdapter:
    """Adapter for Fama-French factor data and FRED macro series.

    Args:
        cache: DataCache instance. If None, creates a default one.

    Example:
        >>> adapter = FactorAdapter()
        >>> factors = adapter.get_ff3_factors()
        >>> rf = adapter.get_risk_free_rate()
    """

    def __init__(self, cache: DataCache | None = None) -> None:
        self._cache = cache or DataCache()

    def get_ff3_factors(self) -> dict[str, FactorData]:
        """Fetch Fama-French 3-factor monthly returns.

        Returns MKT-RF, SMB, HML, and RF (risk-free rate).

        Returns:
            Dict mapping factor name to FactorData.

        Raises:
            FactorDataError: If download or parsing fails.
        """
        cache_key = "french:ff3:monthly"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return {
                name: FactorData(**data)
                for name, data in cached.value.items()
            }

        url = f"{FRENCH_BASE_URL}/{FRENCH_FF3_FILE}"
        raw = _safe_download(url, "Fama-French 3-factor data")

        factors = self._parse_french_csv(raw, ["Mkt-RF", "SMB", "HML", "RF"])

        # Cache serialized
        cache_data: dict[str, dict[str, Any]] = {}
        for name, fd in factors.items():
            cache_data[name] = {
                "name": fd.name,
                "dates": fd.dates,
                "returns": fd.returns,
                "description": fd.description,
            }
        self._cache.put(cache_key, cache_data, source="french", ttl=TTL_FACTORS)

        return factors

    def get_ff5_factors(self) -> dict[str, FactorData]:
        """Fetch Fama-French 5-factor monthly returns.

        Returns MKT-RF, SMB, HML, RMW, CMA, and RF.

        Returns:
            Dict mapping factor name to FactorData.

        Raises:
            FactorDataError: If download or parsing fails.
        """
        cache_key = "french:ff5:monthly"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return {
                name: FactorData(**data)
                for name, data in cached.value.items()
            }

        url = f"{FRENCH_BASE_URL}/{FRENCH_FF5_FILE}"
        raw = _safe_download(url, "Fama-French 5-factor data")

        factors = self._parse_french_csv(
            raw, ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]
        )

        cache_data: dict[str, dict[str, Any]] = {}
        for name, fd in factors.items():
            cache_data[name] = {
                "name": fd.name,
                "dates": fd.dates,
                "returns": fd.returns,
                "description": fd.description,
            }
        self._cache.put(cache_key, cache_data, source="french", ttl=TTL_FACTORS)

        return factors

    def _parse_french_csv(
        self, raw_zip: bytes, expected_columns: list[str]
    ) -> dict[str, FactorData]:
        """Parse a Kenneth French CSV zip file.

        The zip contains a CSV with a header line, monthly data rows
        (YYYYMM format), and an annual section we skip.

        Args:
            raw_zip: Raw zip file bytes.
            expected_columns: Column names to extract.

        Returns:
            Dict mapping column name to FactorData.

        Raises:
            FactorDataError: If parsing fails.
        """
        try:
            with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
                csv_names = [n for n in zf.namelist() if n.endswith(".CSV") or n.endswith(".csv")]
                if not csv_names:
                    msg = "No CSV file found in zip archive"
                    raise FactorDataError(msg)
                csv_content = zf.read(csv_names[0]).decode("utf-8", errors="replace")
        except zipfile.BadZipFile as e:
            msg = f"Invalid zip file from French data library: {e}"
            raise FactorDataError(msg) from e

        # Initialize result
        factors: dict[str, FactorData] = {}
        descriptions: dict[str, str] = {
            "Mkt-RF": "Market excess return (market minus risk-free)",
            "SMB": "Small minus big (size factor)",
            "HML": "High minus low (value factor)",
            "RMW": "Robust minus weak (profitability factor)",
            "CMA": "Conservative minus aggressive (investment factor)",
            "RF": "Risk-free rate (1-month T-bill)",
        }
        for col in expected_columns:
            factors[col] = FactorData(
                name=col,
                description=descriptions.get(col, col),
            )

        # Parse CSV
        lines = csv_content.strip().split("\n")
        header_idx: int | None = None
        col_indices: dict[str, int] = {}

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            # Look for the header row containing our column names
            parts = [p.strip() for p in stripped.split(",")]
            if any(col in parts for col in expected_columns):
                header_idx = i
                for j, p in enumerate(parts):
                    if p in expected_columns:
                        col_indices[p] = j
                break

        if header_idx is None or not col_indices:
            msg = "Could not find factor columns in French CSV data"
            raise FactorDataError(msg)

        # Read data rows after header
        for line in lines[header_idx + 1:]:
            stripped = line.strip()
            if not stripped:
                continue
            parts = [p.strip() for p in stripped.split(",")]
            if len(parts) < 2:
                continue

            # Date column is first: YYYYMM format for monthly
            date_str = parts[0].strip()
            if len(date_str) != 6 or not date_str.isdigit():
                # Reached annual data or footer -- stop
                break

            date_formatted = f"{date_str[:4]}-{date_str[4:]}"

            for col, idx in col_indices.items():
                if idx < len(parts):
                    try:
                        # French data is in percentage points, convert to decimal
                        val = float(parts[idx]) / 100.0
                        factors[col].dates.append(date_formatted)
                        factors[col].returns.append(round(val, 8))
                    except ValueError:
                        continue

        total_obs = sum(len(f.returns) for f in factors.values())
        if total_obs == 0:
            msg = "No valid data rows parsed from French CSV"
            raise FactorDataError(msg)

        logger.info(
            "Parsed %d factor series with %d observations each",
            len(factors),
            len(next(iter(factors.values())).returns),
        )

        return factors

    def get_fred_series(
        self,
        series_id: str,
        start_date: str = "2000-01-01",
    ) -> MacroSeries:
        """Fetch a FRED economic time series.

        Args:
            series_id: FRED series ID (e.g. 'DGS3MO', 'DGS10').
                See FRED_SERIES dict for common series.
            start_date: Start date in YYYY-MM-DD format.

        Returns:
            MacroSeries with dates and values.

        Raises:
            ValueError: If series_id is empty.
            FactorDataError: If download or parsing fails.
        """
        if not series_id or not series_id.strip():
            msg = "series_id must not be empty"
            raise ValueError(msg)

        series_id = series_id.strip().upper()

        # Decision 6 allowlist: only Federal-Reserve-owned series are
        # served. FRED's terms put third-party-owned series under their
        # owners' restrictions; FRED cannot grant that permission.
        if series_id not in FRED_SERIES_OWNERS:
            owner = FRED_EXCLUDED_SERIES.get(series_id)
            if owner is not None:
                msg = (
                    f"FRED series {series_id} is owned by {owner}, not the "
                    "Federal Reserve. Under Decision 6 (first source, free "
                    "use verified) PAE does not serve third-party-owned "
                    "FRED series; the owner's own terms must be verified "
                    "and recorded before this series can be enabled."
                )
            else:
                msg = (
                    f"FRED series {series_id} is not on PAE's Decision 6 "
                    "allowlist (Federal-Reserve-owned series only). Verify "
                    "the series' owner against FRED's series notes and "
                    "record it in FRED_SERIES_OWNERS before enabling."
                )
            raise FactorDataError(msg)
        cache_key = f"fred:{series_id}:{start_date}"

        cached = self._cache.get(cache_key)
        if cached is not None:
            return MacroSeries(**cached.value)

        dates: list[str] = []
        values: list[float] = []

        # Preferred path: the keyed FRED API, when the deployment
        # supplies a key in PAE_FRED_API_KEY (the fleet already holds a
        # FRED key for its other products; the same key arms PAE). The
        # key travels only in the request -- never in logs, errors, or
        # the cache. Without a key, the keyless CSV endpoint serves.
        api_key = os.environ.get("PAE_FRED_API_KEY", "").strip()
        if api_key:
            dates, values = _fetch_fred_api(series_id, start_date, api_key)
        else:
            url = (
                f"{FRED_BASE_URL}?id={series_id}"
                f"&cosd={start_date}&coed=9999-12-31"
                f"&fq=Daily&fam=avg&vintage_date=&nd=&revision_date="
            )
            raw = _safe_download(url, f"FRED series {series_id}")
            text = raw.decode("utf-8", errors="replace")
            reader = csv.reader(io.StringIO(text))
            header = next(reader, None)
            if header is None:
                msg = f"Empty response from FRED for series {series_id}"
                raise FactorDataError(msg)
            for row in reader:
                if len(row) < 2:
                    continue
                date = row[0].strip()
                val_str = row[1].strip()
                if val_str == "." or not val_str:
                    continue
                try:
                    val = float(val_str)
                    dates.append(date)
                    values.append(val)
                except ValueError:
                    continue

        if not values:
            msg = f"No valid data for FRED series {series_id}"
            raise FactorDataError(msg)

        name_map: dict[str, str] = {
            "DGS3MO": "3-Month Treasury Rate",
            "DGS2": "2-Year Treasury Rate",
            "DGS10": "10-Year Treasury Rate",
            "DGS30": "30-Year Treasury Rate",
            "FEDFUNDS": "Federal Funds Rate",
            "DFEDTARU": "Fed Funds Target Range (upper)",
            "DFEDTARL": "Fed Funds Target Range (lower)",
            "T10YIE": "10-Year Breakeven Inflation Rate",
            "DTWEXBGS": "Broad Trade-Weighted USD Index",
            "WALCL": "Federal Reserve Total Assets",
        }

        result = MacroSeries(
            series_id=series_id,
            name=name_map.get(series_id, series_id),
            dates=dates,
            values=values,
            units=(
                "Index"
                if series_id.startswith(("DTWEX", "DEX"))
                else "Millions of dollars"
                if series_id == "WALCL"
                else "Percent"
            ),
        )

        self._cache.put(
            cache_key,
            {
                "series_id": result.series_id,
                "name": result.name,
                "dates": result.dates,
                "values": result.values,
                "units": result.units,
            },
            source="fred",
            ttl=TTL_MACRO,
        )

        return result

    def get_risk_free_rate(self) -> float:
        """Get the latest annualized risk-free rate.

        Uses the 3-Month Treasury rate from FRED.

        Returns:
            Annualized risk-free rate as decimal (e.g. 0.045 for 4.5%).

        Raises:
            FactorDataError: If FRED data is unavailable.
        """
        series = self.get_fred_series("DGS3MO")
        if not series.values:
            msg = "No risk-free rate data available"
            raise FactorDataError(msg)

        # Latest value is a percentage, convert to decimal
        return series.values[-1] / 100.0
