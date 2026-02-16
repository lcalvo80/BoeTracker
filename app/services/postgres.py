# app/services/postgres.py
from __future__ import annotations

import os
from contextlib import contextmanager
from urllib.parse import urlparse

import psycopg2
from psycopg2.pool import ThreadedConnectionPool


# Pool de conexiones (por proceso). En producción (Gunicorn) se crea uno por worker.
# Reduce latencia, evita agotar conexiones bajo carga y minimiza jitter.
_POOL: ThreadedConnectionPool | None = None


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


def _get_pool(dsn: str) -> ThreadedConnectionPool:
    """Inicializa (lazy) un pool por proceso."""
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
    """Obtiene una conexión del pool y aplica timeouts 1 sola vez por conexión."""
    pool = _get_pool(dsn)
    conn = pool.getconn()

    # Aplicar timeouts de sesión solo la primera vez que vemos esta conexión.
    # Evita 3 SET por request.
    if not getattr(conn, "_boe_timeouts_applied", False):
        _apply_session_timeouts(conn)
        setattr(conn, "_boe_timeouts_applied", True)

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
        # Fallback ultra defensivo
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

    # Nota: psycopg2 conecta usando libpq. Los parámetros DSN (keepalives, sslmode, etc.)
    # se aplican aquí.
    # Pooling: una conexión por request (checkout/putback), NO un connect/close.
    conn = _checkout_conn(dsn)
    try:
        yield conn
        conn.commit()
    except Exception:
        # Importante: si hay excepción, rollback antes de devolver al pool.
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        _release_conn(conn)
