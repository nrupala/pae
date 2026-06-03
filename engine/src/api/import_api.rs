//! CSV / broker-statement import endpoints.
//!
//! Two-step flow so the user can review before anything is persisted:
//!
//! 1. `POST /api/v1/import/csv` — multipart upload of a broker CSV. The
//!    file is size/type-validated and parsed into holdings, which are
//!    returned as JSON for review. **Nothing is saved here.**
//! 2. `POST /api/v1/import/confirm` — the client posts back the reviewed,
//!    **client-side-encrypted** holdings, which are persisted to SQLite.
//!
//! Consistent with PAE's zero-knowledge model, the confirm step accepts
//! only ciphertext blobs (symbol + payload), never plaintext financials.
//! Plaintext parsing in step 1 happens in-memory and is never stored.
//!
//! The canonical multi-format importer (Interactive Brokers / Questrade /
//! Wealthsimple / OFX) lives in Python at `analytics/pae/data/csv_import.py`.
//! This endpoint implements an equivalent generic-CSV review parser in Rust
//! so the synchronous upload→review path needs no Python runtime; the
//! validation contract (reject empty symbol, NaN/Inf, negative qty/value)
//! is kept identical.

use std::sync::Arc;

use axum::extract::{Multipart, State};
use axum::http::StatusCode;
use axum::Json;
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use serde::{Deserialize, Serialize};

use crate::storage::{NewHolding, Store};

/// Maximum accepted upload size: 10 MB.
const MAX_FILE_BYTES: usize = 10 * 1024 * 1024;

/// Standard error response body for import endpoints.
#[derive(Serialize)]
pub struct ImportErrorResponse {
    pub error: String,
    pub code: String,
}

/// Import-specific errors mapped to HTTP status codes.
#[derive(Debug)]
pub enum ImportError {
    /// No file part was found in the multipart body.
    NoFile,
    /// The multipart body could not be read.
    MalformedMultipart(String),
    /// File exceeded [`MAX_FILE_BYTES`].
    TooLarge { size: usize },
    /// Unsupported file extension / content type.
    UnsupportedType { detail: String },
    /// File decoded but contained no parseable holdings.
    ParseFailed(String),
    /// A confirm request referenced a portfolio that does not exist.
    PortfolioNotFound(String),
    /// Generic validation failure (empty fields, bad base64, etc.).
    Validation(String),
    /// Persistence failed.
    Storage(String),
}

impl ImportError {
    fn status_code(&self) -> StatusCode {
        match self {
            ImportError::NoFile
            | ImportError::MalformedMultipart(_)
            | ImportError::TooLarge { .. }
            | ImportError::UnsupportedType { .. }
            | ImportError::Validation(_)
            | ImportError::PortfolioNotFound(_) => StatusCode::BAD_REQUEST,
            ImportError::ParseFailed(_) => StatusCode::UNPROCESSABLE_ENTITY,
            ImportError::Storage(_) => StatusCode::INTERNAL_SERVER_ERROR,
        }
    }

    fn code(&self) -> &'static str {
        match self {
            ImportError::NoFile => "NO_FILE",
            ImportError::MalformedMultipart(_) => "MALFORMED_MULTIPART",
            ImportError::TooLarge { .. } => "FILE_TOO_LARGE",
            ImportError::UnsupportedType { .. } => "UNSUPPORTED_TYPE",
            ImportError::ParseFailed(_) => "PARSE_FAILED",
            ImportError::PortfolioNotFound(_) => "PORTFOLIO_NOT_FOUND",
            ImportError::Validation(_) => "VALIDATION_ERROR",
            ImportError::Storage(_) => "STORAGE_ERROR",
        }
    }

    fn message(&self) -> String {
        match self {
            ImportError::NoFile => "No 'file' part found in upload".to_string(),
            ImportError::MalformedMultipart(m) => format!("Malformed multipart body: {m}"),
            ImportError::TooLarge { size } => format!(
                "File is {size} bytes; maximum allowed is {MAX_FILE_BYTES} bytes"
            ),
            ImportError::UnsupportedType { detail } => {
                format!("Unsupported file type: {detail}. Expected .csv, .ofx, or .qfx")
            }
            ImportError::ParseFailed(m) => format!("Could not parse file: {m}"),
            ImportError::PortfolioNotFound(id) => format!("Portfolio not found: {id}"),
            ImportError::Validation(m) => format!("Validation failed: {m}"),
            ImportError::Storage(m) => format!("Storage error: {m}"),
        }
    }
}

