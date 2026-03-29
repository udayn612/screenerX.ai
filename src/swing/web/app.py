"""FastAPI web application for the swing trading dashboard."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from swing.analysis.indicators import compute_indicators
from swing.analysis.levels import compute_levels
from swing.analysis.scorer import compute_score, rank_candidates
from swing.analysis.signals import detect_signals
from swing.config import (
    BACKGROUND_SCAN_INTERVAL_SECONDS,
    BACKGROUND_SCAN_MARKETS,
    BACKGROUND_SCAN_START_DELAY_SECONDS,
    MIN_PRICE,
    MIN_PRICE_US,
    SCAN_RATE_LIMIT_PER_MINUTE,
    SESSION_COOKIE_SECURE,
    SESSION_SECRET,
    WARMUP_DAYS,
)
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
from swing.web.security import (
    SESSION_USER_KEY,
    auth_enabled,
    create_oauth,
    init_auth_db,
    is_admin_session,
    list_users_for_admin,
    oauth_callback_url,
    record_google_login,
    require_admin_api,
    require_api_user,
)

log = get_logger(__name__)

oauth_google = create_oauth()

_BG_VALID_MARKETS = frozenset(
    {
        "nifty_50",
        "nifty_100",
        "nifty_200",
        "nifty_500",
        "dow_30",
        "nasdaq_100",
        "sp_500",
    }
)


async def _background_scan_refresh_loop() -> None:
    """Refresh SQLite scan cache when missing or past TTL; shares locks with /api/scan."""
    await asyncio.sleep(BACKGROUND_SCAN_START_DELAY_SECONDS)
    while True:
        for market in BACKGROUND_SCAN_MARKETS:
            if market not in _BG_VALID_MARKETS:
                log.warning("BACKGROUND_SCAN_MARKETS: unknown market %r skipped", market)
                continue
            ms_key = max_stocks_cache_key(None)
            if get_web_scan_cache(market, ms_key) is not None:
                continue
            lock_key = f"{market}:{ms_key}"
            lock = await _scan_lock(lock_key)
            async with lock:
                if get_web_scan_cache(market, ms_key) is not None:
                    continue
                try:
                    data = await asyncio.to_thread(_run_scan_sync, market, None)
                    save_web_scan_cache(market, ms_key, data, data["scanned_at"])
                    log.info("Background scan cached %s (%s candidates)", market, data.get("count"))
                except Exception as exc:
                    log.warning("Background scan failed for %s: %s", market, exc)
        await asyncio.sleep(BACKGROUND_SCAN_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_auth_db()
    tasks: list[asyncio.Task[None]] = []
    if BACKGROUND_SCAN_MARKETS:
        log.info(
            "Background scan refresh enabled for: %s (every %ss)",
            ", ".join(BACKGROUND_SCAN_MARKETS),
            BACKGROUND_SCAN_INTERVAL_SECONDS,
        )
        tasks.append(asyncio.create_task(_background_scan_refresh_loop()))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title="ScreenerX.ai", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="sx_session",
    max_age=14 * 24 * 3600,
    same_site="lax",
    https_only=SESSION_COOKIE_SECURE,
)

STATIC_DIR = Path(__file__).parent / "static"
# Avoid stale dashboard HTML in browsers / proxies after deploys.
_HTML_NO_CACHE = {"Cache-Control": "no-store, max-age=0, must-revalidate"}

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_scan_locks: dict[str, asyncio.Lock] = {}
_locks_init_lock = asyncio.Lock()
_rate_log: dict[str, list[float]] = defaultdict(list)


def _session_dep(request: Request) -> dict:
    return require_api_user(request)


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


@app.get("/auth/google/login")
async def google_login(request: Request):
    if not oauth_google:
        return RedirectResponse("/")
    uri = oauth_callback_url(request)
    try:
        # authorize_redirect lives on the named client (google), not on the OAuth registry.
        return await oauth_google.google.authorize_redirect(request, uri)
    except Exception:
        log.exception("Google OAuth authorize_redirect failed (check GOOGLE_* env, PUBLIC_BASE_URL, outbound HTTPS to Google)")
        return RedirectResponse("/login?error=redirect", status_code=302)


@app.get("/auth/google/callback", name="google_auth_callback")
async def google_callback(request: Request):
    if not oauth_google:
        return RedirectResponse("/")
    try:
        token = await oauth_google.google.authorize_access_token(request)
    except Exception as exc:
        log.warning("Google OAuth error: %s", exc)
        return RedirectResponse("/login?error=oauth")

    access_token = token.get("access_token")
    if not access_token:
        return RedirectResponse("/login?error=token")

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            info = r.json()
    except Exception as exc:
        log.warning("Google userinfo error: %s", exc)
        return RedirectResponse("/login?error=profile")

    if not info.get("email_verified", True):
        return RedirectResponse("/login?error=unverified")

    try:
        session_payload = record_google_login(info)
    except ValueError:
        return RedirectResponse("/login?error=profile")

    request.session[SESSION_USER_KEY] = session_payload
    return RedirectResponse("/", status_code=302)


@app.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not auth_enabled():
        return RedirectResponse("/")
    if request.session.get(SESSION_USER_KEY):
        return RedirectResponse("/")
    html_path = STATIC_DIR / "login.html"
    return HTMLResponse(
        content=html_path.read_text(encoding="utf-8"),
        headers=_HTML_NO_CACHE,
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not auth_enabled():
        return RedirectResponse("/")
    user = request.session.get(SESSION_USER_KEY)
    if not user:
        return RedirectResponse("/login")
    if not is_admin_session(user):
        return HTMLResponse("Forbidden", status_code=403)
    p = STATIC_DIR / "admin.html"
    return HTMLResponse(p.read_text(encoding="utf-8"), headers=_HTML_NO_CACHE)


@app.get("/api/me")
async def me(request: Request):
    if not auth_enabled():
        return {
            "auth_configured": False,
            "authenticated": False,
        }
    user = request.session.get(SESSION_USER_KEY)
    if not user:
        return {
            "auth_configured": True,
            "authenticated": False,
        }
    return {
        "auth_configured": True,
        "authenticated": True,
        "email": user.get("email"),
        "name": user.get("name"),
        "picture": user.get("picture"),
        "is_admin": is_admin_session(user),
    }


@app.get("/api/admin/users")
async def admin_users(request: Request):
    require_admin_api(request)
    return {"users": list_users_for_admin()}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if auth_enabled() and not request.session.get(SESSION_USER_KEY):
        return RedirectResponse("/login")
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(
        content=html_path.read_text(encoding="utf-8"),
        headers=_HTML_NO_CACHE,
    )


@app.get("/api/results")
async def results(
    request: Request,
    market: str = "nifty_500",
    max_stocks: int | None = None,
    _: dict = Depends(_session_dep),
):
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
    __user: dict = Depends(_session_dep),
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
async def quotes(
    request: Request,
    tickers: str = "",
    _: dict = Depends(_session_dep),
):
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
    uvicorn.run(
        app,
        host=WEB_HOST,
        port=WEB_PORT,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
