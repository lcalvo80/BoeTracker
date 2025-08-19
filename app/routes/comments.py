# app/routes/comments.py
from flask import Blueprint, jsonify, request
from app.services.postgres import get_db
from datetime import datetime

bp = Blueprint("comments", __name__)

@bp.route("", methods=["GET"])
def list_by_item():
    ident = request.args.get("identificador")
    if not ident:
        return jsonify([]), 200
    sql = """
      SELECT id, item_identificador, content, user_id, author, created_at
      FROM comments
      WHERE item_identificador = %s
      ORDER BY created_at DESC
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ident,))
            rows = cur.fetchall()
            cols = [c.name for c in cur.description]
            data = [dict(zip(cols, r)) for r in rows]
    # ISO
    for r in data:
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
    return jsonify(data), 200

@bp.route("", methods=["POST"])
def add():
    body = request.get_json(force=True) or {}
    ident = body.get("identificador")
    content = (body.get("content") or "").strip()
    user_id = body.get("user_id")
    author  = body.get("author")
    if not ident or not content:
        return jsonify({"detail": "identificador y content requeridos"}), 400
    sql = """
      INSERT INTO comments (item_identificador, content, user_id, author)
      VALUES (%s, %s, %s, %s)
      RETURNING id, item_identificador, content, user_id, author, created_at
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (ident, content, user_id, author))
            row = cur.fetchone()
        conn.commit()
    cols = [c.name for c in cur.description]
    data = dict(zip(cols, row))
    if isinstance(data.get("created_at"), datetime):
        data["created_at"] = data["created_at"].isoformat()
    return jsonify(data), 201
