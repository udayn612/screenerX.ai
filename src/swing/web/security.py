"""Google OAuth helpers, session user shape, and SQLite user log."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request

from swing.config import (
    ADMIN_EMAILS,
    AUTH_DB_PATH,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    PUBLIC_BASE_URL,
)

SESSION_USER_KEY = "user"


def auth_enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def init_auth_db() -> None:
    if not auth_enabled():
        return
    AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUTH_DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oauth_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                google_sub TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL,
                name TEXT,
                picture TEXT,
                first_login_utc TEXT NOT NULL,
                last_login_utc TEXT NOT NULL,
                login_count INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_oauth_users_email ON oauth_users(email)"
        )
        conn.commit()
    finally:
        conn.close()


def record_google_login(claims: dict[str, Any]) -> dict[str, Any]:
    """Upsert user row; return session payload."""
    sub = str(claims.get("sub") or "")
    email = (claims.get("email") or "").strip().lower()
    name = str(claims.get("name") or "")
    picture = str(claims.get("picture") or "")
    now = _utc_now_iso()
    if not sub or not email:
        raise ValueError("Google profile missing sub or email")

    conn = sqlite3.connect(AUTH_DB_PATH)
    try:
        row = conn.execute(
            "SELECT id, login_count FROM oauth_users WHERE google_sub = ?",
            (sub,),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO oauth_users (
                    google_sub, email, name, picture,
                    first_login_utc, last_login_utc, login_count
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (sub, email, name, picture, now, now),
            )
            uid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            login_count = 1
        else:
            uid, prev_count = int(row[0]), int(row[1])
            login_count = prev_count + 1
            conn.execute(
                """
                UPDATE oauth_users
                SET email = ?, name = ?, picture = ?,
                    last_login_utc = ?, login_count = ?
                WHERE google_sub = ?
                """,
                (email, name, picture, now, login_count, sub),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": uid,
        "sub": sub,
        "email": email,
        "name": name,
        "picture": picture,
        "is_admin": email in ADMIN_EMAILS,
    }


def list_users_for_admin() -> list[dict[str, Any]]:
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, google_sub, email, name, picture,
                   first_login_utc, last_login_utc, login_count
            FROM oauth_users
            ORDER BY last_login_utc DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def is_admin_session(user: dict[str, Any]) -> bool:
    email = (user.get("email") or "").strip().lower()
    return email in ADMIN_EMAILS


def create_oauth() -> OAuth | None:
    if not auth_enabled():
        return None
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


def oauth_callback_url(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/auth/google/callback"
    return str(request.url_for("google_auth_callback"))


def require_api_user(request: Request) -> dict[str, Any]:
    if not auth_enabled():
        return {}
    user = request.session.get(SESSION_USER_KEY)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin_api(request: Request) -> dict[str, Any]:
    user = require_api_user(request)
    if not is_admin_session(user):
        raise HTTPException(status_code=403, detail="Admin only")
    return user
