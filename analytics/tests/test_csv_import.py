"""Tests for the CSV/OFX broker statement import parser."""

import math

import pytest

from pae.data.csv_import import (
    BrokerFormat,
    CsvImportError,
    ImportedHolding,
    PortfolioImport,
    detect_format,
    parse_portfolio_file,
    parse_portfolio_text,
)

# --- Sample fixtures ------------------------------------------------------

IB_CSV = """Symbol,FinancialInstrument,Quantity,CostBasis,PositionValue,Currency
AAPL,Apple Inc,100,15000.00,18500.50,USD
SPY,SPDR S&P 500,50,20000,22500,USD
GC=F,Gold Future,2,3800,4100,USD
"""

QUESTRADE_CSV = """Symbol,OpenQuantity,CurrentMarketValue,AverageCost,Currency
ENB.TO,200,11000.00,9500.00,CAD
VFV.TO,150,18000.00,15000.00,CAD
"""

WEALTHSIMPLE_CSV = """Symbol,Quantity,BookValue,MarketValue,Currency
XEQT,300,9000.00,9750.00,CAD
VDY,100,4000.00,4200.00,CAD
"""

GENERIC_CSV = """ticker,shares,value,yield
MSFT,40,16000,0.8
KO,120,7200,3.1
"""

# Generic CSV with messy numbers: currency symbols, thousands separators,
# parenthesized negative (should be rejected as negative market value).
MESSY_CSV = """symbol,quantity,market_value,cost_basis
 aapl ,10,"$1,850.50","1,500.00"
,5,100,90
BADVAL,3,N/A,200
NEGQTY,-4,500,400
"""

OFX_SAMPLE = """OFXHEADER:100
DATA:OFXSGML
<OFX>
<INVSTMTRS>
<INVPOSLIST>
<POSSTOCK>
<INVPOS>
<SECID><UNIQUEID>037833100<TICKER>AAPL</SECID>
<UNITS>100
<UNITPRICE>185.00
<MKTVAL>18500.00
</INVPOS>
</POSSTOCK>
<POSSTOCK>
<INVPOS>
<SECID><UNIQUEID>78462F103<TICKER>SPY</SECID>
<UNITS>50
<UNITPRICE>450.00
</INVPOS>
</POSSTOCK>
</INVPOSLIST>
</INVSTMTRS>
</OFX>
"""


# --- Format detection -----------------------------------------------------

def test_detect_interactive_brokers():
    assert detect_format(IB_CSV) == BrokerFormat.INTERACTIVE_BROKERS


def test_detect_questrade():
    assert detect_format(QUESTRADE_CSV) == BrokerFormat.QUESTRADE


def test_detect_wealthsimple():
    assert detect_format(WEALTHSIMPLE_CSV) == BrokerFormat.WEALTHSIMPLE


def test_detect_generic():
    assert detect_format(GENERIC_CSV) == BrokerFormat.GENERIC


def test_detect_ofx():
    assert detect_format(OFX_SAMPLE) == BrokerFormat.OFX


def test_detect_unknown():
    assert detect_format("foo,bar,baz\n1,2,3\n") == BrokerFormat.UNKNOWN


# --- CSV parsing ----------------------------------------------------------

def test_parse_interactive_brokers():
    result = parse_portfolio_text(IB_CSV)
    assert result.broker_format == BrokerFormat.INTERACTIVE_BROKERS
    assert result.ok
    assert result.rows_imported == 3
    aapl = next(h for h in result.holdings if h.symbol == "AAPL")
    assert aapl.quantity == 100
    assert aapl.market_value == pytest.approx(18500.50)
    assert aapl.cost_basis == pytest.approx(15000.0)
    assert aapl.currency == "USD"


def test_parse_questrade_cad():
    result = parse_portfolio_text(QUESTRADE_CSV)
    assert result.broker_format == BrokerFormat.QUESTRADE
    assert result.rows_imported == 2
    enb = next(h for h in result.holdings if h.symbol == "ENB.TO")
    assert enb.currency == "CAD"
    assert enb.quantity == 200


def test_parse_wealthsimple():
    result = parse_portfolio_text(WEALTHSIMPLE_CSV)
    assert result.broker_format == BrokerFormat.WEALTHSIMPLE
    assert result.rows_imported == 2
    xeqt = next(h for h in result.holdings if h.symbol == "XEQT")
    assert xeqt.cost_basis == pytest.approx(9000.0)
    assert xeqt.market_value == pytest.approx(9750.0)


def test_parse_generic_with_yield():
    result = parse_portfolio_text(GENERIC_CSV)
    assert result.broker_format == BrokerFormat.GENERIC
    assert result.rows_imported == 2
    ko = next(h for h in result.holdings if h.symbol == "KO")
    assert ko.yield_pct == pytest.approx(3.1)


# --- Validation / messy data ---------------------------------------------

