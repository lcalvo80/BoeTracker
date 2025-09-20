# app/routes/comments.py
from flask import Blueprint, jsonify, request, current_app
from app.services.postgres import get_db
from math import ceil
from datetime import datetime
import os

# Prefijo unificado: /api/items
bp = Blueprint("comments", __name__, url_prefix="/api/items")

# ---------- helpers ----------
def _col_exists(conn, table, col) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s AND column_name=%s
            LIMIT 1
        """, (table, col))
        return cur.fetchone() is not None

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
    # normalización para front
    if "content" in d and "text" not in d and d.get("content") is not None:
        d["text"] = d["content"]
    if "item_identificador" in d and "identificador" not in d:
        d["identificador"] = d["item_identificador"]
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat()
    return d

# ---------- bootstrap suave y seguro ----------
def _ensure_table():
    """
    Crea la tabla mínima si existe DATABASE_URL y no estamos en TESTING.
    Nunca rompe en import-time (tests/entornos sin BD).
    """
    if not os.getenv("DATABASE_URL"):
        return

    try:
        if current_app and current_app.config.get("TESTING"):
            return
    except Exception:
        pass

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS comments (
                        id SERIAL PRIMARY KEY,
                        item_identificador TEXT NOT NULL,
                        content TEXT,
                        created_at TIMESTAMP DEFAULT NOW()
                    );
                """)
            conn.commit()
    except Exception:
        try:
            current_app.logger.exception("comments bootstrap failed")
        except Exception:
            pass

_ensure_table()

# =========================
# GET /api/items/<ident>/comments
# =========================
@bp.get("/<ident>/comments")
def list_item_comments(ident):
    page  = _safe_int(request.args.get("page", 1), 1, 1, 1_000_000)
    limit = _safe_int(request.args.get("limit", 20), 20, 1, 100)
    offset = (page - 1) * limit

    try:
        with get_db() as conn:
            has_content    = _col_exists(conn, "comments", "content")
            has_comentario = _col_exists(conn, "comments", "comentario")
            has_author     = _col_exists(conn, "comments", "author")
            has_user_name  = _col_exists(conn, "comments", "user_name")

            text_expr = "COALESCE(content, comentario)" if (has_content and has_comentario) \
                        else ("content" if has_content else ("comentario" if has_comentario else "NULL"))
            author_expr = "COALESCE(author, user_name)" if (has_author and has_user_name) \
                          else ("author" if has_author else ("user_name" if has_user_name else "NULL"))

            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM comments WHERE item_identificador = %s", (ident,))
                total = cur.fetchone()[0] or 0

                cur.execute(f"""
                    SELECT id,
                           item_identificador,
                           {text_expr}   AS content,
                           {author_expr} AS author,
                           created_at
                    FROM comments
                    WHERE item_identificador = %s
                    ORDER BY created_at DESC NULLS LAST, id DESC
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
            "total": total,
            "limit": limit,
        }), 200

    except Exception:
        current_app.logger.exception("list_item_comments failed")
        return jsonify({"items": [], "page": 1, "pages": 0, "total": 0, "limit": limit}), 200

# =========================
# POST /api/items/<ident>/comments
# =========================
@bp.post("/<ident>/comments")
def add_item_comment(ident):
    try:
        body = request.get_json(force=True) or {}
        text_input   = (body.get("text") or body.get("content") or "").strip()
        author_input = (body.get("author") or "").strip() or None

        if not text_input:
            return jsonify({"detail": "content/text requerido"}), 400

        with get_db() as conn:
            has_content    = _col_exists(conn, "comments", "content")
            has_comentario = _col_exists(conn, "comments", "comentario")
            has_author     = _col_exists(conn, "comments", "author")
            has_user_name  = _col_exists(conn, "comments", "user_name")

            if not has_content and not has_comentario:
                with conn.cursor() as cur:
                    cur.execute('ALTER TABLE comments ADD COLUMN content TEXT;')
                conn.commit()
                has_content = True

            text_col   = "content" if has_content else "comentario"
            author_col = "author" if has_author else ("user_name" if has_user_name else None)

            cols = ["item_identificador", text_col]
            args = [ident, text_input]

            if author_col:
                cols.append(author_col)
                args.append(author_input)

            placeholders = ", ".join(["%s"] * len(cols))
            col_list = ", ".join(cols)

            return_text_expr   = f"{text_col} AS content"
            return_author_expr = ("COALESCE(author, user_name) AS author"
                                  if (has_author and has_user_name)
                                  else (f"{author_col} AS author" if author_col else "NULL AS author"))

            with conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO comments ({col_list})
                    VALUES ({placeholders})
                    RETURNING id, item_identificador, {return_text_expr}, {return_author_expr}, created_at
                """, args)
                row = cur.fetchone()
            conn.commit()

        cols = ["id", "item_identificador", "content", "author", "created_at"]
        data = _row_dict(row, cols)
        return jsonify(data), 201

    except Exception:
        current_app.logger.exception("add_item_comment failed")
        return jsonify({"detail": "No se pudo crear el comentario"}), 400
