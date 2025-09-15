# app/services/postgres.py
import os
import psycopg2
from contextlib import contextmanager
from urllib.parse import urlparse

def _normalize_db_url(url: str) -> str:
    # Si ya trae sslmode en la URL, respétalo
    if "sslmode=" in url:
        return url

    # Permite override explícito por ENV (útil para cloud)
    env_mode = os.getenv("DB_SSLMODE")
    if env_mode:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}sslmode={env_mode}"

    # Si el host es local, desactiva SSL por defecto; en remoto, requiérelo
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        mode = "disable" if host in ("localhost", "127.0.0.1") else "require"
    except Exception:
        mode = "require"

    sep = "&" if "?" in url else "?"
    return f"{url}{sep}sslmode={mode}"

@contextmanager
def get_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL no configurada")
    conn = psycopg2.connect(_normalize_db_url(url))
    try:
        yield conn
    finally:
        conn.close()
