# app/routes/comments.py
from flask import Blueprint, jsonify, request
from datetime import datetime
from app.services.postgres import get_db  # mismo helper que usas en items_controller

bp = Blueprint("comments", __name__)

# --- util: asegurar tabla (simple y seguro en Postgres) ----------------------
def ensure_schema():
    sql = """
    CREATE TABLE IF NOT EXISTS comments (
        id BIGSERIAL PRIMARY KEY,
        item_identificador TEXT NOT NULL,
        user_id TEXT NULL,            -- opcional: id de Clerk si lo guardas
        author TEXT NULL,             -- opcional: nombre para mostrar
        content TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_comments_item_identificador
      ON comments(item_identificador);
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

# se ejecuta una vez por proceso
ensure_schema()

# ---------------------------------------------------------------------------
# GET /api/comments/<item_identificador>
# Devuelve los comentarios de un item (ordenados por fecha desc)
# ---------------------------------------------------------------------------
@bp.get("/comments/<item_identificador>")
def get_comments(item_identificador):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, item_identificador, user_id, author, content, created_at
                FROM comments
                WHERE item_identificador = %s
                ORDER BY created_at DESC, id DESC
                """,
                (item_identificador,),
            )
            rows = cur.fetchall()

    data = [
        {
            "id": r[0],
            "item_identificador": r[1],
            "user_id": r[2],
            "author": r[3],
            "content": r[4],
            "created_at": (r[5].isoformat() if isinstance(r[5], datetime) else r[5]),
        }
        for r in rows
    ]
    return jsonify(data)

# ---------------------------------------------------------------------------
# POST /api/comments
# Body JSON esperado: { "identificador": "...", "content": "...", "user_id"?: "...", "author"?: "..." }
# ---------------------------------------------------------------------------
@bp.post("/comments")
def post_comment():
    data = request.get_json(silent=True) or {}
    identificador = (data.get("identificador") or data.get("item_identificador") or "").strip()
    content = (data.get("content") or "").strip()
    user_id = (data.get("user_id") or "").strip() or None
    author = (data.get("author") or "").strip() or None

    if not identificador or not content:
        return jsonify({"error": "identificador y content son obligatorios"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO comments (item_identificador, user_id, author, content)
                VALUES (%s, %s, %s, %s)
                RETURNING id, item_identificador, user_id, author, content, created_at
                """,
                (identificador, user_id, author, content),
            )
            r = cur.fetchone()
        conn.commit()

    created = {
        "id": r[0],
        "item_identificador": r[1],
        "user_id": r[2],
        "author": r[3],
        "content": r[4],
        "created_at": (r[5].isoformat() if isinstance(r[5], datetime) else r[5]),
    }
    return jsonify(created), 201
