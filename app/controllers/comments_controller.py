# app/controllers/comments_controller.py
from app.services.postgres import get_db
from datetime import datetime

# Crea la tabla si no existe (id, item_identificador, content, user_id, author, created_at)
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
                "user_id": r[3],
                "author": r[4],
                "created_at": r[5].isoformat() if isinstance(r[5], datetime) else r[5],
            }
            for r in rows
        ]

def create_comment(payload: dict):
    identificador = (payload.get("identificador") or "").strip()
    content = (payload.get("content") or "").strip()
    user_id = (payload.get("user_id") or None)
    author = (payload.get("author") or None)

    if not identificador or not content:
        # respuesta coherente con API simple
        return {"error": "identificador y content son obligatorios"}

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
            "user_id": r[3],
            "author": r[4],
            "created_at": r[5].isoformat() if isinstance(r[5], datetime) else r[5],
        }
