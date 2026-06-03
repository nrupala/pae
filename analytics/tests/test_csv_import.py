"""Tests for CSV import parser."""

from pae.storage.csv_import import (
    detect_format,
    import_csv_string,
    parse_float,
)


# --- parse_float ---


def test_parse_float_basic():
    assert parse_float("100.50") == 100.50


def test_parse_float_with_commas():
    assert parse_float("1,234,567.89") == 1234567.89


def test_parse_float_with_currency():
    assert parse_float("$1,234.56") == 1234.56
    assert parse_float("CAD 500.00") == 500.00


def test_parse_float_empty():
    assert parse_float("") == 0.0
    assert parse_float("  ") == 0.0


def test_parse_float_invalid():
    assert parse_float("abc") == 0.0
    assert parse_float("N/A") == 0.0


def test_parse_float_nan_inf():
    assert parse_float("nan") == 0.0
    assert parse_float("inf") == 0.0


# --- detect_format ---


def test_detect_ibkr():
    headers = ["Symbol", "Financial Instrument", "Position", "Market Value", "Cost Basis"]
    assert detect_format(headers) == "ibkr"


def test_detect_questrade():
    headers = ["Symbol", "Description", "Quantity", "Market Value", "Book Cost"]
    assert detect_format(headers) == "questrade"


def test_detect_wealthsimple():
    headers = ["Symbol", "Name", "Quantity", "Market Value", "Book Value"]
    assert detect_format(headers) == "wealthsimple"


def test_detect_generic():
    headers = ["Symbol", "Quantity", "Market_Value"]
    assert detect_format(headers) == "generic"


def test_detect_unknown():
    headers = ["Column1", "Column2", "Column3"]
    assert detect_format(headers) == "generic"


# --- import_csv_string ---


GENERIC_CSV = """Symbol,Quantity,Market_Value,Cost_Basis,Yield
FDS,100,18000.00,12000.00,0.9
GSBD,500,15796.00,14000.00,13.0
COF,200,26368.00,20000.00,1.1
TTD,50,11457.00,8000.00,0.0
"""

IBKR_CSV = """Symbol,Financial Instrument,Position,Market Value,Cost Basis,Currency
FDS,FactSet Research Systems,100,"$18,000.00","$12,000.00",USD
GSBD,Goldman Sachs BDC,500,"$15,796.00","$14,000.00",USD
"""

EMPTY_CSV = """Symbol,Quantity,Market_Value
"""

CSV_WITH_ERRORS = """Symbol,Quantity,Market_Value
,100,5000
FDS,-50,0
"""


def test_import_generic_csv():
    result = import_csv_string(GENERIC_CSV, portfolio_id="test-portfolio")
    assert result.format_detected == "generic"
    assert len(result.holdings) == 4
    assert result.rows_parsed == 4
    assert result.rows_skipped == 0

    fds = next(h for h in result.holdings if h.symbol == "FDS")
    assert fds.market_value == 18000.0
    assert fds.cost_basis == 12000.0
    assert fds.yield_pct == 0.9
    assert fds.portfolio_id == "test-portfolio"

    # Weights should sum to ~1.0
    total_weight = sum(h.weight for h in result.holdings)
    assert abs(total_weight - 1.0) < 0.001


def test_import_ibkr_csv():
    result = import_csv_string(IBKR_CSV, portfolio_id="test-ibkr")
    assert result.format_detected == "ibkr"
    assert len(result.holdings) == 2

    fds = next(h for h in result.holdings if h.symbol == "FDS")
    assert fds.market_value == 18000.0
    assert fds.currency == "USD"


def test_import_empty_csv():
    result = import_csv_string(EMPTY_CSV, portfolio_id="test-empty")
    assert len(result.holdings) == 0
    assert result.rows_parsed == 0


def test_import_csv_with_errors():
    result = import_csv_string(CSV_WITH_ERRORS, portfolio_id="test-errors")
    # First row: empty symbol -> error
    assert any(e.message == "Empty symbol" for e in result.errors)
    # Second row: zero value -> warning and skip
    assert result.rows_skipped >= 1


def test_import_single_row():
    csv = "Symbol,Market_Value\nAAPL,150000\n"
    result = import_csv_string(csv, portfolio_id="test-single")
    assert len(result.holdings) == 1
    assert result.holdings[0].symbol == "AAPL"
    assert result.holdings[0].weight == 1.0  # only holding = 100%


def test_import_with_account_id():
    result = import_csv_string(GENERIC_CSV, portfolio_id="p1", account_id="acc-ibkr")
    for h in result.holdings:
        assert h.account_id == "acc-ibkr"


def test_import_totally_empty():
    result = import_csv_string("", portfolio_id="test")
    assert len(result.holdings) == 0
    assert len(result.errors) > 0  # should report "must have header + data"
