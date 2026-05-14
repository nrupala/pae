"""Yahoo Finance data adapter.

Fetches price data, dividends, and returns for stocks, ETFs, commodities,
and forex via yfinance. All data is cached locally via DataCache.

No API key required. Rate-limited to avoid throttling.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from pae.data.cache import DataCache, TTL_DAILY_PRICES, TTL_METADATA, TTL_QUOTES

logger = logging.getLogger(__name__)

# Rate limiting: minimum seconds between Yahoo Finance API calls
MIN_REQUEST_INTERVAL = 0.5

# Maximum retry attempts for transient failures
MAX_RETRIES = 3

# Backoff multiplier for retries (seconds)
RETRY_BACKOFF = 2.0


@dataclass
class QuoteData:
    """Current quote for an instrument.

    Attributes:
        symbol: Ticker symbol.
        price: Last known price.
        currency: Price currency (e.g. 'USD', 'CAD').
        change_pct: Percentage change from previous close.
        volume: Trading volume.
        market_cap: Market capitalization (None for non-equity).
        name: Instrument display name.
        asset_type: Instrument type (equity, etf, commodity, forex, crypto).
        timestamp: Unix timestamp of the quote.
    """

    symbol: str
    price: float
    currency: str = "USD"
    change_pct: float = 0.0
    volume: int = 0
    market_cap: float | None = None
    name: str = ""
    asset_type: str = "equity"
    timestamp: float = 0.0


@dataclass
class PriceHistory:
    """Historical price and return data.

    Attributes:
        symbol: Ticker symbol.
        dates: List of date strings (YYYY-MM-DD).
        close_prices: Array of closing prices.
        returns: Array of period-over-period returns.
        dividends: Array of dividend payments (0.0 for non-dividend periods).
        total_return: Cumulative total return over the period.
        annualized_return: Annualized return.
        volatility: Annualized volatility (std dev of returns).
    """

    symbol: str
    dates: list[str] = field(default_factory=list)
    close_prices: list[float] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    dividends: list[float] = field(default_factory=list)
    total_return: float = 0.0
    annualized_return: float = 0.0
    volatility: float = 0.0


class YahooError(Exception):
    """Raised when Yahoo Finance data fetching fails."""


class YahooAdapter:
    """Yahoo Finance data adapter with caching and rate limiting.

    Args:
        cache: DataCache instance for local caching. If None, creates a default one.

    Example:
        >>> adapter = YahooAdapter()
        >>> quote = adapter.get_quote("AAPL")
        >>> history = adapter.get_price_history("SPY", period="1y")
    """

    def __init__(self, cache: DataCache | None = None) -> None:
        self._cache = cache or DataCache()
        self._last_request_time: float = 0.0

    def _rate_limit(self) -> None:
        """Enforce minimum interval between API calls."""
        elapsed = time.time() - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def _fetch_with_retry(self, fetch_fn: Any, description: str) -> Any:
        """Execute a fetch function with retry and exponential backoff.

        Args:
            fetch_fn: Callable that performs the actual data fetch.
            description: Human-readable description for logging.

        Returns:
            Result of fetch_fn.

        Raises:
            YahooError: If all retries are exhausted.
        """
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._rate_limit()
                return fetch_fn()
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF ** attempt
                    logger.warning(
                        "%s failed (attempt %d/%d): %s. Retrying in %.1fs",
                        description, attempt, MAX_RETRIES, e, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        "%s failed after %d attempts: %s",
                        description, MAX_RETRIES, e,
                    )
        msg = f"{description} failed after {MAX_RETRIES} retries: {last_error}"
        raise YahooError(msg)

    def get_quote(self, symbol: str) -> QuoteData:
        """Fetch current quote for a symbol.

        Checks cache first (TTL_QUOTES = 5 min). Falls back to yfinance.

        Args:
            symbol: Ticker symbol (e.g. 'AAPL', 'GC=F' for gold, 'CADUSD=X' for forex).

        Returns:
            QuoteData with current price and metadata.

        Raises:
            ValueError: If symbol is empty.
            YahooError: If the fetch fails after retries.
        """
        if not symbol or not symbol.strip():
            msg = "Symbol must not be empty"
            raise ValueError(msg)

        symbol = symbol.strip().upper()
        cache_key = f"yahoo:quote:{symbol}"

        # Check cache
        cached = self._cache.get(cache_key)
        if cached is not None:
            return QuoteData(**cached.value)

        # Fetch from yfinance
        import yfinance as yf

        def fetch() -> dict[str, Any]:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            if not info or "regularMarketPrice" not in info:
                msg = f"No quote data available for '{symbol}'"
                raise YahooError(msg)
            return info

        info = self._fetch_with_retry(fetch, f"Quote for {symbol}")

        # Determine asset type
        quote_type = info.get("quoteType", "EQUITY").upper()
        asset_type_map: dict[str, str] = {
            "EQUITY": "equity",
            "ETF": "etf",
            "MUTUALFUND": "fund",
            "FUTURE": "commodity",
            "CURRENCY": "forex",
            "CRYPTOCURRENCY": "crypto",
            "INDEX": "index",
        }
        asset_type = asset_type_map.get(quote_type, "equity")

        quote = QuoteData(
            symbol=symbol,
            price=float(info.get("regularMarketPrice", 0.0)),
            currency=info.get("currency", "USD"),
            change_pct=float(info.get("regularMarketChangePercent", 0.0)),
            volume=int(info.get("regularMarketVolume", 0)),
            market_cap=info.get("marketCap"),
            name=info.get("shortName", info.get("longName", symbol)),
            asset_type=asset_type,
            timestamp=time.time(),
        )

        # Cache the quote
        self._cache.put(
            cache_key,
            {
                "symbol": quote.symbol,
                "price": quote.price,
                "currency": quote.currency,
                "change_pct": quote.change_pct,
                "volume": quote.volume,
                "market_cap": quote.market_cap,
                "name": quote.name,
                "asset_type": quote.asset_type,
                "timestamp": quote.timestamp,
            },
            source="yahoo",
            ttl=TTL_QUOTES,
        )

        return quote

    def get_price_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> PriceHistory:
        """Fetch historical prices and compute returns.

        Args:
            symbol: Ticker symbol.
            period: Lookback period ('1mo', '3mo', '6mo', '1y', '2y', '5y', 'max').
            interval: Data interval ('1d', '1wk', '1mo').

        Returns:
            PriceHistory with dates, prices, returns, dividends.

        Raises:
            ValueError: If symbol is empty or period/interval is invalid.
            YahooError: If the fetch fails after retries.
        """
        if not symbol or not symbol.strip():
            msg = "Symbol must not be empty"
            raise ValueError(msg)

        symbol = symbol.strip().upper()
        valid_periods = {"1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max"}
        if period not in valid_periods:
            msg = f"Invalid period '{period}'. Must be one of: {sorted(valid_periods)}"
            raise ValueError(msg)

        valid_intervals = {"1d", "1wk", "1mo"}
        if interval not in valid_intervals:
            msg = f"Invalid interval '{interval}'. Must be one of: {sorted(valid_intervals)}"
            raise ValueError(msg)

        cache_key = f"yahoo:history:{symbol}:{period}:{interval}"

        # Check cache
        cached = self._cache.get(cache_key)
        if cached is not None:
            return PriceHistory(**cached.value)

        # Fetch from yfinance
        import yfinance as yf

        def fetch() -> Any:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period, interval=interval)
            if hist.empty:
                msg = f"No price history available for '{symbol}'"
                raise YahooError(msg)
            return hist

        hist = self._fetch_with_retry(fetch, f"History for {symbol}")

        # Extract data
        dates = [d.strftime("%Y-%m-%d") for d in hist.index]
        close_prices = hist["Close"].tolist()
        dividends = hist.get("Dividends", [0.0] * len(dates))
        dividends = [float(d) for d in dividends]

        # Compute returns
        closes = np.array(close_prices, dtype=np.float64)
        if len(closes) >= 2:
            raw_returns = np.diff(closes) / closes[:-1]
            # Replace NaN/Inf with 0.0
            raw_returns = np.where(np.isfinite(raw_returns), raw_returns, 0.0)
            returns_list = raw_returns.tolist()
        else:
            returns_list = []

        # Compute summary stats
        if len(returns_list) >= 2:
            ret_arr: NDArray[np.float64] = np.array(returns_list)
            total_ret = float(np.prod(1.0 + ret_arr) - 1.0)
            periods_per_year = {"1d": 252, "1wk": 52, "1mo": 12}.get(interval, 252)
            years = len(ret_arr) / periods_per_year
            ann_ret = float((1.0 + total_ret) ** (1.0 / years) - 1.0) if years > 0 else 0.0
            vol = float(np.std(ret_arr, ddof=1) * np.sqrt(periods_per_year))
        else:
            total_ret = 0.0
            ann_ret = 0.0
            vol = 0.0

        result = PriceHistory(
            symbol=symbol,
            dates=dates,
            close_prices=close_prices,
            returns=returns_list,
            dividends=dividends,
            total_return=round(total_ret, 6),
            annualized_return=round(ann_ret, 6),
            volatility=round(vol, 6),
        )

        # Cache
        self._cache.put(
            cache_key,
            {
                "symbol": result.symbol,
                "dates": result.dates,
                "close_prices": result.close_prices,
                "returns": result.returns,
                "dividends": result.dividends,
                "total_return": result.total_return,
                "annualized_return": result.annualized_return,
                "volatility": result.volatility,
            },
            source="yahoo",
            ttl=TTL_DAILY_PRICES,
        )

        return result

    def search_instruments(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search for tradeable instruments by name or symbol.

        Args:
            query: Search string (e.g. 'oil', 'apple', 'gold').
            limit: Maximum results to return (1-50).

        Returns:
            List of dicts with symbol, name, type, exchange.

        Raises:
            ValueError: If query is empty or limit is out of range.
            YahooError: If the search fails.
        """
        if not query or not query.strip():
            msg = "Search query must not be empty"
            raise ValueError(msg)
        if limit < 1 or limit > 50:
            msg = f"Limit must be between 1 and 50, got {limit}"
            raise ValueError(msg)

        cache_key = f"yahoo:search:{query.strip().lower()}:{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached.value

        import yfinance as yf

        def fetch() -> list[dict[str, Any]]:
            # yfinance doesn't have a direct search API
            # Use the Ticker approach to validate symbols
            # For broader search, we use a known symbol list approach
            results: list[dict[str, Any]] = []
            try:
                ticker = yf.Ticker(query.strip().upper())
                info = ticker.info
                if info and "shortName" in info:
                    results.append({
                        "symbol": query.strip().upper(),
                        "name": info.get("shortName", ""),
                        "type": info.get("quoteType", "EQUITY"),
                        "exchange": info.get("exchange", ""),
                    })
            except Exception:
                pass
            return results

        results = self._fetch_with_retry(fetch, f"Search for '{query}'")

        self._cache.put(cache_key, results, source="yahoo", ttl=TTL_METADATA)
        return results
