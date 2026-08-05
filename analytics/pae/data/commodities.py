"""Commodity and forex price feeds.

Provides standardized access to commodity futures (oil, gold, natural gas),
precious metals, agricultural commodities, and forex pairs via Yahoo Finance
commodity tickers.

All data is cached locally. No data leaves the user's machine after fetch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pae.data.cache import DataCache
from pae.data.yahoo import PriceHistory, QuoteData, YahooAdapter, YahooError

logger = logging.getLogger(__name__)

# Yahoo Finance ticker symbols for common commodities
COMMODITY_TICKERS: dict[str, dict[str, str]] = {
    # Energy
    "crude_oil": {"symbol": "CL=F", "name": "WTI Crude Oil", "category": "energy"},
    "brent_oil": {"symbol": "BZ=F", "name": "Brent Crude Oil", "category": "energy"},
    "natural_gas": {"symbol": "NG=F", "name": "Natural Gas", "category": "energy"},
    "heating_oil": {"symbol": "HO=F", "name": "Heating Oil", "category": "energy"},
    "gasoline": {"symbol": "RB=F", "name": "RBOB Gasoline", "category": "energy"},
    # Precious Metals
    "gold": {"symbol": "GC=F", "name": "Gold", "category": "precious_metals"},
    "silver": {"symbol": "SI=F", "name": "Silver", "category": "precious_metals"},
    "platinum": {"symbol": "PL=F", "name": "Platinum", "category": "precious_metals"},
    "palladium": {"symbol": "PA=F", "name": "Palladium", "category": "precious_metals"},
    # Agricultural
    "corn": {"symbol": "ZC=F", "name": "Corn", "category": "agricultural"},
    "wheat": {"symbol": "ZW=F", "name": "Wheat", "category": "agricultural"},
    "soybeans": {"symbol": "ZS=F", "name": "Soybeans", "category": "agricultural"},
    "sugar": {"symbol": "SB=F", "name": "Sugar", "category": "agricultural"},
    "coffee": {"symbol": "KC=F", "name": "Coffee", "category": "agricultural"},
    "cotton": {"symbol": "CT=F", "name": "Cotton", "category": "agricultural"},
    # Industrial Metals
    "copper": {"symbol": "HG=F", "name": "Copper", "category": "industrial_metals"},
}

# Yahoo Finance ticker symbols for major forex pairs
FOREX_TICKERS: dict[str, dict[str, str]] = {
    "usd_cad": {"symbol": "USDCAD=X", "name": "USD/CAD", "category": "forex"},
    "eur_usd": {"symbol": "EURUSD=X", "name": "EUR/USD", "category": "forex"},
    "gbp_usd": {"symbol": "GBPUSD=X", "name": "GBP/USD", "category": "forex"},
    "usd_jpy": {"symbol": "USDJPY=X", "name": "USD/JPY", "category": "forex"},
    "aud_usd": {"symbol": "AUDUSD=X", "name": "AUD/USD", "category": "forex"},
    "usd_chf": {"symbol": "USDCHF=X", "name": "USD/CHF", "category": "forex"},
    "eur_cad": {"symbol": "EURCAD=X", "name": "EUR/CAD", "category": "forex"},
    "gbp_cad": {"symbol": "GBPCAD=X", "name": "GBP/CAD", "category": "forex"},
    "usd_inr": {"symbol": "USDINR=X", "name": "USD/INR", "category": "forex"},
    "cad_inr": {"symbol": "CADINR=X", "name": "CAD/INR", "category": "forex"},
}

# Major index tickers
INDEX_TICKERS: dict[str, dict[str, str]] = {
    "sp500": {"symbol": "^GSPC", "name": "S&P 500", "category": "index"},
    "nasdaq": {"symbol": "^IXIC", "name": "NASDAQ Composite", "category": "index"},
    "dow": {"symbol": "^DJI", "name": "Dow Jones Industrial Average", "category": "index"},
    "tsx": {"symbol": "^GSPTSE", "name": "S&P/TSX Composite", "category": "index"},
    "russell2000": {"symbol": "^RUT", "name": "Russell 2000", "category": "index"},
    "vix": {"symbol": "^VIX", "name": "CBOE Volatility Index", "category": "index"},
    "ftse100": {"symbol": "^FTSE", "name": "FTSE 100", "category": "index"},
    "nikkei": {"symbol": "^N225", "name": "Nikkei 225", "category": "index"},
}


@dataclass
class CommodityQuote:
    """Extended quote with commodity-specific metadata.

    Attributes:
        quote: Base quote data from Yahoo Finance.
        commodity_name: Human-readable commodity name.
        category: Category (energy, precious_metals, agricultural, etc.).
        ticker_key: Internal key for the commodity (e.g. 'crude_oil').
    """

    quote: QuoteData
    commodity_name: str
    category: str
    ticker_key: str


class CommodityError(Exception):
    """Raised when commodity or forex data fetching fails."""


class CommodityAdapter:
    """Adapter for commodity, forex, and index data.

    Wraps YahooAdapter with standardized access to commodity futures,
    forex pairs, and major indices using well-known ticker mappings.

    Args:
        cache: DataCache instance. If None, creates a default one.
        yahoo: YahooAdapter instance. If None, creates one using the cache.

    Example:
        >>> adapter = CommodityAdapter()
        >>> oil = adapter.get_commodity_quote("crude_oil")
        >>> cad = adapter.get_forex_quote("usd_cad")
        >>> gold_history = adapter.get_commodity_history("gold", period="1y")
    """

    def __init__(
        self,
        cache: DataCache | None = None,
        yahoo: YahooAdapter | None = None,
    ) -> None:
        self._cache = cache or DataCache()
        self._yahoo = yahoo or YahooAdapter(cache=self._cache)

    def list_commodities(self) -> list[dict[str, str]]:
        """List all available commodity tickers.

        Returns:
            List of dicts with key, symbol, name, category.
        """
        return [
            {"key": key, **info}
            for key, info in COMMODITY_TICKERS.items()
        ]

    def list_forex(self) -> list[dict[str, str]]:
        """List all available forex pairs.

        Returns:
            List of dicts with key, symbol, name, category.
        """
        return [
            {"key": key, **info}
            for key, info in FOREX_TICKERS.items()
        ]

    def list_indices(self) -> list[dict[str, str]]:
        """List all available index tickers.

        Returns:
            List of dicts with key, symbol, name, category.
        """
        return [
            {"key": key, **info}
            for key, info in INDEX_TICKERS.items()
        ]

    def _resolve_ticker(
        self, key: str, registry: dict[str, dict[str, str]], type_name: str
    ) -> dict[str, str]:
        """Resolve a ticker key to its Yahoo Finance symbol.

        Args:
            key: Internal key (e.g. 'crude_oil', 'usd_cad').
            registry: Ticker registry to look up in.
            type_name: Type name for error messages.

        Returns:
            Ticker info dict with symbol, name, category.

        Raises:
            ValueError: If key is not found in the registry.
        """
        if not key or not key.strip():
            msg = f"{type_name} key must not be empty"
            raise ValueError(msg)

        key = key.strip().lower()
        if key not in registry:
            available = ", ".join(sorted(registry.keys()))
            msg = f"Unknown {type_name} '{key}'. Available: {available}"
            raise ValueError(msg)

        return registry[key]

    def get_commodity_quote(self, key: str) -> CommodityQuote:
        """Fetch current quote for a commodity.

        Args:
            key: Commodity key (e.g. 'crude_oil', 'gold', 'natural_gas').
                Use list_commodities() to see all available keys.

        Returns:
            CommodityQuote with price and metadata.

        Raises:
            ValueError: If key is unknown.
            CommodityError: If the fetch fails.
        """
        info = self._resolve_ticker(key, COMMODITY_TICKERS, "commodity")
        try:
            quote = self._yahoo.get_quote(info["symbol"])
        except YahooError as e:
            msg = f"Failed to fetch commodity quote for '{key}': {e}"
            raise CommodityError(msg) from e

        return CommodityQuote(
            quote=quote,
            commodity_name=info["name"],
            category=info["category"],
            ticker_key=key,
        )

    def get_forex_quote(self, key: str) -> CommodityQuote:
        """Fetch current forex rate.

        Args:
            key: Forex pair key (e.g. 'usd_cad', 'eur_usd').
                Use list_forex() to see all available keys.

        Returns:
            CommodityQuote with exchange rate.

        Raises:
            ValueError: If key is unknown.
            CommodityError: If the fetch fails.
        """
        info = self._resolve_ticker(key, FOREX_TICKERS, "forex pair")
        try:
            quote = self._yahoo.get_quote(info["symbol"])
        except YahooError as e:
            msg = f"Failed to fetch forex quote for '{key}': {e}"
            raise CommodityError(msg) from e

        return CommodityQuote(
            quote=quote,
            commodity_name=info["name"],
            category=info["category"],
            ticker_key=key,
        )

    def get_index_quote(self, key: str) -> CommodityQuote:
        """Fetch current index level.

        Args:
            key: Index key (e.g. 'sp500', 'tsx', 'vix').
                Use list_indices() to see all available keys.

        Returns:
            CommodityQuote with index level.

        Raises:
            ValueError: If key is unknown.
            CommodityError: If the fetch fails.
        """
        info = self._resolve_ticker(key, INDEX_TICKERS, "index")
        try:
            quote = self._yahoo.get_quote(info["symbol"])
        except YahooError as e:
            msg = f"Failed to fetch index quote for '{key}': {e}"
            raise CommodityError(msg) from e

        return CommodityQuote(
            quote=quote,
            commodity_name=info["name"],
            category=info["category"],
            ticker_key=key,
        )

    def get_commodity_history(
        self, key: str, period: str = "1y", interval: str = "1d"
    ) -> PriceHistory:
        """Fetch historical prices for a commodity.

        Args:
            key: Commodity key.
            period: Lookback period.
            interval: Data interval.

        Returns:
            PriceHistory with dates, prices, returns.

        Raises:
            ValueError: If key is unknown.
            CommodityError: If the fetch fails.
        """
        info = self._resolve_ticker(key, COMMODITY_TICKERS, "commodity")
        try:
            return self._yahoo.get_price_history(
                info["symbol"], period=period, interval=interval
            )
        except YahooError as e:
            msg = f"Failed to fetch commodity history for '{key}': {e}"
            raise CommodityError(msg) from e

    def get_forex_history(
        self, key: str, period: str = "1y", interval: str = "1d"
    ) -> PriceHistory:
        """Fetch historical forex rates.

        Args:
            key: Forex pair key.
            period: Lookback period.
            interval: Data interval.

        Returns:
            PriceHistory with dates, rates, returns.

        Raises:
            ValueError: If key is unknown.
            CommodityError: If the fetch fails.
        """
        info = self._resolve_ticker(key, FOREX_TICKERS, "forex pair")
        try:
            return self._yahoo.get_price_history(
                info["symbol"], period=period, interval=interval
            )
        except YahooError as e:
            msg = f"Failed to fetch forex history for '{key}': {e}"
            raise CommodityError(msg) from e

    def get_all_commodity_quotes(self) -> list[CommodityQuote]:
        """Fetch quotes for all tracked commodities.

        Continues past individual failures, logging errors.

        Returns:
            List of CommodityQuote for successful fetches.
        """
        results: list[CommodityQuote] = []
        for key in COMMODITY_TICKERS:
            try:
                results.append(self.get_commodity_quote(key))
            except (CommodityError, ValueError) as e:
                logger.warning("Skipping commodity '%s': %s", key, e)
        return results

    def get_all_forex_quotes(self) -> list[CommodityQuote]:
        """Fetch quotes for all tracked forex pairs.

        Continues past individual failures, logging errors.

        Returns:
            List of CommodityQuote for successful fetches.
        """
        results: list[CommodityQuote] = []
        for key in FOREX_TICKERS:
            try:
                results.append(self.get_forex_quote(key))
            except (CommodityError, ValueError) as e:
                logger.warning("Skipping forex pair '%s': %s", key, e)
        return results
