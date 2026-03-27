"""SQLite caching layer for OHLCV data and scan results."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from swing.config import CACHE_TTL_SECONDS, DB_PATH
from swing.utils.logger import get_logger

log = get_logger(__name__)


def _table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({name})").fetchall()}


def _ensure_ohlcv_cache(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ohlcv_cache'"
    ).fetchone()
    if row is None:
        conn.execute("""
            CREATE TABLE ohlcv_cache (
                ticker TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
        """)
        return
    cols = _table_columns(conn, "ohlcv_cache")
    if cols >= {"ticker", "fetched_at", "data_json"} and "fetch_date" not in cols:
        return
    conn.execute("DROP TABLE ohlcv_cache")
    conn.execute("""
        CREATE TABLE ohlcv_cache (
            ticker TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """)


def _ensure_web_scan_cache(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='web_scan_cache'"
    ).fetchone()
    if row is None:
        conn.execute("""
            CREATE TABLE web_scan_cache (
                market TEXT NOT NULL,
                max_stocks INTEGER NOT NULL,
                results_json TEXT NOT NULL,
                scanned_at TEXT NOT NULL,
                PRIMARY KEY (market, max_stocks)
            )
        """)
        return
    cols = _table_columns(conn, "web_scan_cache")
    if "scan_date" not in cols and cols >= {"market", "max_stocks", "results_json", "scanned_at"}:
        return
    conn.execute("DROP TABLE web_scan_cache")
    conn.execute("""
        CREATE TABLE web_scan_cache (
            market TEXT NOT NULL,
            max_stocks INTEGER NOT NULL,
            results_json TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            PRIMARY KEY (market, max_stocks)
        )
    """)


def _utc_from_iso(ts: str) -> datetime:
    s = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_fresh(stored_at: str, ttl_seconds: int = CACHE_TTL_SECONDS) -> bool:
    try:
        age = (datetime.now(timezone.utc) - _utc_from_iso(stored_at)).total_seconds()
        return age <= ttl_seconds
    except Exception:
        return False


def _get_conn() -> sqlite3.Connection:
    """Get a connection to the cache database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    _ensure_ohlcv_cache(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_results (
            scan_date TEXT NOT NULL,
            scope INTEGER NOT NULL,
            results_json TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            PRIMARY KEY (scan_date, scope)
        )
    """)
    _ensure_web_scan_cache(conn)
    conn.commit()
    return conn


def get_cached_data(ticker: str) -> pd.DataFrame | None:
    """Return cached OHLCV if stored within CACHE_TTL_SECONDS, else None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT data_json, fetched_at FROM ohlcv_cache WHERE ticker = ?",
            (ticker,),
        ).fetchone()
        if row is None or not _is_fresh(row[1]):
            return None
        df = pd.read_json(row[0], orient="split")
        return df
    except Exception:
        return None
    finally:
        conn.close()


def save_to_cache(ticker: str, df: pd.DataFrame) -> None:
    """Save OHLCV DataFrame with current UTC timestamp."""
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    conn = _get_conn()
    try:
        data_json = df.to_json(orient="split", date_format="iso")
        conn.execute(
            """INSERT OR REPLACE INTO ohlcv_cache (ticker, data_json, fetched_at)
               VALUES (?, ?, ?)""",
            (ticker, data_json, fetched_at),
        )
        conn.commit()
    except Exception as exc:
        log.warning("Failed to cache data for %s: %s", ticker, exc)
    finally:
        conn.close()


def clear_old_cache(keep_days: int = 3) -> None:
    """Remove stale rows (OHLCV / web scan by timestamp; legacy tables by date)."""
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat().replace(
        "+00:00", "Z"
    )
    cutoff_day = (date.today() - timedelta(days=keep_days)).isoformat()
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM ohlcv_cache WHERE fetched_at < ?", (cutoff_ts,))
        conn.execute("DELETE FROM web_scan_cache WHERE scanned_at < ?", (cutoff_ts,))
        conn.execute("DELETE FROM scan_results WHERE scan_date < ?", (cutoff_day,))
        conn.commit()
    except Exception as exc:
        log.warning("Failed to clear old cache: %s", exc)
    finally:
        conn.close()


# ── Scan Results Cache ──

def get_cached_results(scope: int) -> dict | None:
    """Return cached scan results for today and given scope, or None."""
    today = date.today().isoformat()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT results_json, scanned_at FROM scan_results "
            "WHERE scan_date = ? AND scope = ?",
            (today, scope),
        ).fetchone()
        if row is None:
            return None
        results = json.loads(row[0])
        results["scanned_at"] = row[1]
        results["cached"] = True
        return results
    except Exception:
        return None
    finally:
        conn.close()


def max_stocks_cache_key(max_stocks: int | None) -> int:
    """SQLite row key: -1 means full universe (no max_stocks cap)."""
    return max_stocks if max_stocks is not None else -1


def get_web_scan_cache(market: str, max_stocks_key: int) -> dict | None:
    """Return cached /api/scan JSON if younger than CACHE_TTL_SECONDS, else None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT results_json, scanned_at FROM web_scan_cache "
            "WHERE market = ? AND max_stocks = ?",
            (market, max_stocks_key),
        ).fetchone()
        if row is None or not _is_fresh(row[1]):
            return None
        data = json.loads(row[0])
        data["scanned_at"] = row[1]
        data["cached"] = True
        return data
    except Exception:
        return None
    finally:
        conn.close()


def save_web_scan_cache(market: str, max_stocks_key: int, payload: dict, scanned_at: str) -> None:
    """Persist a successful scan for reuse within CACHE_TTL_SECONDS."""
    to_store = {k: v for k, v in payload.items() if k not in ("cached", "scanned_at")}
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO web_scan_cache
               (market, max_stocks, results_json, scanned_at)
               VALUES (?, ?, ?, ?)""",
            (market, max_stocks_key, json.dumps(to_store), scanned_at),
        )
        conn.commit()
    except Exception as exc:
        log.warning("Failed to cache web scan for %s: %s", market, exc)
    finally:
        conn.close()


def save_scan_results(scope: int, results: dict) -> str:
    """Save scan results to cache. Returns the scanned_at timestamp."""
    today = date.today().isoformat()
    scanned_at = datetime.utcnow().isoformat() + "Z"
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO scan_results
               (scan_date, scope, results_json, scanned_at)
               VALUES (?, ?, ?, ?)""",
            (today, scope, json.dumps(results), scanned_at),
        )
        conn.commit()
    except Exception as exc:
        log.warning("Failed to cache scan results: %s", exc)
    finally:
        conn.close()
    return scanned_at
