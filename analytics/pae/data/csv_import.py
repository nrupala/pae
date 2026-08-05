"""Broker statement import: CSV / OFX / QFX parsing for PAE.

Parses holdings exports from common brokers (Interactive Brokers,
Questrade, Wealthsimple) plus a generic fallback format, and basic
OFX/QFX investment statements. The format is auto-detected from the
file's header row (CSV) or its SGML/XML tags (OFX).

Every parsed row is validated: no empty symbols, no NaN/Inf numbers,
no negative quantities or market values. Problems are collected into
``errors`` (rows that were dropped) and ``warnings`` (rows that were
kept but had a recoverable issue), so the caller can surface them for
user review before anything is persisted.

This module performs **no** network or database I/O. It turns raw file
bytes/text into a validated :class:`PortfolioImport` that the API layer
can return to the client for review, then save once confirmed.

Example:
    >>> result = parse_portfolio_file("ib_export.csv", raw_text)
    >>> if result.ok:
    ...     for h in result.holdings:
    ...         print(h.symbol, h.quantity, h.market_value)
"""

from __future__ import annotations

import csv
import io
import logging
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Maximum number of data rows we will parse from a single file. Guards
# against pathological inputs; the API layer also enforces a byte cap.
MAX_ROWS = 100_000


class BrokerFormat(str, Enum):  # noqa: UP042
    """Recognized import formats."""

    INTERACTIVE_BROKERS = "interactive_brokers"
    QUESTRADE = "questrade"
    WEALTHSIMPLE = "wealthsimple"
    GENERIC = "generic"
    OFX = "ofx"
    UNKNOWN = "unknown"


@dataclass
class ImportedHolding:
    """A single validated holding parsed from a broker statement.

    Attributes:
        symbol: Ticker symbol, upper-cased and stripped (never empty).
        quantity: Number of units/shares held (>= 0).
        market_value: Current market value of the position (>= 0).
        cost_basis: Total cost basis if available, else None.
        currency: ISO currency code (defaults to 'USD' when absent).
        yield_pct: Trailing dividend yield as a percent, if available.
        account: Broker account identifier/label, if present in the file.
    """

    symbol: str
    quantity: float
    market_value: float
    cost_basis: float | None = None
    currency: str = "USD"
    yield_pct: float | None = None
    account: str | None = None


