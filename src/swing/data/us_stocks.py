"""Fetch stock lists for US market indices (S&P 500, Dow 30, Nasdaq 100)."""

from __future__ import annotations

from io import StringIO
from urllib.parse import quote

import httpx
import pandas as pd

from swing.config import NASDAQ100_FALLBACK_TICKERS
from swing.utils.logger import get_logger

log = get_logger(__name__)

_WIKI_REST_HTML = "https://en.wikipedia.org/api/rest_v1/page/html/"

# Wikipedia often returns 403 for non-browser User-Agents; mirrors IAB / browser expectations.
# Wikimedia requires a descriptive User-Agent with contact context (reduces 403).
_BROWSER_HEADERS = {
    "User-Agent": (
        "ScreenerX.ai/1.0 (Python httpx; index constituent table fetch)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_SP500_CSV_FALLBACK = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
    "master/data/constituents.csv"
)


def _fetch_wiki_tables(page_title: str) -> list[pd.DataFrame]:
    """Fetch article HTML via MediaWiki REST API (more reliable than /wiki/ URLs) and parse tables."""
    slug = page_title.replace(" ", "_")
    encoded = quote(slug, safe="")
    url = f"{_WIKI_REST_HTML}{encoded}"
    resp = httpx.get(url, headers=_BROWSER_HEADERS, follow_redirects=True, timeout=25)
    resp.raise_for_status()
    return pd.read_html(StringIO(resp.text))


def _normalize_wiki_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [
            " ".join(str(x) for x in tup if str(x) not in ("nan", "NaN")).strip()
            for tup in df.columns.values
        ]
    return df


def _load_nasdaq100_from_fallback_file() -> list[dict]:
    """Load tickers from bundled data/nasdaq100_tickers.txt (one symbol per line)."""
    path = NASDAQ100_FALLBACK_TICKERS
    if not path.is_file():
        log.error("Nasdaq 100 fallback file missing: %s", path)
        return []
    text = path.read_text(encoding="utf-8")
    stocks = []
    for line in text.splitlines():
        sym = line.strip()
        if not sym or sym.startswith("#"):
            continue
        stocks.append({
            "symbol": sym,
            "company": sym,
            "industry": "",
            "yf_ticker": sym,
        })
    log.info("Loaded %d Nasdaq 100 tickers from fallback file", len(stocks))
    return stocks


def _pick_nasdaq100_constituents_table(tables: list[pd.DataFrame]) -> pd.DataFrame | None:
    """Choose the main components table (avoids small 'Ticker | Security' history tables)."""
    best: pd.DataFrame | None = None
    best_rows = 0
    for raw in tables:
        df = _normalize_wiki_columns(raw)
        if "Ticker" not in df.columns:
            continue
        name_col = "Company" if "Company" in df.columns else ("Security" if "Security" in df.columns else None)
        if name_col is None:
            continue
        n = len(df)
        if n < 50:
            continue
        if n > best_rows:
            best_rows = n
            best = df
    return best


def _safe_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Find first matching column name from candidates."""
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _sp500_rows_from_df(df: pd.DataFrame, source: str) -> list[dict]:
    sym_col = _safe_col(df, ["Symbol", "Ticker symbol", "Ticker"])
    name_col = _safe_col(df, ["Security", "Company", "Name"])
    sector_col = _safe_col(df, ["GICS Sector", "Sector", "Industry", "ICB Industry"])

    if not sym_col or not name_col:
        log.error("S&P 500: unexpected columns from %s: %s", source, list(df.columns))
        return []

    stocks = []
    for _, row in df.iterrows():
        symbol = str(row[sym_col]).strip().replace(".", "-")
        stocks.append({
            "symbol": symbol,
            "company": str(row[name_col]).strip(),
            "industry": str(row.get(sector_col, "")).strip() if sector_col else "",
            "yf_ticker": symbol,
        })
    log.info("Loaded %d S&P 500 stocks from %s", len(stocks), source)
    return stocks


def _get_sp500_from_csv_fallback() -> list[dict]:
    try:
        resp = httpx.get(_SP500_CSV_FALLBACK, headers=_BROWSER_HEADERS, follow_redirects=True, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text))
        return _sp500_rows_from_df(df, "GitHub constituents.csv")
    except Exception as exc:
        log.error("S&P 500 CSV fallback failed: %s", exc)
        return []


def get_sp500_stocks() -> list[dict]:
    """Get S&P 500 stocks from Wikipedia, or fall back to a public constituents CSV."""
    try:
        tables = _fetch_wiki_tables("List of S&P 500 companies")
        df = tables[0]
        stocks = _sp500_rows_from_df(df, "Wikipedia")
        if stocks:
            return stocks
    except Exception as exc:
        log.warning("Wikipedia S&P 500 failed (%s); trying CSV fallback", exc)

    return _get_sp500_from_csv_fallback()


def get_dow30_stocks() -> list[dict]:
    """Get Dow Jones 30 stocks from Wikipedia."""
    try:
        tables = _fetch_wiki_tables("Dow Jones Industrial Average")

        df = None
        for table in tables:
            if "Symbol" in table.columns:
                df = table
                break

        if df is None:
            log.error("Could not find Dow 30 components table")
            return []

        sym_col = _safe_col(df, ["Symbol", "Ticker"])
        name_col = _safe_col(df, ["Company"])
        sector_col = _safe_col(df, ["Industry", "Sector"])

        if not sym_col or not name_col:
            log.error("Dow 30 table: unexpected columns: %s", list(df.columns))
            return []

        stocks = []
        for _, row in df.iterrows():
            symbol = str(row[sym_col]).strip()
            stocks.append({
                "symbol": symbol,
                "company": str(row[name_col]).strip(),
                "industry": str(row.get(sector_col, "")).strip() if sector_col else "",
                "yf_ticker": symbol,
            })

        log.info("Loaded %d Dow Jones stocks from Wikipedia", len(stocks))
        return stocks
    except Exception as exc:
        log.error("Failed to fetch Dow 30 list: %s", exc)
        return []


def get_nasdaq100_stocks() -> list[dict]:
    """Get Nasdaq 100 stocks from Wikipedia."""
    try:
        tables = _fetch_wiki_tables("Nasdaq-100")
        df = _pick_nasdaq100_constituents_table(tables)

        if df is None:
            log.warning("Could not find Nasdaq 100 components table; using bundled ticker list")
            return _load_nasdaq100_from_fallback_file()

        sym_col = _safe_col(df, ["Ticker", "Symbol"])
        name_col = _safe_col(df, ["Company", "Security"])
        sector_col = _safe_col(df, ["GICS Sector", "Sector", "Industry", "ICB Industry"])

        if not sym_col or not name_col:
            log.warning("Nasdaq 100: unexpected columns %s; using bundled ticker list", list(df.columns))
            return _load_nasdaq100_from_fallback_file()

        stocks = []
        for _, row in df.iterrows():
            symbol = str(row[sym_col]).strip()
            stocks.append({
                "symbol": symbol,
                "company": str(row[name_col]).strip(),
                "industry": str(row.get(sector_col, "")).strip() if sector_col else "",
                "yf_ticker": symbol,
            })

        if not stocks:
            return _load_nasdaq100_from_fallback_file()

        log.info("Loaded %d Nasdaq 100 stocks from Wikipedia", len(stocks))
        return stocks
    except Exception as exc:
        log.warning("Nasdaq 100 Wikipedia fetch failed (%s); using bundled ticker list", exc)

    return _load_nasdaq100_from_fallback_file()
