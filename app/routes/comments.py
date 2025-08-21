# app/routes/comments.py
from flask import Blueprint, jsonify, request, current_app
from app.services.postgres import get_db
from math import ceil
from datetime import datetime

bp = Blueprint("comments", __name__)

# --- bootstrap + migración suave del esquema ---
def _col_exists(conn, table, col) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s AND column_name=%s
            LIMIT 1
        """, (table, col))
        return cur.fetchone() is not None

def _ensure_table():
    with get_db() as conn:
        with conn.cursor() as cur:
            # crea si no existe
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
            # --- migraciones suaves ---
            # 1) si existe 'comentario' y NO existe 'content' -> renombrar
            has_content   = _col_exists(conn, "comments", "content")
            has_comentario= _col_exists(conn, "comments", "comentario")
            if has_comentario and not has_content:
                cur.execute('ALTER TABLE comments RENAME COLUMN "comentario" TO content;')

            # 2) si falta 'created_at' -> añadir con default
            if not _col_exists(conn, "comments", "created_at"):
                cur.execute('ALTER TABLE comments ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT NOW();')

            # 3) si falta 'item_identificador' -> añadir (muy improbable)
            if not _col_exists(conn, "comments", "item_identificador"):
                cur.execute('ALTER TABLE comments ADD COLUMN item_identificador TEXT NOT NULL;')

            # 4) índice para lecturas por item
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE c.relname = 'idx_comments_item' AND n.nspname = 'public'
                    ) THEN
                        CREATE INDEX idx_comments_item ON comments(item_identificador);
                    END IF;
                END$$;
            """)
        conn.commit()

_ensure_table()

def _safe_int(v, d, mi=1, ma=100):
    try:
        n = int(v)
        if n < mi: n = mi
        if n > ma: n = ma
        return n
    except Exception:
        return d

def _row_dict(row, cols):
    d = dict(zip(cols, row))
    # alias para el front
    if "content" in d and "text" not in d:
        d["text"] = d["content"]
    if "item_identificador" in d and "identificador" not in d:
        d["identificador"] = d["item_identificador"]
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat()
    return d

# =========================
# GET /api/items/<ident>/comments
# =========================
@bp.route("/items/<ident>/comments", methods=["GET"])
def list_item_comments(ident):
    page  = _safe_int(request.args.get("page", 1), 1, 1, 1_000_000)
    limit = _safe_int(request.args.get("limit", 20), 20, 1, 100)
    offset = (page - 1) * limit

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM comments WHERE item_identificador = %s", (ident,))
                total = cur.fetchone()[0] or 0

                cur.execute("""
                    SELECT id,
                           item_identificador,
                           content,
                           user_id,
                           author,
                           created_at
                    FROM comments
                    WHERE item_identificador = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s OFFSET %s
                """, (ident, limit, offset))
                rows = cur.fetchall()
                cols = [c.name for c in cur.description]
                items = [_row_dict(r, cols) for r in rows]

        pages = ceil(total / limit) if limit else 0
        return jsonify({
            "items": items,
            "page": page if total else 1,
            "pages": pages if total else 0,
            "total": total
        }), 200

    except Exception as e:
        current_app.logger.exception("list_item_comments failed")
        # UX amable: no rompemos el front
        return jsonify({"items": [], "page": 1, "pages": 0, "total": 0}), 200

# =========================
# POST /api/items/<ident>/comments
# =========================
@bp.route("/items/<ident>/comments", methods=["POST"])
def add_item_comment(ident):
    try:
        body = request.get_json(force=True) or {}
        text    = (body.get("text") or body.get("content") or "").strip()
        author  = (body.get("author") or None)
        user_id = (body.get("user_id") or None)

        if not text:
            return jsonify({"detail": "content/text requerido"}), 400

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO comments (item_identificador, content, user_id, author)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, item_identificador, content, user_id, author, created_at
                """, (ident, text, user_id, author))
                row = cur.fetchone()
            conn.commit()

        cols = ["id", "item_identificador", "content", "user_id", "author", "created_at"]
        data = _row_dict(row, cols)
        return jsonify(data), 201

    except Exception as e:
        current_app.logger.exception("add_item_comment failed")
        # En dev te puede interesar ver el error exacto (descomenta si lo necesitas):
        # return jsonify({"detail": "No se pudo crear el comentario", "error": str(e)}), 400
        return jsonify({"detail": "No se pudo crear el comentario"}), 400