@dataclass
class PortfolioImport:
    """Result of parsing a broker statement.

    Attributes:
        broker_format: The detected source format.
        holdings: Successfully parsed and validated holdings.
        errors: Fatal per-row problems; the row was dropped.
        warnings: Recoverable issues; the row was kept (possibly adjusted).
        rows_total: Number of data rows seen (excluding header).
        rows_imported: Number of holdings that passed validation.
    """

    broker_format: BrokerFormat
    holdings: list[ImportedHolding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows_total: int = 0
    rows_imported: int = 0

    @property
    def ok(self) -> bool:
        """True if at least one holding was imported and there were no fatal errors."""
        return self.rows_imported > 0 and not self.errors


class CsvImportError(Exception):
    """Raised when a file cannot be read or its format cannot be determined."""


# --- Column synonyms ------------------------------------------------------

# Header tokens (lower-cased, non-alphanumerics stripped) that map to each
# logical field. Order within a list does not matter; first matching header
# in the file wins.
_SYMBOL_KEYS = {"symbol", "ticker", "instrument", "security", "stocksymbol"}
_QUANTITY_KEYS = {"quantity", "qty", "shares", "units", "position", "amount", "openquantity"}
_MARKET_VALUE_KEYS = {
    "marketvalue", "value", "currentvalue", "positionvalue",
    "marketvaluebase", "totalvalue", "mktvalue", "currentmarketvalue",
}
_COST_BASIS_KEYS = {
    "costbasis", "cost", "bookvalue", "totalcost", "averagecost", "acb",
}
_CURRENCY_KEYS = {"currency", "ccy", "curr"}
_YIELD_KEYS = {"yield", "yieldpct", "dividendyield", "divyield"}
_ACCOUNT_KEYS = {"account", "accountid", "accountnumber", "acct"}


def _norm(header: str) -> str:
    """Normalize a header cell to a comparison key (lower, alnum only)."""
    return re.sub(r"[^a-z0-9]", "", header.strip().lower())


def _to_float(raw: Any) -> float | None:
    """Best-effort parse of a numeric cell.

    Handles thousands separators, currency symbols, parentheses for
    negatives, and percent signs. Returns None if the value is blank or
    cannot be parsed.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.upper() in {"N/A", "NA", "-", "--"}:
        return None

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]

    # Strip currency symbols, thousands separators, percent, and whitespace.
    s = re.sub(r"[,$€£¥%\s]", "", s)
    if not s:
        return None

    try:
        val = float(s)
    except ValueError:
        return None

    return -val if negative else val


# --- Format detection -----------------------------------------------------

def detect_format(text: str) -> BrokerFormat:
    """Auto-detect the import format from file content.

    Inspects the leading bytes for an OFX/QFX marker, otherwise reads the
    CSV header row and matches against known broker fingerprints.

    Args:
        text: Decoded file contents.

    Returns:
        The detected :class:`BrokerFormat` (``UNKNOWN`` if no match).
    """
    head = text.lstrip()[:4096].upper()
    if "OFXHEADER" in head or "<OFX>" in head or "<INVSTMTRS>" in head:
        return BrokerFormat.OFX

    # Read the first non-empty CSV line as the header.
    header_line = ""
    for line in text.splitlines():
        if line.strip():
            header_line = line
            break
    if not header_line:
        return BrokerFormat.UNKNOWN

    keys = {_norm(h) for h in header_line.split(",")}

    # Interactive Brokers Flex/Activity exports include these distinctive tokens.
    if {"financialinstrument", "positionvalue"} & keys or (
        "symbol" in keys and "costbasis" in keys and "positionvalue" in keys
    ):
        return BrokerFormat.INTERACTIVE_BROKERS

    # Questrade position exports.
    if "symbol" in keys and "currentmarketvalue" in {_norm(h) for h in header_line.split(",")}:
        return BrokerFormat.QUESTRADE
    if {"openquantity"} & keys and "symbol" in keys:
        return BrokerFormat.QUESTRADE

    # Wealthsimple holdings export.
    if "symbol" in keys and {"bookvalue", "marketvalue"} <= keys:
        return BrokerFormat.WEALTHSIMPLE

    # Generic: has at least a symbol-ish and a value-ish column.
    if keys & _SYMBOL_KEYS and (keys & _MARKET_VALUE_KEYS or keys & _QUANTITY_KEYS):
        return BrokerFormat.GENERIC

    return BrokerFormat.UNKNOWN


# --- CSV parsing ----------------------------------------------------------

def _build_column_map(headers: list[str]) -> dict[str, int]:
    """Map logical field names to their column index in the header row.

    Returns a dict with any subset of keys: symbol, quantity, market_value,
    cost_basis, currency, yield_pct, account.
    """
    norm_headers = [_norm(h) for h in headers]

    def find(synonyms: set[str]) -> int | None:
        for idx, h in enumerate(norm_headers):
            if h in synonyms:
                return idx
        return None

    mapping: dict[str, int] = {}
    for field_name, keys in (
        ("symbol", _SYMBOL_KEYS),
        ("quantity", _QUANTITY_KEYS),
        ("market_value", _MARKET_VALUE_KEYS),
        ("cost_basis", _COST_BASIS_KEYS),
        ("currency", _CURRENCY_KEYS),
        ("yield_pct", _YIELD_KEYS),
        ("account", _ACCOUNT_KEYS),
    ):
        idx = find(keys)
        if idx is not None:
            mapping[field_name] = idx
    return mapping


def _validate_holding(
    symbol: str,
    quantity: float | None,
    market_value: float | None,
    row_num: int,
    result: PortfolioImport,
) -> bool:
    """Validate the core numeric fields. Records errors/warnings on `result`.

    Returns True if the row is safe to import.
    """
    if not symbol:
        result.errors.append(f"Row {row_num}: empty symbol; row skipped")
        return False

    for label, val in (("quantity", quantity), ("market_value", market_value)):
        if val is not None and (math.isnan(val) or math.isinf(val)):
            result.errors.append(
                f"Row {row_num} ({symbol}): {label} is NaN/Inf; row skipped"
            )
            return False

    if quantity is not None and quantity < 0:
        result.errors.append(
            f"Row {row_num} ({symbol}): negative quantity {quantity}; row skipped"
        )
        return False

    if market_value is not None and market_value < 0:
        result.errors.append(
            f"Row {row_num} ({symbol}): negative market_value {market_value}; row skipped"
        )
        return False

    if market_value is None and quantity is None:
        result.errors.append(
            f"Row {row_num} ({symbol}): no quantity or market_value; row skipped"
        )
        return False

    return True


def _parse_csv(text: str, fmt: BrokerFormat) -> PortfolioImport:
    """Parse CSV content into a PortfolioImport using header column mapping."""
    result = PortfolioImport(broker_format=fmt)

    reader = csv.reader(io.StringIO(text))
    try:
        rows = list(reader)
    except csv.Error as e:
        result.errors.append(f"CSV parse error: {e}")
        return result

    # Skip leading blank lines to find the header.
    header_idx = next((i for i, r in enumerate(rows) if any(c.strip() for c in r)), None)
    if header_idx is None:
        result.errors.append("File contains no data")
        return result

    headers = rows[header_idx]
    col = _build_column_map(headers)

    if "symbol" not in col:
        result.errors.append(
            "Could not find a symbol/ticker column in the header row"
        )
        return result
    if "market_value" not in col and "quantity" not in col:
        result.errors.append(
            "Could not find a market value or quantity column in the header row"
        )
        return result

    data_rows = rows[header_idx + 1 :]
    if len(data_rows) > MAX_ROWS:
        result.warnings.append(
            f"File has {len(data_rows)} rows; only the first {MAX_ROWS} were parsed"
        )
        data_rows = data_rows[:MAX_ROWS]

    def cell(row: list[str], key: str) -> str | None:
        idx = col.get(key)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    for offset, row in enumerate(data_rows):
        row_num = header_idx + 2 + offset  # 1-based line number in the file
        if not any(c.strip() for c in row):
            continue  # skip blank lines silently
        result.rows_total += 1

        symbol_raw = cell(row, "symbol") or ""
        symbol = symbol_raw.strip().upper()
        quantity = _to_float(cell(row, "quantity"))
        market_value = _to_float(cell(row, "market_value"))
        cost_basis = _to_float(cell(row, "cost_basis"))
        yield_pct = _to_float(cell(row, "yield_pct"))
        currency_raw = cell(row, "currency")
        currency = (currency_raw or "USD").strip().upper() or "USD"
        account_raw = cell(row, "account")
        account = account_raw.strip() if account_raw and account_raw.strip() else None

        if not _validate_holding(symbol, quantity, market_value, row_num, result):
            continue

        if cost_basis is not None and cost_basis < 0:
            result.warnings.append(
                f"Row {row_num} ({symbol}): negative cost_basis; treated as unknown"
            )
            cost_basis = None

        if quantity is None:
            result.warnings.append(
                f"Row {row_num} ({symbol}): no quantity column; defaulted to 0"
            )
            quantity = 0.0
        if market_value is None:
            result.warnings.append(
                f"Row {row_num} ({symbol}): no market_value; defaulted to 0"
            )
            market_value = 0.0

        result.holdings.append(
            ImportedHolding(
                symbol=symbol,
                quantity=quantity,
                market_value=market_value,
                cost_basis=cost_basis,
                currency=currency,
                yield_pct=yield_pct,
                account=account,
            )
        )
        result.rows_imported += 1

    return result


# --- OFX/QFX parsing ------------------------------------------------------

# OFX investment positions live inside <INVPOS> blocks. We extract the
# security id (<UNIQUEID>), units, market value, and unit price with simple
# tag scans rather than a full SGML parser (OFX 1.x is non-XML SGML).
_OFX_POS_RE = re.compile(r"<INVPOS>(.*?)</INVPOS>", re.DOTALL | re.IGNORECASE)
_OFX_POSSTOCK_RE = re.compile(r"<POSSTOCK>(.*?)</POSSTOCK>", re.DOTALL | re.IGNORECASE)


def _ofx_tag(block: str, tag: str) -> str | None:
    """Extract a single OFX tag value. Handles both <TAG>value and <TAG>value</TAG>."""
    m = re.search(rf"<{tag}>([^<\r\n]*)", block, re.IGNORECASE)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


def _parse_ofx(text: str) -> PortfolioImport:
    """Parse a basic OFX/QFX investment statement into a PortfolioImport.

    Extracts one holding per <INVPOS> block. Symbol falls back to the
    security UNIQUEID when no human-readable ticker is present.
    """
    result = PortfolioImport(broker_format=BrokerFormat.OFX)

    blocks = _OFX_POS_RE.findall(text)
    if not blocks:
        # Some exports wrap positions in <POSSTOCK> without <INVPOS>.
        blocks = _OFX_POSSTOCK_RE.findall(text)

    if not blocks:
        result.errors.append("No investment positions (<INVPOS>) found in OFX file")
        return result

    if len(blocks) > MAX_ROWS:
        result.warnings.append(
            f"OFX has {len(blocks)} positions; only the first {MAX_ROWS} were parsed"
        )
        blocks = blocks[:MAX_ROWS]

    for i, block in enumerate(blocks, start=1):
        result.rows_total += 1

        symbol = (
            _ofx_tag(block, "TICKER")
            or _ofx_tag(block, "SECNAME")
            or _ofx_tag(block, "UNIQUEID")
            or ""
        ).strip().upper()

        quantity = _to_float(_ofx_tag(block, "UNITS"))
        market_value = _to_float(_ofx_tag(block, "MKTVAL"))
        unit_price = _to_float(_ofx_tag(block, "UNITPRICE"))

        # Derive market value from units * price when MKTVAL is absent.
        if market_value is None and quantity is not None and unit_price is not None:
            market_value = quantity * unit_price

        if not _validate_holding(symbol, quantity, market_value, i, result):
            continue

        if quantity is None:
            quantity = 0.0
        if market_value is None:
            market_value = 0.0

        result.holdings.append(
            ImportedHolding(
                symbol=symbol,
                quantity=quantity,
                market_value=market_value,
                cost_basis=None,
                currency=(_ofx_tag(block, "CURSYM") or "USD"),
            )
        )
        result.rows_imported += 1

    return result


# --- Public entry point ---------------------------------------------------

def parse_portfolio_text(text: str, fmt: BrokerFormat | None = None) -> PortfolioImport:
    """Parse already-decoded statement text into a PortfolioImport.

    Args:
        text: The decoded file contents.
        fmt: Optional explicit format. If None, the format is auto-detected.

    Returns:
        A :class:`PortfolioImport` with holdings, errors, and warnings.

    Raises:
        CsvImportError: If `text` is empty or the format cannot be determined.
    """
    if not text or not text.strip():
        raise CsvImportError("File is empty")

    detected = fmt or detect_format(text)
    if detected == BrokerFormat.UNKNOWN:
        raise CsvImportError(
            "Could not detect file format. Expected a broker CSV with a "
            "symbol column and a market value/quantity column, or an OFX/QFX file."
        )

    if detected == BrokerFormat.OFX:
        return _parse_ofx(text)
    return _parse_csv(text, detected)


def parse_portfolio_file(filename: str, raw: bytes | str) -> PortfolioImport:
    """Parse a broker statement file (bytes or text) into a PortfolioImport.

    Decodes bytes as UTF-8 with a latin-1 fallback, auto-detects the
    format, and parses. The filename is used only as a hint for OFX/QFX
    extensions and for error messages.

    Args:
        filename: Original filename (used for extension hints and messages).
        raw: File contents as bytes or already-decoded str.

    Returns:
        A :class:`PortfolioImport`.

    Raises:
        CsvImportError: If decoding fails or the format is unknown.
    """
    try:
        if isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                logger.warning("UTF-8 decode failed for %s; falling back to latin-1", filename)
                text = raw.decode("latin-1")
        else:
            text = raw
    except (UnicodeDecodeError, LookupError) as e:
        msg = f"Failed to decode '{filename}': {e}"
        raise CsvImportError(msg) from e

    # Extension hint: force OFX parsing for .ofx/.qfx even if content scan is ambiguous.
    lower = filename.lower()
    if lower.endswith((".ofx", ".qfx")):
        return parse_portfolio_text(text, BrokerFormat.OFX)

    return parse_portfolio_text(text)
