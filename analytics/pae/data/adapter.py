"""Unified data adapter for PAE.

Provides a single interface across all data sources (Yahoo Finance,
Fama-French, FRED, commodities, forex). Implements source priority,
health checks, and graceful fallback.

This is the entry point for all data access in PAE. Individual adapters
should not be called directly by the engine or UI.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pae.data.cache import DataCache
from pae.data.commodities import (
    CommodityAdapter,
    CommodityError,
    CommodityQuote,
)
from pae.data.factors import (
    FactorAdapter,
    FactorData,
    FactorDataError,
    MacroSeries,
)
from pae.data.yahoo import (
    PriceHistory,
    QuoteData,
    YahooAdapter,
    YahooError,
)

logger = logging.getLogger(__name__)


@dataclass
class SourceHealth:
    """Health status of a data source.

    Attributes:
        source: Source name (e.g. 'yahoo', 'fred', 'french').
        healthy: Whether the last request succeeded.
        last_check: Unix timestamp of the last health check.
        last_error: Last error message, if any.
        latency_ms: Last observed response time in milliseconds.
        total_requests: Total requests made to this source.
        total_errors: Total failed requests.
    """

    source: str
    healthy: bool = True
    last_check: float = 0.0
    last_error: str = ""
    latency_ms: float = 0.0
    total_requests: int = 0
    total_errors: int = 0

    @property
    def error_rate(self) -> float:
        """Error rate as a fraction (0.0 to 1.0)."""
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests


@dataclass
class MarketSnapshot:
    """Aggregated market data snapshot.

    Combines quotes, commodity prices, forex rates, and index levels
    into a single view for the dashboard.

    Attributes:
        timestamp: When the snapshot was taken.
        quotes: Dict of symbol -> QuoteData for portfolio holdings.
        commodities: List of commodity quotes.
        forex: List of forex quotes.
        indices: List of index quotes.
        risk_free_rate: Current annualized risk-free rate.
        errors: List of error messages from failed fetches.
    """

    timestamp: float = 0.0
    quotes: dict[str, QuoteData] = field(default_factory=dict)
    commodities: list[CommodityQuote] = field(default_factory=list)
    forex: list[CommodityQuote] = field(default_factory=list)
    indices: list[CommodityQuote] = field(default_factory=list)
    risk_free_rate: float = 0.0
    errors: list[str] = field(default_factory=list)


class DataAdapterError(Exception):
    """Raised when the unified data adapter encounters an unrecoverable error."""


class DataAdapter:
    """Unified data adapter for all PAE data needs.

    Provides a single interface for quotes, price history, factor data,
    commodities, forex, and macro indicators. Manages source health,
    caching, and graceful degradation when individual sources fail.

    Args:
        cache_dir: Optional custom cache directory path.

    Example:
        >>> adapter = DataAdapter()
        >>> quote = adapter.get_quote("AAPL")
        >>> history = adapter.get_history("SPY", period="1y")
        >>> factors = adapter.get_factors(model="ff5")
        >>> oil = adapter.get_commodity("crude_oil")
        >>> snapshot = adapter.get_market_snapshot(["AAPL", "SPY", "AGG"])
    """

    def __init__(self, cache_dir: Any = None) -> None:
        from pathlib import Path

        cd = Path(cache_dir) if cache_dir else None
        self._cache = DataCache(cache_dir=cd)
        self._yahoo = YahooAdapter(cache=self._cache)
        self._factors = FactorAdapter(cache=self._cache)
        self._commodities = CommodityAdapter(
            cache=self._cache, yahoo=self._yahoo
        )

        # Source health tracking
        self._health: dict[str, SourceHealth] = {
            "yahoo": SourceHealth(source="yahoo"),
            "french": SourceHealth(source="french"),
            "fred": SourceHealth(source="fred"),
        }

    def _track_request(
        self, source: str, success: bool, error: str = "", latency_ms: float = 0.0
    ) -> None:
        """Update health tracking for a data source."""
        if source not in self._health:
            self._health[source] = SourceHealth(source=source)

        h = self._health[source]
        h.total_requests += 1
        h.last_check = time.time()
        h.latency_ms = latency_ms

        if success:
            h.healthy = True
            h.last_error = ""
        else:
            h.total_errors += 1
            h.healthy = False
            h.last_error = error
            logger.warning("Source '%s' error: %s", source, error)

    # --- Quotes ---

    def get_quote(self, symbol: str) -> QuoteData:
        """Fetch current quote for any tradeable instrument.

        Args:
            symbol: Ticker symbol (stock, ETF, commodity future, forex pair).

        Returns:
            QuoteData with current price and metadata.

        Raises:
            ValueError: If symbol is empty.
            DataAdapterError: If the fetch fails.
        """
        start = time.time()
        try:
            result = self._yahoo.get_quote(symbol)
            self._track_request(
                "yahoo", True, latency_ms=(time.time() - start) * 1000
            )
            return result
        except (YahooError, ValueError) as e:
            self._track_request("yahoo", False, str(e))
            msg = f"Failed to fetch quote for '{symbol}': {e}"
            raise DataAdapterError(msg) from e

    def get_quotes(self, symbols: list[str]) -> dict[str, QuoteData]:
        """Fetch quotes for multiple symbols.

        Continues past individual failures. Failed symbols are logged
        but not included in results.

        Args:
            symbols: List of ticker symbols.

        Returns:
            Dict mapping symbol to QuoteData (only successful fetches).
        """
        results: dict[str, QuoteData] = {}
        for sym in symbols:
            try:
                results[sym.strip().upper()] = self.get_quote(sym)
            except (DataAdapterError, ValueError) as e:
                logger.warning("Skipping quote for '%s': %s", sym, e)
        return results

    # --- Price History ---

    def get_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> PriceHistory:
        """Fetch historical prices and returns for any instrument.

        Args:
            symbol: Ticker symbol.
            period: Lookback period ('1mo', '3mo', '6mo', '1y', '2y', '5y', 'max').
            interval: Data interval ('1d', '1wk', '1mo').

        Returns:
            PriceHistory with dates, prices, returns, and summary stats.

        Raises:
            ValueError: If inputs are invalid.
            DataAdapterError: If the fetch fails.
        """
        start = time.time()
        try:
            result = self._yahoo.get_price_history(symbol, period, interval)
            self._track_request(
                "yahoo", True, latency_ms=(time.time() - start) * 1000
            )
            return result
        except (YahooError, ValueError) as e:
            self._track_request("yahoo", False, str(e))
            msg = f"Failed to fetch history for '{symbol}': {e}"
            raise DataAdapterError(msg) from e

    # --- Factor Data ---

    def get_factors(self, model: str = "ff5") -> dict[str, FactorData]:
        """Fetch Fama-French factor return series.

        Args:
            model: Factor model ('ff3' for 3-factor, 'ff5' for 5-factor).

        Returns:
            Dict mapping factor name to FactorData.

        Raises:
            ValueError: If model is invalid.
            DataAdapterError: If the fetch fails.
        """
        if model not in ("ff3", "ff5"):
            msg = f"Invalid factor model '{model}'. Must be 'ff3' or 'ff5'."
            raise ValueError(msg)

        start = time.time()
        try:
            if model == "ff3":
                result = self._factors.get_ff3_factors()
            else:
                result = self._factors.get_ff5_factors()
            self._track_request(
                "french", True, latency_ms=(time.time() - start) * 1000
            )
            return result
        except FactorDataError as e:
            self._track_request("french", False, str(e))
            msg = f"Failed to fetch {model} factors: {e}"
            raise DataAdapterError(msg) from e

    def get_risk_free_rate(self) -> float:
        """Get the current annualized risk-free rate.

        Returns:
            Risk-free rate as decimal (e.g. 0.045 for 4.5%).

        Raises:
            DataAdapterError: If FRED data is unavailable.
        """
        start = time.time()
        try:
            result = self._factors.get_risk_free_rate()
            self._track_request(
                "fred", True, latency_ms=(time.time() - start) * 1000
            )
            return result
        except FactorDataError as e:
            self._track_request("fred", False, str(e))
            msg = f"Failed to fetch risk-free rate: {e}"
            raise DataAdapterError(msg) from e

    # --- Macro Data ---

    def get_macro_series(
        self, series_id: str, start_date: str = "2000-01-01"
    ) -> MacroSeries:
        """Fetch a FRED macroeconomic time series.

        Args:
            series_id: FRED series ID (e.g. 'CPIAUCSL', 'UNRATE', 'VIXCLS').
            start_date: Start date (YYYY-MM-DD).

        Returns:
            MacroSeries with dates and values.

        Raises:
            ValueError: If series_id is empty.
            DataAdapterError: If the fetch fails.
        """
        start = time.time()
        try:
            result = self._factors.get_fred_series(series_id, start_date)
            self._track_request(
                "fred", True, latency_ms=(time.time() - start) * 1000
            )
            return result
        except (FactorDataError, ValueError) as e:
            self._track_request("fred", False, str(e))
            msg = f"Failed to fetch FRED series '{series_id}': {e}"
            raise DataAdapterError(msg) from e

    # --- Commodities & Forex ---

    def get_commodity(self, key: str) -> CommodityQuote:
        """Fetch current commodity quote.

        Args:
            key: Commodity key (e.g. 'crude_oil', 'gold', 'natural_gas').

        Returns:
            CommodityQuote with price and metadata.

        Raises:
            ValueError: If key is unknown.
            DataAdapterError: If the fetch fails.
        """
        try:
            return self._commodities.get_commodity_quote(key)
        except (CommodityError, ValueError) as e:
            msg = f"Failed to fetch commodity '{key}': {e}"
            raise DataAdapterError(msg) from e

    def get_forex(self, key: str) -> CommodityQuote:
        """Fetch current forex rate.

        Args:
            key: Forex pair key (e.g. 'usd_cad', 'eur_usd').

        Returns:
            CommodityQuote with exchange rate.

        Raises:
            ValueError: If key is unknown.
            DataAdapterError: If the fetch fails.
        """
        try:
            return self._commodities.get_forex_quote(key)
        except (CommodityError, ValueError) as e:
            msg = f"Failed to fetch forex '{key}': {e}"
            raise DataAdapterError(msg) from e

    def get_index(self, key: str) -> CommodityQuote:
        """Fetch current index level.

        Args:
            key: Index key (e.g. 'sp500', 'tsx', 'vix').

        Returns:
            CommodityQuote with index level.

        Raises:
            ValueError: If key is unknown.
            DataAdapterError: If the fetch fails.
        """
        try:
            return self._commodities.get_index_quote(key)
        except (CommodityError, ValueError) as e:
            msg = f"Failed to fetch index '{key}': {e}"
            raise DataAdapterError(msg) from e

    # --- Instrument Search ---

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search for tradeable instruments.

        Searches across Yahoo Finance tickers, known commodities,
        forex pairs, and indices.

        Args:
            query: Search string.
            limit: Maximum results (1-50).

        Returns:
            List of dicts with symbol, name, type, exchange.
        """
        if not query or not query.strip():
            return []

        results: list[dict[str, Any]] = []
        q = query.strip().lower()

        # Search known commodities
        for info in self._commodities.list_commodities():
            key = info["key"]
            if q in key or q in info.get("name", "").lower():
                results.append({
                    "symbol": info["symbol"],
                    "name": info["name"],
                    "type": "commodity",
                    "key": key,
                })

        # Search known forex
        for info in self._commodities.list_forex():
            key = info["key"]
            if q in key or q in info.get("name", "").lower():
                results.append({
                    "symbol": info["symbol"],
                    "name": info["name"],
                    "type": "forex",
                    "key": key,
                })

        # Search known indices
        for info in self._commodities.list_indices():
            key = info["key"]
            if q in key or q in info.get("name", "").lower():
                results.append({
                    "symbol": info["symbol"],
                    "name": info["name"],
                    "type": "index",
                    "key": key,
                })

        # Search Yahoo Finance for equities/ETFs
        try:
            yf_results = self._yahoo.search_instruments(query, limit=limit)
            results.extend(yf_results)
        except (YahooError, ValueError):
            pass  # Yahoo search is best-effort

        return results[:limit]

    # --- Market Snapshot ---

    def get_market_snapshot(
        self,
        portfolio_symbols: list[str] | None = None,
        include_commodities: bool = True,
        include_forex: bool = True,
        include_indices: bool = True,
    ) -> MarketSnapshot:
        """Fetch a complete market data snapshot for the dashboard.

        Aggregates portfolio quotes, commodity prices, forex rates,
        and index levels. Continues past individual failures.

        Args:
            portfolio_symbols: List of portfolio holding symbols to quote.
            include_commodities: Whether to fetch commodity prices.
            include_forex: Whether to fetch forex rates.
            include_indices: Whether to fetch index levels.

        Returns:
            MarketSnapshot with all available data and any errors.
        """
        snapshot = MarketSnapshot(timestamp=time.time())

        # Portfolio quotes
        if portfolio_symbols:
            for sym in portfolio_symbols:
                try:
                    snapshot.quotes[sym.strip().upper()] = self.get_quote(sym)
                except (DataAdapterError, ValueError) as e:
                    snapshot.errors.append(f"Quote {sym}: {e}")

        # Commodities
        if include_commodities:
            try:
                snapshot.commodities = self._commodities.get_all_commodity_quotes()
            except Exception as e:
                snapshot.errors.append(f"Commodities: {e}")

        # Forex
        if include_forex:
            try:
                snapshot.forex = self._commodities.get_all_forex_quotes()
            except Exception as e:
                snapshot.errors.append(f"Forex: {e}")

        # Indices
        if include_indices:
            for key in ("sp500", "tsx", "vix", "nasdaq"):
                try:
                    snapshot.indices.append(
                        self._commodities.get_index_quote(key)
                    )
                except (CommodityError, ValueError) as e:
                    snapshot.errors.append(f"Index {key}: {e}")

        # Risk-free rate
        try:
            snapshot.risk_free_rate = self.get_risk_free_rate()
        except DataAdapterError as e:
            snapshot.errors.append(f"Risk-free rate: {e}")

        return snapshot

    # --- Health & Diagnostics ---

    def health(self) -> dict[str, Any]:
        """Return health status of all data sources.

        Returns:
            Dict with per-source health, cache stats, and overall status.
        """
        source_health = {}
        for name, h in self._health.items():
            source_health[name] = {
                "healthy": h.healthy,
                "last_check": h.last_check,
                "last_error": h.last_error,
                "latency_ms": round(h.latency_ms, 1),
                "total_requests": h.total_requests,
                "total_errors": h.total_errors,
                "error_rate": round(h.error_rate, 3),
            }

        all_healthy = all(h.healthy for h in self._health.values())

        return {
            "status": "healthy" if all_healthy else "degraded",
            "sources": source_health,
            "cache": self._cache.stats(),
        }

    def close(self) -> None:
        """Close the data adapter and release resources."""
        self._cache.close()

    def __enter__(self) -> DataAdapter:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