def test_messy_values_parsed_and_bad_rows_rejected():
    result = parse_portfolio_text(MESSY_CSV)
    # Row 1: aapl with $ and thousands separators -> imported, symbol upper/stripped
    imported_symbols = {h.symbol for h in result.holdings}
    assert "AAPL" in imported_symbols
    aapl = next(h for h in result.holdings if h.symbol == "AAPL")
    assert aapl.market_value == pytest.approx(1850.50)
    assert aapl.cost_basis == pytest.approx(1500.0)

    # Empty symbol row, N/A market value with no quantity-derived value is fine,
    # and negative quantity row must be rejected.
    assert "NEGQTY" not in imported_symbols
    # There must be recorded errors for the empty-symbol and negative-qty rows.
    assert any("empty symbol" in e for e in result.errors)
    assert any("negative quantity" in e for e in result.errors)


def test_negative_market_value_rejected():
    csv_text = "symbol,quantity,market_value\nXYZ,10,(500.00)\n"
    result = parse_portfolio_text(csv_text)
    assert result.rows_imported == 0
    assert any("negative market_value" in e for e in result.errors)


def test_nan_inf_rejected_via_validation():
    # Direct unit test of validation through the public parser:
    # 'inf' parses to float('inf') only via Python float(); our _to_float
    # strips it, so simulate by feeding a value Python float() accepts.
    csv_text = "symbol,quantity,market_value\nABC,inf,100\n"
    result = parse_portfolio_text(csv_text)
    # 'inf' -> float('inf'); must be rejected as NaN/Inf.
    assert result.rows_imported == 0
    assert any("NaN/Inf" in e for e in result.errors)


def test_empty_file_raises():
    with pytest.raises(CsvImportError):
        parse_portfolio_text("   ")


def test_unknown_format_raises():
    with pytest.raises(CsvImportError):
        parse_portfolio_text("a,b,c\n1,2,3\n")


def test_missing_symbol_column_errors():
    # Has value columns but no symbol-like column -> detected generic? No:
    # without a symbol key it is UNKNOWN, which raises.
    with pytest.raises(CsvImportError):
        parse_portfolio_text("quantity,market_value\n10,100\n")


# --- OFX parsing ----------------------------------------------------------

def test_parse_ofx_positions():
    result = parse_portfolio_text(OFX_SAMPLE)
    assert result.broker_format == BrokerFormat.OFX
    assert result.rows_imported == 2
    aapl = next(h for h in result.holdings if h.symbol == "AAPL")
    assert aapl.market_value == pytest.approx(18500.0)
    # SPY had no MKTVAL -> derived from units * unit_price = 50 * 450
    spy = next(h for h in result.holdings if h.symbol == "SPY")
    assert spy.market_value == pytest.approx(22500.0)


def test_ofx_no_positions_error():
    result = parse_portfolio_text("OFXHEADER:100\n<OFX></OFX>\n")
    assert result.rows_imported == 0
    assert any("No investment positions" in e for e in result.errors)


# --- File-level entry point ----------------------------------------------

def test_parse_portfolio_file_bytes():
    result = parse_portfolio_file("export.csv", IB_CSV.encode("utf-8"))
    assert result.rows_imported == 3


def test_parse_portfolio_file_latin1_fallback():
    # Byte 0xff is invalid UTF-8; ensure latin-1 fallback path works.
    raw = "symbol,quantity,market_value\nC\xa0O,10,100\n".encode("latin-1")
    result = parse_portfolio_file("weird.csv", raw)
    assert result.rows_imported == 1


def test_parse_portfolio_file_ofx_extension_hint():
    result = parse_portfolio_file("statement.qfx", OFX_SAMPLE)
    assert result.broker_format == BrokerFormat.OFX
    assert result.rows_imported == 2


def test_portfolio_import_ok_property():
    pi = PortfolioImport(broker_format=BrokerFormat.GENERIC)
    assert not pi.ok
    pi.holdings.append(ImportedHolding(symbol="X", quantity=1, market_value=10))
    pi.rows_imported = 1
    assert pi.ok
    pi.errors.append("some fatal error")
    assert not pi.ok


def test_imported_holding_defaults():
    h = ImportedHolding(symbol="AAPL", quantity=10, market_value=1850.0)
    assert h.currency == "USD"
    assert h.cost_basis is None
    assert h.yield_pct is None
    assert h.account is None


def test_no_nan_in_any_imported_holding():
    """Guard: no imported holding may carry NaN/Inf in numeric fields."""
    for sample in (IB_CSV, QUESTRADE_CSV, WEALTHSIMPLE_CSV, GENERIC_CSV):
        result = parse_portfolio_text(sample)
        for h in result.holdings:
            assert not math.isnan(h.quantity) and not math.isinf(h.quantity)
            assert not math.isnan(h.market_value) and not math.isinf(h.market_value)
            if h.cost_basis is not None:
                assert not math.isnan(h.cost_basis) and not math.isinf(h.cost_basis)
