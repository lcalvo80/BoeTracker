# app/routes/comments.py
from flask import Blueprint, jsonify, request
from app.services.postgres import get_db
from math import ceil
from datetime import datetime

bp = Blueprint("comments", __name__)

def _row_to_dict(row, cols):
    d = dict(zip(cols, row))
    # Normalizamos claves para el front: text en vez de content
    if "content" in d and "text" not in d:
        d["text"] = d["content"]
    if isinstance(d.get("created_at"), datetime):
        d["created_at"] = d["created_at"].isoformat()
    return d

def _safe_int(val, default, min_value=1, max_value=1000):
    try:
        n = int(val)
        if n < min_value: n = min_value
        if n > max_value: n = max_value
        return n
    except Exception:
        return default

# ----------------------------
# Rutas NUEVAS que usa el front
# ----------------------------

@bp.route("/items/<ident>/comments", methods=["GET"])
def list_by_item_nested(ident):
    page = _safe_int(request.args.get("page", 1), 1, 1, 1000000)
    limit = _safe_int(request.args.get("limit", 20), 20, 1, 100)
    offset = (page - 1) * limit

    with get_db() as conn:
        with conn.cursor() as cur:
            # total
            cur.execute("SELECT COUNT(*) FROM comments WHERE item_identificador = %s", (ident,))
            total = cur.fetchone()[0] or 0

            # page data (orden estable)
            cur.execute("""
                SELECT id,
                       item_identificador AS identificador,
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
            items = [_row_to_dict(r, cols) for r in rows]

    pages = ceil(total / limit) if limit > 0 else 0
    # Respuesta que espera el front
    return jsonify({
        "items": items,
        "page": page if total else 1,
        "pages": pages if total else 0,
        "total": total
    }), 200

@bp.route("/items/<ident>/comments", methods=["POST"])
def add_nested(ident):
    body = request.get_json(force=True) or {}
    # El front manda { author, text }; admitimos también { content } por compatibilidad
    author = (body.get("author") or None)
    text = (body.get("text") or body.get("content") or "").strip()
    user_id = (body.get("user_id") or None)

    if not text:
        return jsonify({"detail": "content/text requerido"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO comments (item_identificador, content, user_id, author)
                VALUES (%s, %s, %s, %s)
                RETURNING id, item_identificador AS identificador, content, user_id, author, created_at
            """, (ident, text, user_id, author))
            row = cur.fetchone()
        conn.commit()

    cols = [c.name for c in cur.description]
    data = _row_to_dict(row, cols)
    # Añadimos alias text para el front
    data["text"] = data.get("content", "")
    return jsonify(data), 201

# ---------------------------------
# Rutas ORIGINALES (compatibilidad)
# ---------------------------------

@bp.route("/comments", methods=["GET"])
def list_by_item_queryparam():
    ident = request.args.get("identificador")
    if not ident:
        # Alineamos respuesta con el front aunque no la use
        return jsonify({"items": [], "page": 1, "pages": 0, "total": 0}), 200

    # opcionalmente soportar paginación aquí también
    page = _safe_int(request.args.get("page", 1), 1, 1, 1000000)
    limit = _safe_int(request.args.get("limit", 100), 100, 1, 100)
    offset = (page - 1) * limit

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM comments WHERE item_identificador = %s", (ident,))
            total = cur.fetchone()[0] or 0

            cur.execute("""
                SELECT id,
                       item_identificador AS identificador,
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
            items = [_row_to_dict(r, cols) for r in rows]

    pages = ceil(total / limit) if limit > 0 else 0
    return jsonify({"items": items, "page": page, "pages": pages, "total": total}), 200


@bp.route("/comments", methods=["POST"])
def add_queryparam():
    body = request.get_json(force=True) or {}
    ident = (body.get("identificador") or "").strip()
    author = (body.get("author") or None)
    text = (body.get("text") or body.get("content") or "").strip()
    user_id = (body.get("user_id") or None)

    if not ident or not text:
        return jsonify({"detail": "identificador y content/text requeridos"}), 400

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO comments (item_identificador, content, user_id, author)
                VALUES (%s, %s, %s, %s)
                RETURNING id, item_identificador AS identificador, content, user_id, author, created_at
            """, (ident, text, user_id, author))
            row = cur.fetchone()
        conn.commit()

    cols = [c.name for c in cur.description]
    data = _row_to_dict(row, cols)
    data["text"] = data.get("content", "")
    return jsonify(data), 201