fn err_response(e: ImportError) -> (StatusCode, Json<ImportErrorResponse>) {
    let status = e.status_code();
    if status.is_server_error() {
        tracing::error!(code = e.code(), "import error: {}", e.message());
    } else {
        tracing::warn!(code = e.code(), "import error: {}", e.message());
    }
    (
        status,
        Json(ImportErrorResponse {
            error: e.message(),
            code: e.code().to_string(),
        }),
    )
}

// --- Step 1: upload + parse for review ---

/// A holding parsed from the uploaded file, returned for user review.
/// Mirrors the Python `ImportedHolding` dataclass field-for-field.
#[derive(Serialize)]
pub struct ParsedHolding {
    pub symbol: String,
    pub quantity: f64,
    pub market_value: f64,
    pub cost_basis: Option<f64>,
    pub currency: String,
    pub yield_pct: Option<f64>,
}

/// Response from the upload/parse step.
#[derive(Serialize)]
pub struct ImportPreviewResponse {
    pub filename: String,
    pub holdings: Vec<ParsedHolding>,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
    pub rows_total: usize,
    pub rows_imported: usize,
}

/// `POST /api/v1/import/csv`
///
/// Accepts a multipart form upload with a `file` field. Validates the
/// file size (<= 10 MB) and extension (.csv/.ofx/.qfx), parses it, and
/// returns the parsed holdings for review. Nothing is persisted.
///
/// Returns:
/// - 400 for a missing/oversized/unsupported file,
/// - 422 if the file decodes but no holdings can be parsed,
/// - 200 with the preview otherwise (which may still carry per-row errors).
pub async fn import_csv(
    Multipart(multipart): Multipart,
) -> Result<Json<ImportPreviewResponse>, (StatusCode, Json<ImportErrorResponse>)> {
    let (filename, bytes) = read_upload(multipart).await.map_err(err_response)?;

    // Extension / type validation.
    let lower = filename.to_ascii_lowercase();
    if !(lower.ends_with(".csv") || lower.ends_with(".ofx") || lower.ends_with(".qfx")) {
        return Err(err_response(ImportError::UnsupportedType {
            detail: filename.clone(),
        }));
    }

    if bytes.len() > MAX_FILE_BYTES {
        return Err(err_response(ImportError::TooLarge { size: bytes.len() }));
    }
    if bytes.is_empty() {
        return Err(err_response(ImportError::ParseFailed("file is empty".to_string())));
    }

    // Decode UTF-8 (lossy) so a stray byte never aborts the whole import.
    let text = String::from_utf8_lossy(&bytes).into_owned();

    let preview = parse_csv_for_review(&filename, &text)?;
    Ok(Json(preview))
}

/// Read the first `file` part of a multipart body into (filename, bytes).
async fn read_upload(mut multipart: Multipart) -> Result<(String, Vec<u8>), ImportError> {
    while let Some(field) = multipart
        .next_field()
        .await
        .map_err(|e| ImportError::MalformedMultipart(e.to_string()))?
    {
        // Accept either a field literally named "file" or the first part
        // that carries a filename.
        let name = field.name().unwrap_or("").to_string();
        let filename = field.file_name().map(|s| s.to_string());

        if name == "file" || filename.is_some() {
            let fname = filename.unwrap_or_else(|| "upload.csv".to_string());
            let data = field
                .bytes()
                .await
                .map_err(|e| ImportError::MalformedMultipart(e.to_string()))?;
            return Ok((fname, data.to_vec()));
        }
    }
    Err(ImportError::NoFile)
}

