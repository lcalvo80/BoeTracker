# app/services/postgres.py
from __future__ import annotations

import os
from contextlib import contextmanager
from urllib.parse import urlparse

import psycopg2


def _append_param(url: str, k: str, v: str) -> str:
    if not v:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{k}={v}"


def _normalize_db_url(url: str) -> str:
    # sslmode
    if "sslmode=" not in url:
        env_mode = os.getenv("DB_SSLMODE")
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

    # connect_timeout (s)
    ct = os.getenv("DB_CONNECT_TIMEOUT", "10").strip() or "10"
    url = _append_param(url, "connect_timeout", ct)

    # application_name
    app_name = (os.getenv("DB_APP_NAME", "boe-api") or "boe-api").strip()
    url = _append_param(url, "application_name", app_name)

    return url


def _apply_session_timeouts(conn) -> None:
    """
    Evita 'pending' indefinidos: corta queries lentas/bloqueos en DB.
    Valores por defecto razonables para API:
      - statement_timeout: 15000ms
      - lock_timeout: 5000ms
      - idle_in_transaction_session_timeout: 30000ms
    """
    statement_ms = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "15000") or "15000")
    lock_ms = int(os.getenv("DB_LOCK_TIMEOUT_MS", "5000") or "5000")
    idle_tx_ms = int(os.getenv("DB_IDLE_TX_TIMEOUT_MS", "30000") or "30000")

    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = %s;", (statement_ms,))
        cur.execute("SET lock_timeout = %s;", (lock_ms,))
        cur.execute("SET idle_in_transaction_session_timeout = %s;", (idle_tx_ms,))


@contextmanager
def get_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL no configurada")

    dsn = _normalize_db_url(url)
    conn = psycopg2.connect(dsn)
    try:
        _apply_session_timeouts(conn)
        yield conn
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
