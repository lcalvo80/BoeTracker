# app/services/postgres.py
import os
import psycopg2
from contextlib import contextmanager

def _normalize_db_url(url: str) -> str:
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url

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
