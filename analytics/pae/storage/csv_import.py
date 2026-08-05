"""CSV/OFX Import Parser.

Parses common broker CSV formats into PAE Holding objects.
Auto-detects format from headers. Validates all parsed data.

Supported formats:
- Interactive Brokers (Flex Query CSV export)
- Questrade CSV export
- Wealthsimple CSV export
- Generic format (Symbol, Quantity, Market Value, Cost Basis)

All parsing is local. No data leaves the user's machine.
"""

import csv
import io
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from pae.storage.db import Holding

logger = logging.getLogger(__name__)


@dataclass
class ImportWarning:
    """Non-fatal issue found during import."""
    row: int
    field: str
    message: str


@dataclass
class ImportError:
    """Fatal issue that prevents a row from being imported."""
    row: int
    message: str


@dataclass
class ImportResult:
    """Result of parsing a CSV/OFX file."""
    holdings: list[Holding] = field(default_factory=list)
    warnings: list[ImportWarning] = field(default_factory=list)
    errors: list[ImportError] = field(default_factory=list)
    format_detected: str = "unknown"
    rows_parsed: int = 0
    rows_skipped: int = 0


# --- Format Detection ---

HEADER_SIGNATURES: dict[str, list[str]] = {
    "ibkr": ["symbol", "financial instrument", "position", "market value", "cost basis"],
    "questrade": ["symbol", "description", "quantity", "market value", "book cost"],
    "wealthsimple": ["symbol", "name", "quantity", "market value", "book value"],
    "generic": ["symbol", "quantity", "market_value"],
}


def detect_format(headers: list[str]) -> str:
    """Detect CSV format from header row.

    Args:
        headers: List of column header strings (lowercased).

    Returns:
        Format name: 'ibkr', 'questrade', 'wealthsimple', or 'generic'.
    """
    headers_lower = [h.strip().lower() for h in headers]

    for fmt, signature in HEADER_SIGNATURES.items():
        if all(any(sig in h for h in headers_lower) for sig in signature):
            return fmt

    return "generic"


# --- Value Parsing ---

def parse_float(value: str, default: float = 0.0) -> float:
    """Parse a string to float, handling currency symbols and commas.

    Returns default if parsing fails or result is NaN/Inf.
    """
    if not value or not value.strip():
        return default
    cleaned = value.strip().replace(",", "").replace("$", "").replace("CAD", "").replace("USD", "")
    try:
        result = float(cleaned)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (ValueError, TypeError):
        return default


# --- Format-Specific Parsers ---

def _find_column(headers: list[str], candidates: list[str]) -> int | None:
    """Find the first matching column index from a list of candidate names."""
    headers_lower = [h.strip().lower() for h in headers]
    for candidate in candidates:
        for i, h in enumerate(headers_lower):
            if candidate in h:
                return i
    return None


def _parse_row(
    row: list[str],
    headers: list[str],
    row_num: int,
    portfolio_id: str,
    account_id: str,
) -> tuple[Holding | None, list[ImportWarning], list[ImportError]]:
    """Parse a single CSV row into a Holding.

    Uses flexible column matching to handle different formats.
    """
    warnings: list[ImportWarning] = []
    errors: list[ImportError] = []

    # Find columns
    sym_idx = _find_column(headers, ["symbol", "ticker"])
    name_idx = _find_column(headers, ["name", "description", "financial instrument"])
    qty_idx = _find_column(headers, ["quantity", "position", "shares", "qty"])
    mv_idx = _find_column(headers, ["market value", "market_value", "current value"])
    cb_idx = _find_column(
        headers, ["cost basis", "cost_basis", "book cost", "book value", "avg cost"]
    )
    yield_idx = _find_column(headers, ["yield", "dividend yield", "yield_pct"])
    currency_idx = _find_column(headers, ["currency", "curr"])
    asset_idx = _find_column(headers, ["asset class", "asset_class", "type", "category"])

    if sym_idx is None:
        errors.append(ImportError(row=row_num, message="Cannot find symbol column"))
        return None, warnings, errors

    # Extract values
    symbol = row[sym_idx].strip().upper() if sym_idx < len(row) else ""
    if not symbol:
        errors.append(ImportError(row=row_num, message="Empty symbol"))
        return None, warnings, errors

    name = row[name_idx].strip() if name_idx is not None and name_idx < len(row) else ""
    quantity = parse_float(row[qty_idx] if qty_idx is not None and qty_idx < len(row) else "0")
    market_value = parse_float(row[mv_idx] if mv_idx is not None and mv_idx < len(row) else "0")
    cost_basis = parse_float(row[cb_idx] if cb_idx is not None and cb_idx < len(row) else "0")
    yield_pct = parse_float(
        row[yield_idx] if yield_idx is not None and yield_idx < len(row) else "0"
    )
    currency = (
        row[currency_idx].strip().upper()
        if currency_idx is not None and currency_idx < len(row)
        else "CAD"
    )
    asset_class = (
        row[asset_idx].strip().lower()
        if asset_idx is not None and asset_idx < len(row)
        else "equity"
    )

    # Validate
    if quantity < 0:
        warnings.append(
            ImportWarning(row=row_num, field="quantity", message=f"Negative quantity: {quantity}")
        )
    if market_value < 0:
        warnings.append(
            ImportWarning(
                row=row_num, field="market_value", message=f"Negative market value: {market_value}"
            )
        )
    if market_value == 0 and quantity == 0:
        warnings.append(
            ImportWarning(
                row=row_num, field="market_value", message="Zero value position, skipping"
            )
        )
        return None, warnings, errors

    # Normalize asset class
    asset_class_map = {
        "stock": "equity", "stocks": "equity", "etf": "equity",
        "bond": "fixed_income", "bonds": "fixed_income", "fixed income": "fixed_income",
        "commodity": "commodity", "gold": "commodity", "silver": "commodity",
        "real estate": "real_estate", "reit": "real_estate",
        "cash": "cash", "money market": "cash",
        "crypto": "crypto", "cryptocurrency": "crypto",
        "preferred": "preferred", "pfd": "preferred",
    }
    asset_class = asset_class_map.get(asset_class, asset_class)
    if asset_class not in (
        "equity", "fixed_income", "commodity", "real_estate", "cash", "crypto", "preferred"
    ):
        asset_class = "equity"

    holding = Holding(
        portfolio_id=portfolio_id,
        account_id=account_id,
        symbol=symbol,
        name=name,
        asset_class=asset_class,
        quantity=quantity,
        market_value=market_value,
        cost_basis=cost_basis,
        weight=0.0,  # computed after all holdings are parsed
        yield_pct=yield_pct,
        currency=currency,
        returns_json="[]",
    )

    return holding, warnings, errors