/// Minimal generic-CSV review parser.
///
/// Maps header synonyms to symbol / quantity / market_value / cost_basis /
/// currency / yield columns, then validates each row using the same rules
/// as the Python importer. Per-row failures are collected into `errors`;
/// recoverable issues into `warnings`.
fn parse_csv_for_review(
    filename: &str,
    text: &str,
) -> Result<ImportPreviewResponse, (StatusCode, Json<ImportErrorResponse>)> {
    let mut lines = text.lines().filter(|l| !l.trim().is_empty());

    let header = lines
        .next()
        .ok_or_else(|| err_response(ImportError::ParseFailed("file has no header row".to_string())))?;

    let headers: Vec<String> = header.split(',').map(normalize_header).collect();
    let col = ColumnMap::from_headers(&headers);

    if col.symbol.is_none() {
        return Err(err_response(ImportError::ParseFailed(
            "could not find a symbol/ticker column".to_string(),
        )));
    }
    if col.market_value.is_none() && col.quantity.is_none() {
        return Err(err_response(ImportError::ParseFailed(
            "could not find a market value or quantity column".to_string(),
        )));
    }

    let mut holdings = Vec::new();
    let mut errors = Vec::new();
    let mut warnings = Vec::new();
    let mut rows_total = 0usize;

    for (i, line) in lines.enumerate() {
        let row_num = i + 2; // header is line 1
        let fields: Vec<&str> = line.split(',').collect();
        rows_total += 1;

        let symbol = col
            .symbol
            .and_then(|idx| fields.get(idx))
            .map(|s| s.trim().to_ascii_uppercase())
            .unwrap_or_default();

        let quantity = col.quantity.and_then(|idx| fields.get(idx)).and_then(|s| parse_num(s));
        let market_value =
            col.market_value.and_then(|idx| fields.get(idx)).and_then(|s| parse_num(s));
        let cost_basis = col.cost_basis.and_then(|idx| fields.get(idx)).and_then(|s| parse_num(s));
        let yield_pct = col.yield_pct.and_then(|idx| fields.get(idx)).and_then(|s| parse_num(s));
        let currency = col
            .currency
            .and_then(|idx| fields.get(idx))
            .map(|s| s.trim().to_ascii_uppercase())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "USD".to_string());

        // Validation (identical contract to the Python importer).
        if symbol.is_empty() {
            errors.push(format!("Row {row_num}: empty symbol; row skipped"));
            continue;
        }
        if let Some(q) = quantity {
            if q.is_nan() || q.is_infinite() {
                errors.push(format!("Row {row_num} ({symbol}): quantity is NaN/Inf; row skipped"));
                continue;
            }
            if q < 0.0 {
                errors.push(format!("Row {row_num} ({symbol}): negative quantity; row skipped"));
                continue;
            }
        }
        if let Some(mv) = market_value {
            if mv.is_nan() || mv.is_infinite() {
                errors.push(format!(
                    "Row {row_num} ({symbol}): market_value is NaN/Inf; row skipped"
                ));
                continue;
            }
            if mv < 0.0 {
                errors.push(format!(
                    "Row {row_num} ({symbol}): negative market_value; row skipped"
                ));
                continue;
            }
        }
        if quantity.is_none() && market_value.is_none() {
            errors.push(format!(
                "Row {row_num} ({symbol}): no quantity or market_value; row skipped"
            ));
            continue;
        }

        let cost_basis = match cost_basis {
            Some(c) if c < 0.0 => {
                warnings.push(format!(
                    "Row {row_num} ({symbol}): negative cost_basis; treated as unknown"
                ));
                None
            }
            other => other,
        };

        holdings.push(ParsedHolding {
            symbol,
            quantity: quantity.unwrap_or(0.0),
            market_value: market_value.unwrap_or(0.0),
            cost_basis,
            currency,
            yield_pct,
        });
    }

    let rows_imported = holdings.len();
    if rows_imported == 0 {
        return Err(err_response(ImportError::ParseFailed(
            "no valid holdings found in file".to_string(),
        )));
    }

    Ok(ImportPreviewResponse {
        filename: filename.to_string(),
        holdings,
        errors,
        warnings,
        rows_total,
        rows_imported,
    })
}

