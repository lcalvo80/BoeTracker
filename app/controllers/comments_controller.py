# app/controllers/comments_controller.py
from app.services.postgres import get_db
from datetime import datetime
from math import ceil

def _ensure_table():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id SERIAL PRIMARY KEY,
                item_identificador TEXT NOT NULL,
                content TEXT NOT NULL,
                user_id TEXT NULL,
                author TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
        """)
        conn.commit()

_ensure_table()

def list_comments_by_item(identificador: str):
    if not identificador:
        return []
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, item_identificador, content, user_id, author, created_at
            FROM comments
            WHERE item_identificador = %s
            ORDER BY created_at DESC, id DESC
        """, (identificador,))
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "identificador": r[1],
                "content": r[2],
                "text": r[2],  # alias para el front
                "user_id": r[3],
                "author": r[4],
                "created_at": r[5].isoformat() if isinstance(r[5], datetime) else r[5],
            }
            for r in rows
        ]

def list_comments_by_item_paginated(identificador: str, page: int, limit: int):
    if not identificador:
        return {"items": [], "page": 1, "pages": 0, "total": 0}
    offset = (page - 1) * limit
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM comments WHERE item_identificador = %s", (identificador,))
        total = cur.fetchone()[0] or 0
        cur.execute("""
            SELECT id, item_identificador, content, user_id, author, created_at
            FROM comments
            WHERE item_identificador = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
        """, (identificador, limit, offset))
        rows = cur.fetchall()
    items = [
        {
            "id": r[0],
            "identificador": r[1],
            "content": r[2],
            "text": r[2],  # alias para el front
            "user_id": r[3],
            "author": r[4],
            "created_at": r[5].isoformat() if isinstance(r[5], datetime) else r[5],
        } for r in rows
    ]
    pages = ceil(total / limit) if limit > 0 else 0
    return {"items": items, "page": page if total else 1, "pages": pages if total else 0, "total": total}

def create_comment(payload: dict):
    identificador = (payload.get("identificador") or "").strip()
    # admitimos text o content
    content = (payload.get("text") or payload.get("content") or "").strip()
    user_id = (payload.get("user_id") or None)
    author = (payload.get("author") or None)

    if not identificador or not content:
        return {"error": "identificador y content/text son obligatorios"}

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO comments (item_identificador, content, user_id, author)
            VALUES (%s, %s, %s, %s)
            RETURNING id, item_identificador, content, user_id, author, created_at
            """,
            (identificador, content, user_id, author),
        )
        r = cur.fetchone()
        conn.commit()
        return {
            "id": r[0],
            "identificador": r[1],
            "content": r[2],
            "text": r[2],  # alias para el front
            "user_id": r[3],
            "author": r[4],
            "created_at": r[5].isoformat() if isinstance(r[5], datetime) else r[5],
        }
