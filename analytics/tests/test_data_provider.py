# Copyright (C) 2026 2351485 ALBERTA INC.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the Decision 6 data provider layer.

Covers the first-source registry, the FRED series allowlist, the SEC
EDGAR and Bank of Canada adapters (parsing, provenance), and the
DataAdapter quarantine that keeps Yahoo-class data development-only.
All network access is stubbed; these tests run offline.
"""

from __future__ import annotations

import json

import pytest

import pae.data.boc as boc_module
import pae.data.edgar as edgar_module
import pae.data.eia as eia_module
from pae.data import registry
from pae.data.adapter import DataAdapter, DataAdapterError
from pae.data.boc import BocAdapter
from pae.data.cache import DataCache
from pae.data.edgar import EdgarAdapter
from pae.data.eia import EiaAdapter, EiaError, parse_series_payload
from pae.data.factors import FactorAdapter, FactorDataError
from pae.data.registry import SourceStatus, entry_for

# --- Registry -------------------------------------------------------


def test_registry_covers_all_classes() -> None:
    for name in (
        "prices",
        "quotes_metadata",
        "factors",
        "macro_us",
        "rates_fx_cad",
        "fundamentals_us",
        "fundamentals_ca",
        "commodities",
    ):
        assert registry.entry_for(name).data_class == name


def test_prices_class_is_a_ruled_gap() -> None:
    with pytest.raises(registry.DataProviderGapError, match="waits"):
        registry.require_available("prices")


def test_adopted_classes_pass() -> None:
    for name in ("macro_us", "rates_fx_cad", "fundamentals_us"):
        assert registry.require_available(name).status is registry.SourceStatus.ADOPTED


# --- FRED allowlist ---------------------------------------------------


def _factors(tmp_path) -> FactorAdapter:
    return FactorAdapter(cache=DataCache(cache_dir=tmp_path))


def test_fred_refuses_third_party_owned_series(tmp_path) -> None:
    adapter = _factors(tmp_path)
    with pytest.raises(FactorDataError, match="Bureau of Labor Statistics"):
        adapter.get_fred_series("CPIAUCSL")
    with pytest.raises(FactorDataError, match="Cboe"):
        adapter.get_fred_series("VIXCLS")


def test_fred_refuses_unlisted_series(tmp_path) -> None:
    adapter = _factors(tmp_path)
    with pytest.raises(FactorDataError, match="allowlist"):
        adapter.get_fred_series("NOTAREALSERIES")


def test_fred_allowlist_is_fed_owned_only() -> None:
    from pae.data.factors import FRED_EXCLUDED_SERIES, FRED_SERIES_OWNERS

    assert "DGS10" in FRED_SERIES_OWNERS
    for excluded in FRED_EXCLUDED_SERIES:
        assert excluded not in FRED_SERIES_OWNERS


# --- EDGAR adapter ------------------------------------------------------

_CANNED_FACTS = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {
                            "end": "2025-09-30",
                            "val": 416161000000,
                            "form": "10-K",
                            "filed": "2025-10-31",
                        }
                    ]
                }
            }
        }
    },
}


def test_edgar_facts_parse_and_provenance(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(edgar_module, "_get_json", lambda url: _CANNED_FACTS)
    adapter = EdgarAdapter(cache=DataCache(cache_dir=tmp_path))
    facts = adapter.get_company_facts(320193)
    assert facts.entity_name == "Apple Inc."
    assert facts.source == "sec-edgar"
    points = facts.facts["Revenues"]
    assert points[0].value == 416161000000.0
    assert points[0].form == "10-K"


def test_edgar_resolve_cik(tmp_path, monkeypatch) -> None:
    rows = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
    monkeypatch.setattr(edgar_module, "_get_json", lambda url: rows)
    adapter = EdgarAdapter(cache=DataCache(cache_dir=tmp_path))
    assert adapter.resolve_cik("aapl") == 320193
    with pytest.raises(edgar_module.EdgarError):
        adapter.resolve_cik("ZZZZ")


# --- Bank of Canada adapter --------------------------------------------

_CANNED_BOC = {
    "observations": [
        {"d": "2026-10-23", "FXUSDCAD": {"v": "1.3943"}},
        {"d": "2026-10-24", "FXUSDCAD": {"v": ""}},
        {"d": "2026-10-25", "FXUSDCAD": {"v": "1.3931"}},
    ]
}


def test_boc_series_parse_skips_blanks(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(boc_module, "_get_json", lambda url: _CANNED_BOC)
    adapter = BocAdapter(cache=DataCache(cache_dir=tmp_path))
    series = adapter.get_fx_rate("USDCAD")
    assert series.source == "bank-of-canada"
    assert series.dates == ["2026-10-23", "2026-10-25"]
    assert series.values == [1.3943, 1.3931]
    assert "Bank of Canada" in series.attribution


def test_boc_notice_constants() -> None:
    assert "free of charge" in boc_module.FREE_AVAILABILITY_NOTICE


# --- DataAdapter quarantine --------------------------------------------


def test_adapter_blocks_prices_in_product_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PAE_DEV_DATA", raising=False)
    adapter = DataAdapter(cache_dir=tmp_path)
    with pytest.raises(DataAdapterError, match="Decision 6"):
        adapter.get_quote("AAPL")
    with pytest.raises(DataAdapterError, match="Decision 6"):
        adapter.get_history("AAPL")
    with pytest.raises(DataAdapterError, match="Decision 6"):
        adapter.get_commodity("gold")


def test_adapter_snapshot_reports_gaps_not_yahoo(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PAE_DEV_DATA", raising=False)
    adapter = DataAdapter(cache_dir=tmp_path)
    snapshot = adapter.get_market_snapshot([])
    joined = " ".join(snapshot.errors)
    assert "Decision 6" in joined
    assert snapshot.commodities == []
    assert snapshot.forex == []


def test_fred_keyed_api_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAE_FRED_API_KEY", "TESTKEY-SECRET")
    adapter = FactorAdapter(cache=DataCache(cache_dir=tmp_path))
    seen: dict[str, str] = {}

    def fake_download(url: str, desc: str) -> bytes:
        seen["url"] = url
        return json.dumps(
            {
                "observations": [
                    {"date": "2026-10-20", "value": "4.10"},
                    {"date": "2026-10-21", "value": "."},
                    {"date": "2026-10-22", "value": "4.16"},
                ]
            }
        ).encode()

    monkeypatch.setattr("pae.data.factors._safe_download", fake_download)
    series = adapter.get_fred_series("DGS10")
    assert "api.stlouisfed.org" in seen["url"]
    # The key rides the request only -- it is never in the result.
    assert series.dates == ["2026-10-20", "2026-10-22"]
    assert series.values == [4.10, 4.16]
    assert "TESTKEY-SECRET" not in repr(series)


def test_fred_csv_fallback_without_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PAE_FRED_API_KEY", raising=False)
    adapter = FactorAdapter(cache=DataCache(cache_dir=tmp_path))
    csv_body = b"Date,DGS10\n2026-10-20,4.10\n2026-10-21,.\n2026-10-22,4.16\n"
    monkeypatch.setattr(
        "pae.data.factors._safe_download", lambda url, desc: csv_body
    )
    series = adapter.get_fred_series("DGS10")
    assert series.values == [4.10, 4.16]


def test_eia_parse_real_payload_shape() -> None:
    payload = {
        "response": {
            "description": "Cushing, OK WTI Spot Price FOB",
            "data": [
                {"period": "2026-10-20", "value": 107.89, "units": "$/BBL"},
                {"period": "2026-10-19", "value": 110.01, "units": "$/BBL"},
                {"period": "2026-10-16", "value": None, "units": "$/BBL"},
            ],
        }
    }
    series = parse_series_payload(payload, "PET.RWTC.D")
    assert series.dates == ["2026-10-19", "2026-10-20"]  # oldest-first
    assert series.values == [110.01, 107.89]
    assert series.units == "$/BBL"
    assert series.source == "eia"


def test_eia_unarmed_refuses_in_the_open(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PAE_EIA_API_KEY", raising=False)
    adapter = EiaAdapter(cache=DataCache(cache_dir=tmp_path))
    assert adapter.armed is False
    with pytest.raises(EiaError, match="PAE_EIA_API_KEY"):
        adapter.get_energy_price("wti_crude")


def test_eia_armed_fetch_and_provenance(tmp_path, monkeypatch) -> None:
    adapter = EiaAdapter(cache=DataCache(cache_dir=tmp_path), api_key="TESTKEY")

    def fake_get_json(url: str, series_id: str):
        assert series_id == "NG.RNGWHHD.D"
        assert "TESTKEY" in url
        return {
            "response": {
                "description": "Henry Hub Natural Gas Spot Price",
                "data": [
                    {"period": "2026-10-20", "value": 3.42, "units": "$/MMBtu"}
                ],
            }
        }

    monkeypatch.setattr(eia_module, "_get_json", fake_get_json)
    series = adapter.get_energy_price("henry_hub_gas")
    assert series.values == [3.42]
    assert series.source == "eia"
    with pytest.raises(EiaError, match="Unknown energy series"):
        adapter.get_energy_price("unobtanium")


def test_registry_commodities_energy_adopted() -> None:
    assert entry_for("commodities").status is SourceStatus.CONDITIONAL
    assert "EIA" in entry_for("commodities").source


def test_adapter_energy_price_wraps_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PAE_EIA_API_KEY", raising=False)
    adapter = DataAdapter(cache_dir=tmp_path)
    with pytest.raises(DataAdapterError, match="PAE_EIA_API_KEY"):
        adapter.get_energy_price("wti_crude")
