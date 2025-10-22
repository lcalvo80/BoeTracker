# app/services/comments_svc.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from psycopg2 import sql
from app.services.postgres import get_db

# ───────────────────────── Helpers de introspección ─────────────────────────

def _table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='public' AND table_name=%s LIMIT 1
        """, (table,))
        return cur.fetchone() is not None

def _column_exists(conn, table: str, col: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s AND column_name=%s
            LIMIT 1
        """, (table, col))
        return cur.fetchone() is not None

# ───────────────────────── DDL ─────────────────────────

def _ensure_table() -> None:
    """
    Crea comments si no existe (nuevo esquema: content/author).
    Si ya existe con columnas legacy (comment/user_name), NO toca estructura.
    """
    with get_db() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id SERIAL PRIMARY KEY,
                item_identificador TEXT NOT NULL,
                content TEXT NOT NULL,
                user_id TEXT NULL,
                author TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        conn.commit()

# ───────────────────────── Normalización I/O ─────────────────────────

def _row_to_payload(row: tuple, cols: List[str]) -> Dict[str, Any]:
    rec = {cols[i]: row[i] for i in range(len(cols))}
    content = rec.get("content")
    author  = rec.get("author")
    # Si viene de esquema legacy:
    if content is None and "comment" in rec:
        content = rec.get("comment")
    if author is None and "user_name" in rec:
        author = rec.get("user_name")

    created = rec.get("created_at")
    if isinstance(created, datetime):
        created = created.isoformat()

    out = {
        "id": rec.get("id"),
        "identificador": rec.get("item_identificador"),
        "item_identificador": rec.get("item_identificador"),
        "content": content,
        "text": content,            # alias FE
        "author": author,
        "user_name": author,        # alias legacy FE
        "user_id": rec.get("user_id"),
        "created_at": created,
    }
    return out

# ───────────────────────── API de servicio ─────────────────────────

def list_by_item_paginated(identificador: str, page: int = 1, limit: int = 20) -> Dict[str, Any]:
    if not identificador:
        return {"items": [], "total": 0, "page": 1, "pages": 0, "limit": limit}

    _ensure_table()
    offset = max(0, (max(1, page) - 1) * max(1, limit))

    with get_db() as conn, conn.cursor() as cur:
        # Detecta columnas disponibles
        has_content  = _column_exists(conn, "comments", "content")
        has_comment  = _column_exists(conn, "comments", "comment")
        has_author   = _column_exists(conn, "comments", "author")
        has_username = _column_exists(conn, "comments", "user_name")
        has_userid   = _column_exists(conn, "comments", "user_id")

        cur.execute("SELECT COUNT(*) FROM comments WHERE item_identificador = %s", (identificador,))
        total = int(cur.fetchone()[0] or 0)

        # SELECT dinámico
        cols_sql = ["id", "item_identificador", "created_at"]
        if has_userid:   cols_sql.append("user_id")
        if has_content:  cols_sql.append("content")
        if has_comment:  cols_sql.append("comment")
        if has_author:   cols_sql.append("author")
        if has_username: cols_sql.append("user_name")

        q = sql.SQL("""
            SELECT {cols}
            FROM comments
            WHERE item_identificador = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
        """).format(cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols_sql))

        cur.execute(q, (identificador, limit, offset))
        rows = cur.fetchall()
        items = [_row_to_payload(r, cols_sql) for r in rows]

    pages = (total + limit - 1) // limit if limit else 0
    return {"items": items, "total": total, "page": page if total else 1, "pages": pages if total else 0, "limit": limit}

def create(identificador: str, *, content: str, author: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    if not identificador or not (content or "").strip():
        raise ValueError("identificador y content son obligatorios")

    _ensure_table()

    with get_db() as conn, conn.cursor() as cur:
        # Detecta columnas para INSERT
        has_content  = _column_exists(conn, "comments", "content")
        has_comment  = _column_exists(conn, "comments", "comment")
        has_author   = _column_exists(conn, "comments", "author")
        has_username = _column_exists(conn, "comments", "user_name")
        has_userid   = _column_exists(conn, "comments", "user_id")

        # Campos y valores
        cols = ["item_identificador"]
        vals = [identificador]

        if has_content:
            cols.append("content"); vals.append(content)
        elif has_comment:
            cols.append("comment"); vals.append(content)
        else:
            # Si no hay ni content ni comment, algo raro con la tabla
            raise RuntimeError("comments table missing content/comment column")

        if has_userid and user_id is not None:
            cols.append("user_id"); vals.append(user_id)

        if author is not None:
            if has_author:
                cols.append("author"); vals.append(author)
            elif has_username:
                cols.append("user_name"); vals.append(author)

        cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
        ph_sql   = sql.SQL(", ").join(sql.Placeholder() * len(cols))

        q = sql.SQL("INSERT INTO comments ({cols}) VALUES ({ph}) RETURNING id, item_identificador, created_at").format(
            cols=cols_sql, ph=ph_sql
        )
        cur.execute(q, vals)
        row = cur.fetchone()
        conn.commit()

        # Devuelve payload normalizado (completamos author/content con lo que mandamos)
        base_cols = ["id", "item_identificador", "created_at"]
        rec = {base_cols[i]: row[i] for i in range(len(base_cols))}
        out = {
            "id": rec["id"],
            "identificador": rec["item_identificador"],
            "item_identificador": rec["item_identificador"],
            "content": content,
            "text": content,
            "author": author,
            "user_name": author,
            "user_id": user_id,
            "created_at": rec["created_at"].isoformat() if isinstance(rec["created_at"], datetime) else rec["created_at"],
        }
        return out

# ───────────────────────── Aliases (compat con controller viejo) ─────────────────────────

def list_comments_by_item(identificador: str) -> List[Dict[str, Any]]:
    res = list_by_item_paginated(identificador, page=1, limit=10_000)
    return res["items"]

def list_comments_by_item_paginated(identificador: str, page: int, limit: int):
    return list_by_item_paginated(identificador, page, limit)

def create_comment(payload: dict):
    identificador = (payload.get("identificador") or "").strip()
    content = (payload.get("text") or payload.get("content") or "").strip()
    author = payload.get("author")
    user_id = payload.get("user_id")
    if not identificador or not content:
        return {"error": "identificador y content/text son obligatorios"}
    return create(identificador, content=content, author=author, user_id=user_id)
