# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""First-source data registry for PAE (Decision 6, owner ruling 2026-10-26).

The rule: every data class is served by its FIRST SOURCE (the originator of
the data, not an aggregator), and only where the source's own published terms
permit the use FREE, including commercial product use. Terms were verified per
class and the evidence is recorded in the build record
(PAE-DATA-SOURCE-REGISTER-2026-10-26). A class with no first+free source is a
GAP: it waits. Nothing in this layer ever falls back silently to a
non-compliant source -- a gap surfaces as DataProviderGapError, in the open.

Classes and dispositions:
    prices / quotes / metadata ... GAP (first source = the exchanges;
        exchange market data is licensed for fees; Yahoo is an aggregator
        on personal-use terms -- both fail the rule)
    factors ...................... French Data Library (first source;
        free download; commercial redistribution needs Dartmouth's
        written permission -- CONDITIONAL)
    macro_us ..................... FRED, Federal-Reserve-owned series only
        (third-party-owned series are excluded by FRED's own terms)
    rates_fx_cad ................. Bank of Canada Valet (first source;
        free reproduction with attribution and the s.1.3 notice duty)
    fundamentals_us .............. SEC EDGAR (first source; public filings;
        free; fair-access conduct rules)
    fundamentals_ca .............. GAP (SEDAR+ has no public API)
    commodities .................. GAP except Fed-owned FRED series,
        served under macro_us discipline
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceStatus(Enum):
    """Adoption status of a data class under Decision 6."""

    ADOPTED = "adopted"
    CONDITIONAL = "conditional"
    GAP = "gap"


@dataclass(frozen=True)
class SourceEntry:
    """One data class and its ruled source.

    Attributes:
        data_class: Registry key (e.g. 'prices', 'macro_us').
        source: The first source's name, or '' for a gap.
        status: ADOPTED / CONDITIONAL / GAP.
        terms_basis: One line naming the terms evidence for the ruling.
        notes: Operating conditions that travel with the source.
    """

    data_class: str
    source: str
    status: SourceStatus
    terms_basis: str
    notes: str = ""


class DataProviderGapError(Exception):
    """Raised when a data class has no Decision-6-compliant source.

    This is a designed outcome, not a failure: the class waits rather than
    being filled against the rule.
    """


REGISTRY: dict[str, SourceEntry] = {
    "prices": SourceEntry(
        data_class="prices",
        source="",
        status=SourceStatus.GAP,
        terms_basis=(
            "First source = exchanges/CTA-UTP; market data is distributed "
            "under licence fees (SEC rule filings document redistribution "
            "fees and enterprise licences). No free first source exists."
        ),
        notes=(
            "Product path: price/return series the user supplies via import. "
            "Development-only Yahoo access exists behind PAE_DEV_DATA=1 and "
            "is labelled non-primary wherever it surfaces."
        ),
    ),
    "quotes_metadata": SourceEntry(
        data_class="quotes_metadata",
        source="",
        status=SourceStatus.GAP,
        terms_basis="Same licensing system as prices; no free first source.",
        notes="Symbol search degrades to the user's imported universe.",
    ),
    "factors": SourceEntry(
        data_class="factors",
        source="Kenneth French Data Library (Dartmouth)",
        status=SourceStatus.CONDITIONAL,
        terms_basis=(
            "Free public download from the first source; Dartmouth's "
            "copyright statement bars commercial redistribution without "
            "written permission."
        ),
        notes=(
            "Self-host use with attribution is the library's evident intent. "
            "Commercial/hosted enablement waits on Dartmouth's permission."
        ),
    ),
    "macro_us": SourceEntry(
        data_class="macro_us",
        source="FRED (Federal Reserve Bank of St. Louis)",
        status=SourceStatus.ADOPTED,
        terms_basis=(
            "FRED API Terms of Use: free; third-party-owned series carry "
            "their owners' restrictions and need owner permission beyond "
            "personal use."
        ),
        notes=(
            "Series allowlist discipline (see factors.FRED_SERIES_OWNERS): "
            "only Federal-Reserve-owned series are served."
        ),
    ),
    "rates_fx_cad": SourceEntry(
        data_class="rates_fx_cad",
        source="Bank of Canada Valet API",
        status=SourceStatus.ADOPTED,
        terms_basis=(
            "Bank of Canada Terms of Use s.1: free use/copy/distribute with "
            "attribution, accuracy diligence, and the s.1.3 duty to tell "
            "paid-tier purchasers the content is free on the Bank's site."
        ),
        notes="Third-party-marked content excluded; request limits honoured.",
    ),
    "fundamentals_us": SourceEntry(
        data_class="fundamentals_us",
        source="SEC EDGAR (data.sec.gov)",
        status=SourceStatus.ADOPTED,
        terms_basis=(
            "SEC EDGAR APIs: no key, public filings data; automated access "
            "under the SEC Privacy and Security Policy (fair access)."
        ),
        notes="Declared User-Agent; at most 10 requests/second.",
    ),
    "fundamentals_ca": SourceEntry(
        data_class="fundamentals_ca",
        source="",
        status=SourceStatus.GAP,
        terms_basis="SEDAR+ publishes filings but offers no public API.",
        notes="User-supplied filings via import remain possible.",
    ),
    "commodities": SourceEntry(
        data_class="commodities",
        source="EIA (energy); exchanges/LBMA (the rest -- gap)",
        status=SourceStatus.CONDITIONAL,
        terms_basis=(
            "EIA Open Data API v2: free with a registered key; EIA data "
            "is US federal statistics (first source). Fleet key held in "
            "the Secure Vault (custom.eia), verified live 2026-10-14 and "
            "2026-10-26. Benchmark commodity prices beyond EIA energy "
            "are administered/licensed products (e.g. LBMA Gold Price "
            "via IBA); no free first source for those."
        ),
        notes=(
            "ENERGY ADOPTED: EIA spot prices (WTI, Brent, Henry Hub) "
            "serve via pae.data.eia using the fleet's existing EIA key "
            "(PAE_EIA_API_KEY). Everything else in the class waits. "
            "Fed-owned FRED commodity series, if any, serve via macro_us."
        ),
    ),
}


def entry_for(data_class: str) -> SourceEntry:
    """Return the registry entry for a data class.

    Raises:
        DataProviderGapError: If the class is not in the registry at all.
    """
    try:
        return REGISTRY[data_class]
    except KeyError:
        msg = f"No data-source ruling exists for class '{data_class}'."
        raise DataProviderGapError(msg) from None


def require_available(data_class: str) -> SourceEntry:
    """Return the entry for a class, or raise if the class is a gap.

    Raises:
        DataProviderGapError: If the class has no compliant source (yet).
    """
    entry = entry_for(data_class)
    if entry.status is SourceStatus.GAP:
        msg = (
            f"Data class '{data_class}' has no first-source, free-use "
            f"provider under Decision 6 and waits. Basis: {entry.terms_basis} "
            f"{entry.notes}"
        )
        raise DataProviderGapError(msg)
    return entry
