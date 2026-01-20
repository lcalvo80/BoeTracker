# app/services/postgres.py
from __future__ import annotations

import os
from contextlib import contextmanager
from urllib.parse import urlparse

import psycopg2


def _append_param(url: str, k: str, v: str) -> str:
    if v is None:
        return url
    v = str(v).strip()
    if not v:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{k}={v}"


def _normalize_db_url(url: str) -> str:
    """
    Normaliza la DATABASE_URL para libpq (psycopg2) añadiendo parámetros útiles:
    - sslmode (require fuera de local)
    - connect_timeout
    - application_name
    - TCP keepalives (reduce "SSL connection closed unexpectedly" en redes/NAT)
    """
    # sslmode
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

    # connect_timeout (seconds)
    ct = (os.getenv("DB_CONNECT_TIMEOUT") or "10").strip() or "10"
    url = _append_param(url, "connect_timeout", ct)

    # application_name (útil para distinguir API vs scripts)
    app_name = (os.getenv("DB_APP_NAME") or "boe-api").strip() or "boe-api"
    url = _append_param(url, "application_name", app_name)

    # TCP keepalives (libpq)
    # Defaults conservadores: empiezan a "pulsar" tras 30s de idle y reintentan.
    url = _append_param(url, "keepalives", os.getenv("DB_KEEPALIVES", "1"))
    url = _append_param(url, "keepalives_idle", os.getenv("DB_KEEPALIVES_IDLE", "30"))
    url = _append_param(url, "keepalives_interval", os.getenv("DB_KEEPALIVES_INTERVAL", "10"))
    url = _append_param(url, "keepalives_count", os.getenv("DB_KEEPALIVES_COUNT", "5"))

    return url


def _apply_session_timeouts(conn) -> None:
    """
    Timeouts de sesión para evitar queries colgadas indefinidamente.
    Defaults razonables para API, pero totalmente overrideable por env
    (para scripts/batch suele interesar subirlos).

    Variables:
      - DB_STATEMENT_TIMEOUT_MS (default 15000)
      - DB_LOCK_TIMEOUT_MS (default 5000)
      - DB_IDLE_TX_TIMEOUT_MS (default 30000)
    """
    statement_ms = int((os.getenv("DB_STATEMENT_TIMEOUT_MS") or "15000").strip() or "15000")
    lock_ms = int((os.getenv("DB_LOCK_TIMEOUT_MS") or "5000").strip() or "5000")
    idle_tx_ms = int((os.getenv("DB_IDLE_TX_TIMEOUT_MS") or "30000").strip() or "30000")

    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = %s;", (statement_ms,))
        cur.execute("SET lock_timeout = %s;", (lock_ms,))
        cur.execute("SET idle_in_transaction_session_timeout = %s;", (idle_tx_ms,))


@contextmanager
def get_db():
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL no configurada")

    dsn = _normalize_db_url(url)

    # Nota: psycopg2 conecta usando libpq. Los parámetros de DSN (keepalives, sslmode, etc.)
    # se aplican aquí.
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
