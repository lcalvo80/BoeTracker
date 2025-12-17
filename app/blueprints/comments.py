# app/blueprints/comments.py
from __future__ import annotations
from typing import Any, Dict, Tuple
from flask import Blueprint, jsonify, request, current_app

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

def _clean_author(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None

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
        return jsonify(detail="ident is required"), 400
    page, limit = _pagination()
    try:
        data = comments_svc.list_by_item_paginated(ident, page=page, limit=limit)
        return jsonify(data), 200
    except Exception:
        current_app.logger.exception("list_comments failed")
        return jsonify({"items": [], "total": 0, "page": 1, "pages": 0, "limit": limit}), 200

# POST /api/items/:ident/comments
@bp.post("/<ident>/comments")
@require_auth
@require_active_subscription
def add_comment(ident: str):
    ident = (ident or "").strip()
    if not ident:
        return jsonify(detail="ident is required"), 400
    body = request.get_json(silent=True) or {}
    content = _extract_text(body)
    author  = _clean_author(body.get("author") or body.get("user_name"))
    user_id = body.get("user_id")

    if not content:
        return jsonify(detail="comment text is required (content/comment/text)"), 400

    try:
        rec = comments_svc.create(ident, content=content, author=author, user_id=user_id)
        return jsonify(rec), 201
    except ValueError as e:
        return jsonify(detail=str(e)), 400
    except Exception:
        current_app.logger.exception("add_comment failed")
        return jsonify(detail="failed to insert comment"), 500