# --- Main Import Functions ---

def import_csv_string(
    csv_content: str,
    portfolio_id: str,
    account_id: str = "",
) -> ImportResult:
    """Parse a CSV string into holdings.

    Args:
        csv_content: Raw CSV text content.
        portfolio_id: Portfolio ID to assign to imported holdings.
        account_id: Optional account ID.

    Returns:
        ImportResult with holdings, warnings, and errors.
    """
    result = ImportResult()

    try:
        reader = csv.reader(io.StringIO(csv_content))
        rows = list(reader)
    except csv.Error as e:
        result.errors.append(ImportError(row=0, message=f"CSV parsing failed: {e}"))
        return result

    if len(rows) < 2:
        result.errors.append(
            ImportError(row=0, message="CSV must have at least a header row and one data row")
        )
        return result

    headers = rows[0]
    result.format_detected = detect_format(headers)
    logger.info("Detected CSV format: %s", result.format_detected)

    for i, row in enumerate(rows[1:], start=2):
        if not row or all(not cell.strip() for cell in row):
            continue  # skip empty rows

        result.rows_parsed += 1
        holding, warnings, errors = _parse_row(row, headers, i, portfolio_id, account_id)

        result.warnings.extend(warnings)
        result.errors.extend(errors)

        if holding is not None:
            result.holdings.append(holding)
        else:
            result.rows_skipped += 1

    # Compute weights
    total_value = sum(h.market_value for h in result.holdings)
    if total_value > 0:
        for h in result.holdings:
            h.weight = round(h.market_value / total_value, 6)

    logger.info(
        "Import complete: %d holdings, %d warnings, %d errors, %d skipped",
        len(result.holdings), len(result.warnings), len(result.errors), result.rows_skipped,
    )
    return result


def import_csv_file(
    file_path: str | Path,
    portfolio_id: str,
    account_id: str = "",
    encoding: str = "utf-8",
    max_size_bytes: int = 10 * 1024 * 1024,
) -> ImportResult:
    """Parse a CSV file into holdings.

    Args:
        file_path: Path to the CSV file.
        portfolio_id: Portfolio ID to assign.
        account_id: Optional account ID.
        encoding: File encoding (default utf-8).
        max_size_bytes: Maximum file size (default 10MB).

    Returns:
        ImportResult with holdings, warnings, and errors.
    """
    path = Path(file_path)

    if not path.exists():
        result = ImportResult()
        result.errors.append(ImportError(row=0, message=f"File not found: {path}"))
        return result

    if path.suffix.lower() not in (".csv", ".tsv", ".txt"):
        result = ImportResult()
        result.errors.append(ImportError(row=0, message=f"Unsupported file type: {path.suffix}"))
        return result

    file_size = path.stat().st_size
    if file_size > max_size_bytes:
        result = ImportResult()
        result.errors.append(ImportError(
            row=0,
            message=f"File too large: {file_size} bytes (max {max_size_bytes})",
        ))
        return result

    try:
        content = path.read_text(encoding=encoding)
    except (OSError, UnicodeDecodeError) as e:
        result = ImportResult()
        result.errors.append(ImportError(row=0, message=f"Failed to read file: {e}"))
        return result

    return import_csv_string(content, portfolio_id, account_id)
