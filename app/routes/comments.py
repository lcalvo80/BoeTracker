# app/blueprints/comments.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import psycopg2
from psycopg2.pool import SimpleConnectionPool
from flask import Blueprint, jsonify, request, current_app

bp = Blueprint("comments", __name__, url_prefix="/api")

# ───────────────────────── DB pool ─────────────────────────

_POOL: Optional[SimpleConnectionPool] = None


def _db_dsn_from_env() -> str:
    """
    Usa DATABASE_URL si existe (estilo Railway/Heroku).
    Si no, compone DSN con PG* env vars.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        # Psycopg2 entiende el URI nativo de PostgreSQL (postgres:// o postgresql://)
        # Railway a veces necesita sslmode=require; si ya viene en la URL, se respeta.
        return url

    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    db   = os.getenv("PGDATABASE", "postgres")
    user = os.getenv("PGUSER", "postgres")
    pwd  = os.getenv("PGPASSWORD", "")

    # Fuerza sslmode=require en producción si no estás en localhost
    sslmode = os.getenv("PGSSLMODE")
    if not sslmode and host not in ("localhost", "127.0.0.1"):
        sslmode = "require"

    parts = [f"host={host}", f"port={port}", f"dbname={db}", f"user={user}"]
    if pwd:
        parts.append(f"password={pwd}")
    if sslmode:
        parts.append(f"sslmode={sslmode}")
    return " ".join(parts)


def _ensure_pool() -> SimpleConnectionPool:
    global _POOL
    if _POOL is None:
        dsn = _db_dsn_from_env()
        minconn = int(os.getenv("PGPOOL_MIN", "1"))
        maxconn = int(os.getenv("PGPOOL_MAX", "5"))
        _POOL = SimpleConnectionPool(minconn=minconn, maxconn=maxconn, dsn=dsn)
        _maybe_create_table()
        current_app.logger.info("[comments] PostgreSQL pool initialized")
    return _POOL


def _get_conn():
    return _ensure_pool().getconn()


def _put_conn(conn):
    try:
        _ensure_pool().putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def _maybe_create_table():
    """
    Crea la tabla si no existe. Campos mínimos compat:
      - id (PK), item_identificador, user_name, comment, created_at (UTC default now()).
    """
    sql = """
    CREATE TABLE IF NOT EXISTS comments (
        id                SERIAL PRIMARY KEY,
        item_identificador TEXT NOT NULL,
        user_name          TEXT NOT NULL DEFAULT 'Anónimo',
        comment            TEXT NOT NULL,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql)
    finally:
        _put_conn(conn)

# ───────────────────────── Helpers ─────────────────────────

def _clean_author(v: Any) -> str:
    name = (v or "").strip()
    # Valor por defecto para evitar NOT NULL (según logs en Railway)
    return name or "Anónimo"

def _extract_text(body: Dict[str, Any]) -> str:
    """
    Soporta claves: content | comment | text
    """
    for k in ("content", "comment", "text"):
        val = body.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""

def _pagination() -> Tuple[int, int]:
    try:
        page = max(1, int(request.args.get("page", "1")))
    except Exception:
        page = 1
    try:
        limit = max(1, min(100, int(request.args.get("limit", "20"))))
    except Exception:
        limit = 20
    return page, limit


def _row_to_dict(row, cols):
    return {cols[i]: row[i] for i in range(len(cols))}

# ───────────────────────── Endpoints ─────────────────────────

@bp.get("/items/<ident>/comments")
def list_item_comments(ident: str):
    """
    GET /api/items/:ident/comments
    Respuesta: { items, total, page, pages, limit }
    """
    item_ident = (ident or "").strip()
    if not item_ident:
        return jsonify(detail="ident is required"), 400

    page, limit = _pagination()
    offset = (page - 1) * limit

    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM comments WHERE item_identificador = %s", (item_ident,))
                total = int(cur.fetchone()[0] or 0)

                cur.execute(
                    """
                    SELECT id, item_identificador, user_name, comment, created_at
                    FROM comments
                    WHERE item_identificador = %s
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (item_ident, limit, offset),
                )
                rows = cur.fetchall()

        cols = ["id", "item_identificador", "user_name", "comment", "created_at"]
        items = []
        for r in rows:
            rec = _row_to_dict(r, cols)
            # Compatibilidad con FE: duplicamos campos
            items.append({
                "id": rec["id"],
                "identificador": rec["item_identificador"],
                "item_identificador": rec["item_identificador"],
                "author": rec["user_name"],
                "user_name": rec["user_name"],
                "content": rec["comment"],
                "text": rec["comment"],
                "created_at": rec["created_at"].isoformat() if hasattr(rec["created_at"], "isoformat") else rec["created_at"],
            })

        pages = (total + limit - 1) // limit if limit else 1
        return jsonify({
            "items": items,
            "total": total,
            "page": page,
            "pages": pages,
            "limit": limit,
        }), 200

    finally:
        _put_conn(conn)


@bp.post("/items/<ident>/comments")
def add_item_comment(ident: str):
    """
    POST /api/items/:ident/comments
    Body (cualquiera de estas claves):
      {
        "author": "Nombre opcional",
        "user_name": "Nombre opcional",
        "content": "Texto del comentario"   // o "comment" o "text"
      }
    Respuesta 201:
      {
        id, item_identificador, identificador, author, user_name,
        content, text, created_at
      }
    """
    item_ident = (ident or "").strip()
    if not item_ident:
        return jsonify(detail="ident is required"), 400

    body = request.get_json(silent=True) or {}
    author = _clean_author(body.get("author") or body.get("user_name"))
    text = _extract_text(body)

    if not text:
        return jsonify(detail="comment text is required (content/comment/text)"), 400

    sql = """
        INSERT INTO comments (item_identificador, user_name, comment)
        VALUES (%s, %s, %s)
        RETURNING id, item_identificador, user_name, comment, created_at
    """
    params = (item_ident, author, text)

    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
        if not row:
            return jsonify(detail="insert failed"), 500

        cols = ["id", "item_identificador", "user_name", "comment", "created_at"]
        rec = _row_to_dict(row, cols)

        payload = {
            "id": rec["id"],
            "item_identificador": rec["item_identificador"],
            "identificador": rec["item_identificador"],
            "author": rec["user_name"],
            "user_name": rec["user_name"],
            "content": rec["comment"],
            "text": rec["comment"],
            "created_at": rec["created_at"].isoformat() if hasattr(rec["created_at"], "isoformat") else rec["created_at"],
        }
        return jsonify(payload), 201
    except Exception:
        current_app.logger.exception("add_item_comment failed")
        # devolvemos error genérico para no filtrar detalles de DB
        return jsonify(detail="failed to insert comment"), 500
    finally:
        _put_conn(conn)
