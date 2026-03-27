"""Download OHLCV data via yfinance.

- OHLCV (for indicators/signals): stock.history(period=1y, interval=1d).
  Cached per ticker for CACHE_TTL_SECONDS (see cache.py / config.py).
- Current price (for display): fetch_current_price() uses fast_info.last_price /
  info.regularMarketPrice. Not cached; fetched on demand when the UI loads or
  refreshes results (/api/quotes).
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf

from swing.config import HISTORY_PERIOD
from swing.data.cache import get_cached_data, save_to_cache
from swing.utils.logger import get_logger

log = get_logger(__name__)


def fetch_ohlcv(ticker: str, use_cache: bool = True) -> pd.DataFrame | None:
    """Fetch daily OHLCV data for a single ticker.

    Returns a DataFrame with columns: Open, High, Low, Close, Volume
    Index is DatetimeIndex. Returns None on failure.
    """
    # Check cache first
    if use_cache:
        cached = get_cached_data(ticker)
        if cached is not None and len(cached) > 50:
            return cached

    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=HISTORY_PERIOD, interval="1d")

        if df is None or df.empty or len(df) < 50:
            log.warning("Insufficient data for %s (%d rows)", ticker, len(df) if df is not None else 0)
            return None

        # Keep only the columns we need
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.dropna(inplace=True)

        # Cache the result
        if use_cache and len(df) > 50:
            save_to_cache(ticker, df)

        return df

    except Exception as exc:
        log.warning("Failed to fetch data for %s: %s", ticker, exc)
        return None


def fetch_current_price(ticker: str) -> float | None:
    """Fetch current/last traded price for a ticker (bypasses daily OHLCV cache).

    Uses yfinance fast_info (last_price) with fallback to info regularMarketPrice.
    Returns None on failure. Intended for display; not cached.
    """
    try:
        stock = yf.Ticker(ticker)
        fi = getattr(stock, "fast_info", None)
        if fi is not None:
            price = getattr(fi, "last_price", None)
            if price is not None and not (isinstance(price, float) and (price != price)):
                return float(price)
        info = stock.info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if price is not None:
            return float(price)
    except Exception as exc:
        log.debug("Current price fetch failed for %s: %s", ticker, exc)
    return None


def fetch_current_prices_batch(tickers: list[str]) -> dict[str, float]:
    """Fetch current price for multiple tickers. Returns dict ticker -> price (only successful)."""
    result: dict[str, float] = {}
    for t in tickers:
        p = fetch_current_price(t)
        if p is not None:
            result[t] = p
    return result


def fetch_batch(
    tickers: list[str],
    use_cache: bool = True,
    progress_callback=None,
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV data for a batch of tickers.

    Returns a dict mapping ticker -> DataFrame.
    Skips tickers that fail or have insufficient data.
    """
    results: dict[str, pd.DataFrame] = {}
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        df = fetch_ohlcv(ticker, use_cache=use_cache)
        if df is not None:
            results[ticker] = df

        if progress_callback:
            progress_callback(i, total, ticker)

    log.info("Fetched data for %d / %d tickers", len(results), total)
    return results
