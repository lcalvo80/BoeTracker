from __future__ import annotations

import os
import psycopg2
from contextlib import contextmanager
from urllib.parse import urlparse

def _append_param(url: str, k: str, v: str) -> str:
    if not v:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{k}={v}"

def _normalize_db_url(url: str) -> str:
    # Si ya trae sslmode en la URL, respétalo; si no, decide por host
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
    ct = os.getenv("DB_CONNECT_TIMEOUT", "10")
    url = _append_param(url, "connect_timeout", ct)

    # application_name
    app_name = os.getenv("DB_APP_NAME", "boe-ingestor")
    url = _append_param(url, "application_name", app_name)

    return url

from contextlib import contextmanager

@contextmanager
def get_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL no configurada")
    dsn = _normalize_db_url(url)
    conn = psycopg2.connect(dsn)
    try:
        yield conn
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