/// Header synonym -> column index resolver.
struct ColumnMap {
    symbol: Option<usize>,
    quantity: Option<usize>,
    market_value: Option<usize>,
    cost_basis: Option<usize>,
    currency: Option<usize>,
    yield_pct: Option<usize>,
}

impl ColumnMap {
    fn from_headers(headers: &[String]) -> Self {
        let find = |syns: &[&str]| -> Option<usize> {
            headers.iter().position(|h| syns.contains(&h.as_str()))
        };
        ColumnMap {
            symbol: find(&["symbol", "ticker", "instrument", "security", "stocksymbol"]),
            quantity: find(&[
                "quantity", "qty", "shares", "units", "position", "amount", "openquantity",
            ]),
            market_value: find(&[
                "marketvalue", "value", "currentvalue", "positionvalue", "marketvaluebase",
                "totalvalue", "mktvalue", "currentmarketvalue",
            ]),
            cost_basis: find(&["costbasis", "cost", "bookvalue", "totalcost", "averagecost", "acb"]),
            currency: find(&["currency", "ccy", "curr"]),
            yield_pct: find(&["yield", "yieldpct", "dividendyield", "divyield"]),
        }
    }
}

/// Normalize a header cell to lowercase alphanumerics only.
fn normalize_header(h: &str) -> String {
    h.trim()
        .to_ascii_lowercase()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect()
}

/// Best-effort numeric parse: strips currency symbols, separators, percent,
/// and treats `(123)` as `-123`. Returns None for blank / unparseable cells.
fn parse_num(raw: &str) -> Option<f64> {
    let s = raw.trim();
    if s.is_empty() || matches!(s.to_ascii_uppercase().as_str(), "N/A" | "NA" | "-" | "--") {
        return None;
    }

    let (negative, body) = if s.starts_with('(') && s.ends_with(')') {
        (true, &s[1..s.len() - 1])
    } else {
        (false, s)
    };

    let cleaned: String = body
        .chars()
        .filter(|c| c.is_ascii_digit() || *c == '.' || *c == '-' || *c == '+')
        .collect();
    if cleaned.is_empty() {
        return None;
    }

    cleaned.parse::<f64>().ok().map(|v| if negative { -v } else { v })
}

// --- Step 2: confirm + persist ---

/// One encrypted holding to persist. The client encrypts symbol and the
/// analytics payload (weight/returns/yield/cost_basis/market_value) with
/// AES-256-GCM before sending; the engine stores only ciphertext.
#[derive(Deserialize)]
pub struct ConfirmHolding {
    pub symbol_encrypted_b64: String,
    pub symbol_nonce_b64: String,
    pub payload_encrypted_b64: String,
    pub payload_nonce_b64: String,
    pub account_id: Option<String>,
}

/// Request body for `POST /api/v1/import/confirm`.
#[derive(Deserialize)]
pub struct ConfirmImportRequest {
    pub portfolio_id: String,
    pub holdings: Vec<ConfirmHolding>,
}

/// Response from the confirm step.
#[derive(Serialize)]
pub struct ConfirmImportResponse {
    pub portfolio_id: String,
    pub saved: usize,
    pub holding_ids: Vec<String>,
}

