"""FastAPI web application for the swing trading dashboard."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from swing.analysis.indicators import compute_indicators
from swing.analysis.levels import compute_levels
from swing.analysis.scorer import compute_score, rank_candidates
from swing.analysis.signals import detect_signals
from swing.config import MIN_PRICE, MIN_PRICE_US, SCAN_RATE_LIMIT_PER_MINUTE, WARMUP_DAYS
from swing.data.cache import (
    get_web_scan_cache,
    max_stocks_cache_key,
    save_web_scan_cache,
)
from swing.data.fetcher import fetch_ohlcv, fetch_current_prices_batch
from swing.data.nifty_indices import (
    get_nifty100_stocks,
    get_nifty200_stocks,
    get_nifty50_stocks,
    get_nifty500_stocks,
)
from swing.data.us_stocks import get_dow30_stocks, get_nasdaq100_stocks, get_sp500_stocks
from swing.utils.logger import get_logger

log = get_logger(__name__)

app = FastAPI(title="ScreenerX.ai", version="0.1.0")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_scan_locks: dict[str, asyncio.Lock] = {}
_locks_init_lock = asyncio.Lock()
_rate_log: dict[str, list[float]] = defaultdict(list)


async def _scan_lock(cache_key: str) -> asyncio.Lock:
    async with _locks_init_lock:
        if cache_key not in _scan_locks:
            _scan_locks[cache_key] = asyncio.Lock()
        return _scan_locks[cache_key]


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _rate_limited(client: str) -> bool:
    if SCAN_RATE_LIMIT_PER_MINUTE <= 0:
        return False
    now = time.monotonic()
    bucket = _rate_log[client]
    cutoff = now - 60.0
    while bucket and bucket[0] < cutoff:
        bucket.pop(0)
    if len(bucket) >= SCAN_RATE_LIMIT_PER_MINUTE:
        return True
    bucket.append(now)
    return False


def _run_scan_sync(market: str, max_stocks: int | None) -> dict:
    """CPU/network-heavy scan; run via asyncio.to_thread."""
    market_fetchers = {
        "nifty_50": get_nifty50_stocks,
        "nifty_100": get_nifty100_stocks,
        "nifty_200": get_nifty200_stocks,
        "nifty_500": get_nifty500_stocks,
        "dow_30": get_dow30_stocks,
        "nasdaq_100": get_nasdaq100_stocks,
        "sp_500": get_sp500_stocks,
    }

    us_markets = {"dow_30", "nasdaq_100", "sp_500"}

    fetcher = market_fetchers.get(market)
    if not fetcher:
        raise ValueError(f"Unknown market: {market}")

    stocks = fetcher()
    if not stocks:
        raise RuntimeError(f"Could not load stock list for {market}")

    if max_stocks is not None:
        stocks = stocks[:max_stocks]

    candidates = []
    stats = {"total": len(stocks), "scanned": 0, "filtered": 0, "skipped": 0}

    for stock_info in stocks:
        ticker = stock_info["yf_ticker"]
        symbol = stock_info["symbol"]

        df = fetch_ohlcv(ticker, use_cache=True)
        if df is None or len(df) < WARMUP_DAYS:
            stats["skipped"] += 1
            stats["scanned"] += 1
            continue

        df = compute_indicators(df)
        min_price = MIN_PRICE_US if market in us_markets else MIN_PRICE
        result = detect_signals(df, min_price=min_price)

        if not result.get("passed"):
            stats["filtered"] += 1
            stats["scanned"] += 1
            continue

        levels = compute_levels(result)
        if levels is None:
            stats["filtered"] += 1
            stats["scanned"] += 1
            continue

        score_result = compute_score(result, levels)

        sparkline = df["Close"].tail(30).tolist()

        candidates.append(
            {
                "symbol": symbol,
                "ticker": ticker,
                "company": stock_info["company"],
                "industry": stock_info["industry"],
                "score": score_result["total"],
                "score_breakdown": score_result["factors"],
                "signals": result["signals"],
                "signal_count": result["signal_count"],
                "latest": result["latest"],
                "levels": levels,
                "sparkline": [round(v, 2) for v in sparkline],
            }
        )
        stats["scanned"] += 1

    candidates = rank_candidates(candidates)

    currency = "$" if market in us_markets else "₹"
    scanned_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "candidates": candidates,
        "stats": stats,
        "count": len(candidates),
        "market": market,
        "currency": currency,
        "scanned_at": scanned_at,
        "cached": False,
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the dashboard."""
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/api/results")
async def results(market: str = "nifty_500", max_stocks: int | None = None):
    """Return cached scan for this market/scope if still within CACHE_TTL_SECONDS."""
    ms_key = max_stocks_cache_key(max_stocks)
    cached = get_web_scan_cache(market, ms_key)
    if cached is not None:
        return JSONResponse(cached)
    return JSONResponse({"cached": False})


@app.get("/api/scan")
async def scan(
    request: Request,
    market: str = "nifty_500",
    max_stocks: int | None = None,
    fresh: bool = False,
):
    """Run the screener. Reuses the cached full-scan result if within CACHE_TTL_SECONDS unless fresh=1.

    Per-ticker OHLCV uses the same TTL on disk. Concurrent scans share one lock so
    only one live fetch runs for a given market/scope.
    """
    if _rate_limited(_client_key(request)):
        return JSONResponse(
            {
                "error": "Too many scan requests from this client; try again in a minute.",
            },
            status_code=429,
        )

    ms_key = max_stocks_cache_key(max_stocks)
    lock_key = f"{market}:{ms_key}"

    if not fresh:
        hit = get_web_scan_cache(market, ms_key)
        if hit is not None:
            return JSONResponse(hit)

    lock = await _scan_lock(lock_key)
    async with lock:
        if not fresh:
            hit = get_web_scan_cache(market, ms_key)
            if hit is not None:
                return JSONResponse(hit)
        try:
            data = await asyncio.to_thread(_run_scan_sync, market, max_stocks)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            log.warning("Scan failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)

        save_web_scan_cache(market, ms_key, data, data["scanned_at"])
        return JSONResponse(data)


@app.get("/api/quotes")
async def quotes(tickers: str = ""):
    """Return current/last price for given tickers (comma-separated). Uses yfinance; not cached."""
    if not tickers or not tickers.strip():
        return JSONResponse({})
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    prices = fetch_current_prices_batch(ticker_list)
    return JSONResponse(prices)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


def start_server():
    """Start the uvicorn server."""
    import uvicorn
    from swing.config import WEB_HOST, WEB_PORT

    print(f"\n🌐 Dashboard starting at http://localhost:{WEB_PORT}\n")
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="info")
