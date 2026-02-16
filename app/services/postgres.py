# app/services/postgres.py
from __future__ import annotations

import os
from contextlib import contextmanager
from urllib.parse import urlparse

from psycopg2.pool import ThreadedConnectionPool

_POOL: ThreadedConnectionPool | None = None

# Tracking de “timeouts aplicados” sin setattr sobre la conexión (objeto C)
_TIMEOUTS_APPLIED: set[int] = set()


def _append_param(url: str, k: str, v: str) -> str:
    if v is None:
        return url
    v = str(v).strip()
    if not v:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{k}={v}"


def _normalize_db_url(url: str) -> str:
    if "sslmode=" not in url:
        env_mode = (os.getenv("DB_SSLMODE") or "").strip()
        if env_mode:
            url = _append_param(url, "sslmode", env_mode)
        else:
            try:
                parsed = urlparse(url)
                host = (parsed.hostname or "").lower()
                mode = "disable" if host in ("localhost", "127.0.0.1") else "require"
            except Exception:
                mode = "require"
            url = _append_param(url, "sslmode", mode)

    url = _append_param(url, "connect_timeout", (os.getenv("DB_CONNECT_TIMEOUT") or "10"))
    url = _append_param(url, "application_name", (os.getenv("DB_APP_NAME") or "boe-api"))

    url = _append_param(url, "keepalives", os.getenv("DB_KEEPALIVES", "1"))
    url = _append_param(url, "keepalives_idle", os.getenv("DB_KEEPALIVES_IDLE", "30"))
    url = _append_param(url, "keepalives_interval", os.getenv("DB_KEEPALIVES_INTERVAL", "10"))
    url = _append_param(url, "keepalives_count", os.getenv("DB_KEEPALIVES_COUNT", "5"))

    return url


def _apply_session_timeouts(conn) -> None:
    statement_ms = int((os.getenv("DB_STATEMENT_TIMEOUT_MS") or "15000").strip() or "15000")
    lock_ms = int((os.getenv("DB_LOCK_TIMEOUT_MS") or "5000").strip() or "5000")
    idle_tx_ms = int((os.getenv("DB_IDLE_TX_TIMEOUT_MS") or "30000").strip() or "30000")

    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = %s;", (statement_ms,))
        cur.execute("SET lock_timeout = %s;", (lock_ms,))
        cur.execute("SET idle_in_transaction_session_timeout = %s;", (idle_tx_ms,))


def _get_pool(dsn: str) -> ThreadedConnectionPool:
    global _POOL
    if _POOL is not None:
        return _POOL

    minconn = int((os.getenv("DB_POOL_MIN") or "1").strip() or "1")
    maxconn = int((os.getenv("DB_POOL_MAX") or "10").strip() or "10")
    if minconn < 1:
        minconn = 1
    if maxconn < minconn:
        maxconn = minconn

    _POOL = ThreadedConnectionPool(minconn=minconn, maxconn=maxconn, dsn=dsn)
    return _POOL


def _checkout_conn(dsn: str):
    pool = _get_pool(dsn)
    conn = pool.getconn()

    cid = id(conn)
    if cid not in _TIMEOUTS_APPLIED:
        _apply_session_timeouts(conn)
        _TIMEOUTS_APPLIED.add(cid)

    return conn


def _release_conn(conn, *, close: bool = False) -> None:
    global _POOL
    if _POOL is None:
        try:
            conn.close()
        except Exception:
            pass
        return

    try:
        _POOL.putconn(conn, close=close)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def get_db():
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL no configurada")

    dsn = _normalize_db_url(url)
    conn = _checkout_conn(dsn)

    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        _release_conn(conn)
