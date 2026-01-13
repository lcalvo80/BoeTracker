# app/blueprints/comments.py
from __future__ import annotations

from typing import Any, Dict, Tuple, Optional

from flask import Blueprint, jsonify, request, current_app, g

from app.auth import require_auth, require_active_subscription
from app.services import comments_svc

bp = Blueprint("comments", __name__)


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


def _extract_text(body: Dict[str, Any]) -> str:
    for k in ("content", "comment", "text"):
        v = body.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _author_from_g() -> Optional[str]:
    """
    Autor confiable: viene del token verificado por require_auth.
    Evitamos aceptar 'author' del body (spoofing).
    """
    name = getattr(g, "name", None)
    email = getattr(g, "email", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


@bp.before_request
def _allow_options():
    if request.method == "OPTIONS":
        return ("", 204)


# GET /api/items/:ident/comments
@bp.get("/<ident>/comments")
@require_auth
@require_active_subscription
def list_comments(ident: str):
    ident = (ident or "").strip()
    if not ident:
        return jsonify({"ok": False, "error": "ident is required"}), 400

    page, limit = _pagination()

    try:
        data = comments_svc.list_by_item_paginated(ident, page=page, limit=limit)
        # Normaliza forma mínima (por si el svc cambia)
        if isinstance(data, dict):
            data.setdefault("items", [])
            data.setdefault("total", 0)
            data.setdefault("page", page)
            data.setdefault("limit", limit)
            if "pages" not in data:
                total = int(data.get("total") or 0)
                data["pages"] = (total + limit - 1) // limit if limit else 0
        return jsonify(data), 200

    except Exception:
        current_app.logger.exception("list_comments failed")
        # No filtramos el error al cliente; devolvemos respuesta estable
        return jsonify({"items": [], "total": 0, "page": page, "pages": 0, "limit": limit}), 200


# POST /api/items/:ident/comments
@bp.post("/<ident>/comments")
@require_auth
@require_active_subscription
def add_comment(ident: str):
    ident = (ident or "").strip()
    if not ident:
        return jsonify({"ok": False, "error": "ident is required"}), 400

    body = request.get_json(silent=True) or {}
    content = _extract_text(body)
    if not content:
        return jsonify({"ok": False, "error": "comment text is required (content/comment/text)"}), 400

    # ✅ Seguridad: user_id y author SIEMPRE desde el token verificado
    user_id = getattr(g, "user_id", None)
    if not user_id:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    author = _author_from_g()

    try:
        rec = comments_svc.create(ident, content=content, author=author, user_id=user_id)
        return jsonify(rec), 201

    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    except Exception:
        current_app.logger.exception("add_comment failed")
        return jsonify({"ok": False, "error": "failed_to_insert_comment"}), 500