/// `POST /api/v1/import/confirm`
///
/// Persists reviewed, client-encrypted holdings to SQLite under
/// `portfolio_id`. Returns the generated holding ids.
///
/// Returns 400 for an empty portfolio_id, an empty holdings list, bad
/// base64, or an unknown portfolio; 500 if persistence fails.
pub async fn confirm_import(
    State(store): State<Arc<Store>>,
    Json(req): Json<ConfirmImportRequest>,
) -> Result<Json<ConfirmImportResponse>, (StatusCode, Json<ImportErrorResponse>)> {
    if req.portfolio_id.trim().is_empty() {
        return Err(err_response(ImportError::Validation(
            "portfolio_id must not be empty".to_string(),
        )));
    }
    if req.holdings.is_empty() {
        return Err(err_response(ImportError::Validation(
            "holdings must not be empty".to_string(),
        )));
    }

    // Verify the portfolio exists up front so we fail fast with a 400.
    match store.portfolio_exists(&req.portfolio_id) {
        Ok(true) => {}
        Ok(false) => {
            return Err(err_response(ImportError::PortfolioNotFound(
                req.portfolio_id.clone(),
            )))
        }
        Err(e) => return Err(err_response(ImportError::Storage(e.to_string()))),
    }

    let mut holding_ids = Vec::with_capacity(req.holdings.len());
    for (i, h) in req.holdings.iter().enumerate() {
        let symbol_encrypted = decode_b64(&h.symbol_encrypted_b64, i, "symbol_encrypted_b64")?;
        let symbol_nonce = decode_b64(&h.symbol_nonce_b64, i, "symbol_nonce_b64")?;
        let payload_encrypted = decode_b64(&h.payload_encrypted_b64, i, "payload_encrypted_b64")?;
        let payload_nonce = decode_b64(&h.payload_nonce_b64, i, "payload_nonce_b64")?;

        let new = NewHolding {
            portfolio_id: req.portfolio_id.clone(),
            account_id: h.account_id.clone(),
            symbol_encrypted,
            symbol_nonce,
            payload_encrypted,
            payload_nonce,
        };

        let id = store
            .insert_holding(&new)
            .map_err(|e| err_response(ImportError::Storage(e.to_string())))?;
        holding_ids.push(id);
    }

    tracing::info!(
        portfolio_id = %req.portfolio_id,
        saved = holding_ids.len(),
        "import confirmed and persisted"
    );

    Ok(Json(ConfirmImportResponse {
        portfolio_id: req.portfolio_id,
        saved: holding_ids.len(),
        holding_ids,
    }))
}

/// Decode a base64 field, attributing failures to a specific holding index.
fn decode_b64(
    value: &str,
    index: usize,
    field: &str,
) -> Result<Vec<u8>, (StatusCode, Json<ImportErrorResponse>)> {
    if value.is_empty() {
        return Err(err_response(ImportError::Validation(format!(
            "holding[{index}].{field} must not be empty"
        ))));
    }
    B64.decode(value).map_err(|e| {
        err_response(ImportError::Validation(format!(
            "holding[{index}].{field} is not valid base64: {e}"
        )))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_num_variants() {
        assert_eq!(parse_num("1,850.50"), Some(1850.50));
        assert_eq!(parse_num("$1,500"), Some(1500.0));
        assert_eq!(parse_num("(500.00)"), Some(-500.0));
        assert_eq!(parse_num("N/A"), None);
        assert_eq!(parse_num(""), None);
        assert_eq!(parse_num("12.5%"), Some(12.5));
    }

    #[test]
    fn test_normalize_header() {
        assert_eq!(normalize_header(" Current Market Value "), "currentmarketvalue");
        assert_eq!(normalize_header("Open_Quantity"), "openquantity");
    }

    #[test]
    fn test_parse_csv_for_review_generic() {
        let csv = "symbol,quantity,market_value,currency\nAAPL,100,18500.50,USD\nKO,120,7200,USD\n";
        let preview = parse_csv_for_review("x.csv", csv).unwrap();
        assert_eq!(preview.rows_imported, 2);
        assert_eq!(preview.holdings[0].symbol, "AAPL");
        assert_eq!(preview.holdings[0].market_value, 18500.50);
    }

    #[test]
    fn test_parse_csv_rejects_negative_and_empty() {
        let csv = "symbol,quantity,market_value\n,5,100\nNEG,-3,500\nGOOD,10,1000\n";
        let preview = parse_csv_for_review("x.csv", csv).unwrap();
        assert_eq!(preview.rows_imported, 1);
        assert_eq!(preview.holdings[0].symbol, "GOOD");
        assert_eq!(preview.errors.len(), 2);
    }

    #[test]
    fn test_parse_csv_no_symbol_column_fails() {
        let csv = "quantity,market_value\n10,100\n";
        assert!(parse_csv_for_review("x.csv", csv).is_err());
    }
}
